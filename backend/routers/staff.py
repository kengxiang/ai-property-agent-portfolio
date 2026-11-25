"""Authenticated operations for lead and follow-up management."""

import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from backend.database.lead_models import LeadRecord
from backend.database.lead_operations_models import FollowUpTaskRecord, OutboxEventRecord
from backend.routers.admin import Credentials, Engine, bearer, database_call
from backend.services import admin_ingestion as auth

router = APIRouter(prefix="/staff", tags=["Staff"])
ROLES = ("admin", "staff")
TASK_STATES = ("PENDING", "IN_PROGRESS", "COMPLETED")
TASK_FIELDS = ("task_id", "event_id", "lead_id", "property_id", "status", "created_at", "updated_at", "updated_by")
LEAD_FIELDS = ("lead_id", "customer_name", "phone", "property_id", "created_at")
EVENT_FIELDS = ("event_id", "aggregate_id", "delivery_status", "attempts", "created_at", "delivered_at")


def identity(engine: Engine, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    token = credentials.credentials if credentials else None
    return database_call(auth.authorize, engine, token, ROLES), token


Identity = Annotated[tuple[str, str], Depends(identity)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100000)]


@router.post("/login")
def login(request: Credentials, response: Response, engine: Engine):
    response.headers["Cache-Control"] = "no-store"
    return database_call(auth.login, engine, request.username, request.password.get_secret_value(), ROLES)


@router.post("/logout", status_code=204)
def logout(engine: Engine, user: Identity):
    database_call(auth.logout, engine, user[1], user[0])
    return Response(status_code=204)


def fields(row, names):
    return {name: getattr(row, name) for name in names}


def valid_task():
    return and_(
        LeadRecord.lead_id.is_not(None),
        FollowUpTaskRecord.task_id.is_not(None),
        FollowUpTaskRecord.lead_id == LeadRecord.lead_id,
        FollowUpTaskRecord.property_id == LeadRecord.property_id,
        FollowUpTaskRecord.status.in_(TASK_STATES),
    )


def event_query(*columns):
    return (select(*columns).select_from(OutboxEventRecord)
            .join(LeadRecord, LeadRecord.lead_id == OutboxEventRecord.aggregate_id)
            .outerjoin(FollowUpTaskRecord, FollowUpTaskRecord.event_id == OutboxEventRecord.event_id)
            .where(OutboxEventRecord.event_type == "LeadCreated"))


def overview_query(engine):
    with Session(engine) as session:
        total = session.scalar(select(func.count()).select_from(LeadRecord))
        states = dict(session.execute(select(FollowUpTaskRecord.status, func.count())
                     .group_by(FollowUpTaskRecord.status)).all())
        counts = {
            state: (count, without_task)
            for state, count, without_task in session.execute(
                event_query(OutboxEventRecord.delivery_status, func.count(), func.count().filter(~valid_task()))
                .group_by(OutboxEventRecord.delivery_status)
            )
        }
        delivered, missing_delivered = counts.get("DELIVERED", (0, 0))
        failed, missing_failed = counts.get("FAILED", (0, 0))
        return {
            "total_leads": total,
            "tasks": {state: states.get(state, 0) for state in TASK_STATES},
            "delivered": delivered, "failed": failed, "terminal_events": delivered + failed,
            "requires_attention": missing_delivered + missing_failed,
            "overall_event_delivery_rate": delivered / (delivered + failed) if delivered + failed else None,
        }


@router.get("/overview")
def overview(engine: Engine, user: Identity, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return database_call(overview_query, engine)


def lead_query():
    # The unique (event_type, aggregate_id) and task event_id keys bound the join to one row per lead.
    return (select(LeadRecord, OutboxEventRecord, FollowUpTaskRecord, valid_task()).select_from(LeadRecord)
            .outerjoin(OutboxEventRecord, and_(OutboxEventRecord.aggregate_id == LeadRecord.lead_id,
                                              OutboxEventRecord.event_type == "LeadCreated"))
            .outerjoin(FollowUpTaskRecord, FollowUpTaskRecord.event_id == OutboxEventRecord.event_id))


def follow_up(event, task, task_valid):
    delivery = event.delivery_status if event else None
    if task_valid:
        status, label = task.status, None
    else:
        status = "AUTOMATION_FAILED" if delivery == "FAILED" else "NO_TASK"
        label = {
            "PENDING": "Waiting for delivery", "PROCESSING": "Processing",
            "RETRY": "Retry scheduled", "DELIVERED": "Delivered event has no valid task",
            "FAILED": "Delivery stopped before a valid task was confirmed",
        }.get(delivery, "No task found")
        if task:
            label += ". Task relationship or state is inconsistent"
    return {
        "follow_up_status": status, "follow_up_note": label,
        "event_id": event.event_id if event else None,
        "task_id": task.task_id if task_valid else None,
    }


def lead_page(engine, limit, offset):
    with Session(engine) as session:
        query = lead_query().order_by(LeadRecord.created_at.desc(), LeadRecord.lead_id).limit(limit).offset(offset)
        return [{**fields(lead, LEAD_FIELDS), **follow_up(event, task, valid)}
                for lead, event, task, valid in session.execute(query)]


@router.get("/leads")
def leads(engine: Engine, user: Identity, response: Response, limit: Limit = 25, offset: Offset = 0):
    response.headers["Cache-Control"] = "no-store"
    return database_call(lead_page, engine, limit, offset)


def lead_details(engine, lead_id):
    with Session(engine) as session:
        row = session.execute(lead_query().where(LeadRecord.lead_id == lead_id)).one_or_none()
        if row is None:
            raise HTTPException(404, "Lead not found")
        lead, event, task, valid = row
        return {**fields(lead, (*LEAD_FIELDS, "message", "contact_purpose", "status")),
                **follow_up(event, task, valid)}


@router.get("/leads/{lead_id}")
def lead(lead_id: str, engine: Engine, user: Identity, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return database_call(lead_details, engine, lead_id)


def task_page(engine, limit, offset):
    with Session(engine) as session:
        return [fields(task, TASK_FIELDS) for task in session.scalars(
            select(FollowUpTaskRecord).order_by(FollowUpTaskRecord.created_at.desc(), FollowUpTaskRecord.task_id)
            .limit(limit).offset(offset)
        )]


@router.get("/tasks")
def tasks(engine: Engine, user: Identity, limit: Limit = 25, offset: Offset = 0):
    return database_call(task_page, engine, limit, offset)


def task_details(engine, task_id):
    with Session(engine) as session:
        task = session.get(FollowUpTaskRecord, task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        return fields(task, TASK_FIELDS)


@router.get("/tasks/{task_id}")
def task(task_id: str, engine: Engine, user: Identity):
    return database_call(task_details, engine, task_id)


class Transition(BaseModel):
    status: Literal["IN_PROGRESS", "COMPLETED"]


def transition(engine, task_id, target, actor):
    previous = "PENDING" if target == "IN_PROGRESS" else "IN_PROGRESS"
    with Session(engine) as session, session.begin():
        task = session.scalar(update(FollowUpTaskRecord)
                              .where(FollowUpTaskRecord.task_id == task_id, FollowUpTaskRecord.status == previous)
                              .values(status=target, updated_at=func.now(), updated_by=actor)
                              .returning(FollowUpTaskRecord))
        if task is None:
            if session.get(FollowUpTaskRecord, task_id) is None:
                raise HTTPException(404, "Task not found")
            raise HTTPException(409, "Task status changed or transition is invalid")
        return fields(task, TASK_FIELDS)


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, request: Transition, engine: Engine, user: Identity):
    return database_call(transition, engine, task_id, request.status, user[0])


def error_summary(code):
    labels = {
        "HTTP_403": "Webhook returned HTTP 403. The request was rejected.",
        "HTTP_404": "Webhook returned HTTP 404. The endpoint or event was not found.",
        "TIMEOUT": "Webhook timed out", "CONNECTION_ERROR": "Webhook connection failed",
        "INVALID_ACK": "Acknowledgement rejected", "INVALID_EVENT": "Event validation failed",
        "ATTEMPT_LIMIT_REACHED": "Attempt limit reached",
    }
    if not code:
        return None
    if code in labels:
        return labels[code]
    if re.fullmatch(r"HTTP_[1-5][0-9]{2}", code):
        return "Webhook returned HTTP " + code[5:]
    return "Delivery error"


def event_view(event, lead, task, task_valid):
    missing = not task_valid
    reason = None
    if missing and event.delivery_status == "FAILED":
        reason = "Terminal delivery failure without a valid follow-up task"
    elif missing and event.delivery_status == "DELIVERED":
        reason = "Delivered event has no valid follow-up task"
    if missing and task:
        reason = (reason + ". " if reason else "") + "Task relationship or state is inconsistent"
    return {
        **fields(event, EVENT_FIELDS),
        "property_id": lead.property_id,
        "next_attempt_at": event.next_attempt_at,
        "last_failure_at": None,  # The outbox does not store attempt timestamps.
        "task_id": task.task_id if task_valid else None,
        "task_status": task.status if task_valid else None,
        "requires_attention": missing and event.delivery_status in {"FAILED", "DELIVERED"},
        "attention_reason": reason,
        "error_summary": error_summary(event.last_error),
    }


def event_page(engine, limit, offset):
    with Session(engine) as session:
        query = (event_query(OutboxEventRecord, LeadRecord, FollowUpTaskRecord, valid_task())
                 .order_by(OutboxEventRecord.created_at.desc(), OutboxEventRecord.event_id)
                 .limit(limit).offset(offset))
        return [event_view(*row) for row in session.execute(query)]


def event_details(engine, event_id):
    with Session(engine) as session:
        query = event_query(OutboxEventRecord, LeadRecord, FollowUpTaskRecord, valid_task())
        row = session.execute(query.where(OutboxEventRecord.event_id == event_id)).one_or_none()
        if row is None:
            raise HTTPException(404, "Event not found")
        return event_view(*row)


@router.get("/events")
def events(engine: Engine, user: Identity, limit: Limit = 25, offset: Offset = 0):
    return database_call(event_page, engine, limit, offset)

@router.get("/events/{event_id}")
def event(event_id: str, engine: Engine, user: Identity):
    return database_call(event_details, engine, event_id)
