"""Real PostgreSQL tests for the SQL boundary called by the n8n workflow."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from backend.database.lead_operations_models import FollowUpTaskRecord, OutboxEventRecord
from backend.main import app
from test_leads_confirm_postgres import payload


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="requires a dedicated PostgreSQL test database",
)
PROCESS = text("SELECT public.process_lead_follow_up(:event_id, :event_type, :lead_id, :property_id)")


def confirm_event(database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database.url.render_as_string(hide_password=False))
    with TestClient(app) as client:
        response = client.post("/leads/confirm", json=payload())
    assert response.status_code == 200
    with database.connect() as connection:
        row = connection.execute(
            select(OutboxEventRecord.event_id, OutboxEventRecord.event_type, OutboxEventRecord.payload)
            .where(OutboxEventRecord.aggregate_id == response.json()["lead_id"])
        ).one()
    return {"event_id": row.event_id, "event_type": row.event_type, **row.payload}


def tasks(database, event_id):
    with database.connect() as connection:
        return connection.execute(
            select(FollowUpTaskRecord.__table__).filter_by(event_id=event_id)
        ).mappings().all()


def test_constraints_and_replay(database, monkeypatch):
    event = confirm_event(database, monkeypatch)
    with database.begin() as connection:
        assert connection.scalar(PROCESS, event) == 200
    original, = tasks(database, event["event_id"])
    assert original["lead_id"] == event["lead_id"]
    assert original["property_id"] == "P001"
    assert (original["status"], original["task_type"]) == ("PENDING", "FOLLOW_UP")
    assert original["created_at"].utcoffset() is not None

    with database.begin() as connection:
        assert connection.scalar(PROCESS, event) == 200
        assert connection.scalar(PROCESS, {**event, "property_id": "P999"}) == 409
        assert connection.scalar(PROCESS, {**event, "lead_id": str(uuid4())}) == 409
        assert connection.scalar(PROCESS, {**event, "event_id": str(uuid4())}) == 404
        assert connection.scalar(PROCESS, {**event, "event_type": "Other"}) == 400
        assert connection.scalar(PROCESS, {**event, "property_id": ""}) == 400
    assert tasks(database, event["event_id"]) == [original]

    table = FollowUpTaskRecord.__table__
    with pytest.raises(IntegrityError) as duplicate, database.begin() as connection:
        connection.execute(table.insert().values(
            task_id=str(uuid4()), event_id=event["event_id"], lead_id=event["lead_id"], property_id="P001",
        ))
    assert duplicate.value.orig.sqlstate == "23505"

    second = confirm_event(database, monkeypatch)
    with pytest.raises(IntegrityError) as foreign_key, database.begin() as connection:
        connection.execute(table.insert().values(
            task_id=str(uuid4()), event_id=second["event_id"], lead_id=str(uuid4()), property_id="P001",
        ))
    assert foreign_key.value.orig.sqlstate == "23503"
    assert {key["referred_table"] for key in inspect(database).get_foreign_keys(table.name)} == {
        "outbox_events", "leads", "properties",
    }


def test_missing_confirmation_cannot_create_task(database, monkeypatch):
    event = confirm_event(database, monkeypatch)
    with database.begin() as connection:
        connection.execute(text("DELETE FROM lead_requests WHERE lead_id = :lead_id"), event)
        assert connection.scalar(PROCESS, event) == 409
    assert not tasks(database, event["event_id"])


def test_concurrent_delivery(database, monkeypatch):
    event = confirm_event(database, monkeypatch)
    ready = Barrier(2, timeout=5)

    def persist():
        with database.begin() as connection:
            ready.wait()
            return connection.scalar(PROCESS, event)

    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs = [workers.submit(persist) for _ in range(2)]
        assert [job.result() for job in jobs] == [200, 200]
    assert len(tasks(database, event["event_id"])) == 1
