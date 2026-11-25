"""Preview and explicitly confirm property enquiries."""

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database.connection import get_engine
from backend.database.lead_models import LeadRecord
from backend.database.lead_operations_models import LeadRequestRecord, OutboxEventRecord
from backend.database.models import PropertyRecord
from backend.schemas.lead import (
    LeadConfirmRequest,
    LeadConfirmResponse,
    LeadDraft,
    LeadPrepareResponse,
    PreparedLead,
)
from backend.services.repository_factory import create_property_repository


DEMO_CONTACT_PURPOSE = (
    "Use your provided contact details only to follow up on this property enquiry "
    "(synthetic local demo; no external messages will be sent)."
)


def prepare_lead(request: LeadDraft) -> LeadPrepareResponse:
    """Validate and display a potential lead; no DB insert or side effects."""
    repository = create_property_repository()
    property_record = repository.get_by_id(request.property_id)
    if property_record is None:
        raise HTTPException(status_code=404, detail="Property not found")

    return LeadPrepareResponse(
        draft=PreparedLead(
            property_id=request.property_id,
            property_name=property_record.name,
            customer_name=request.customer_name,
            phone=request.phone,
            message=request.message,
        ),
        contact_purpose=DEMO_CONTACT_PURPOSE,
    )


def confirmation_request_hash(request: LeadConfirmRequest) -> str:
    data = request.model_dump(mode="json", exclude={"idempotency_key"})
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _confirmation_response(lead: LeadRecord) -> LeadConfirmResponse:
    return LeadConfirmResponse(
        lead_id=lead.lead_id,
        property_id=lead.property_id,
        status=lead.status,
    )


def _replay(session: Session, previous: LeadRequestRecord, request_hash: str) -> LeadConfirmResponse:
    if previous.request_hash != request_hash:
        raise HTTPException(status_code=409, detail="Idempotency key already used with different details")
    lead = session.get(LeadRecord, previous.lead_id)
    return _confirmation_response(lead)


def _is_idempotency_conflict(error: IntegrityError) -> bool:
    original = error.orig
    diagnostics = getattr(original, "diag", None)
    return (
        getattr(original, "sqlstate", None) == "23505"
        and getattr(diagnostics, "table_name", None) == "lead_requests"
        and getattr(diagnostics, "constraint_name", None) == "lead_requests_pkey"
    )


def _persist_confirmation(engine, request: LeadConfirmRequest) -> LeadConfirmResponse:
    key = str(request.idempotency_key)
    request_hash = confirmation_request_hash(request)
    try:
        with Session(engine) as session, session.begin():
            previous = session.get(LeadRequestRecord, key)
            if previous is not None:
                return _replay(session, previous, request_hash)

            # Confirm against the same PostgreSQL data that the Lead foreign key references.
            if session.get(PropertyRecord, request.property_id) is None:
                raise HTTPException(status_code=404, detail="Property not found")

            lead = LeadRecord(
                lead_id=str(uuid4()),
                property_id=request.property_id,
                customer_name=request.customer_name,
                phone=request.phone,
                message=request.message,
                contact_purpose=DEMO_CONTACT_PURPOSE,
                consented_at=datetime.now(timezone.utc),
            )
            session.add(lead)
            session.flush()
            session.add(LeadRequestRecord(
                idempotency_key=key, request_hash=request_hash, lead_id=lead.lead_id,
            ))
            session.flush()
            session.add(OutboxEventRecord(
                event_id=str(uuid4()),
                event_type="LeadCreated",
                aggregate_id=lead.lead_id,
                payload={"lead_id": lead.lead_id, "property_id": lead.property_id},
            ))
            return _confirmation_response(lead)
    except IntegrityError as error:
        if not _is_idempotency_conflict(error):
            raise
        # The losing transaction rolled back on exit. Read the winner in a fresh session.
        with Session(engine) as session, session.begin():
            previous = session.get(LeadRequestRecord, key)
            if previous is None:
                raise
            return _replay(session, previous, request_hash)


def confirm_lead(request: LeadConfirmRequest) -> LeadConfirmResponse:
    try:
        engine = get_engine()
    except (RuntimeError, SQLAlchemyError):
        raise HTTPException(status_code=503, detail="Lead database is unavailable") from None

    try:
        return _persist_confirmation(engine, request)
    except OperationalError as error:
        if getattr(error.orig, "sqlstate", None) not in ("40001", "40P01"):
            raise HTTPException(status_code=503, detail="Lead database is unavailable") from None
        raise HTTPException(status_code=503, detail="Lead transaction could not complete; retry with the same key") from None
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail="Could not confirm lead") from None
    finally:
        engine.dispose()
