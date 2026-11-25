"""Confirm against a uniquely named PostgreSQL database, separate from development data."""

import os
import socket
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Thread
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from backend.database.lead_models import LeadRecord
from backend.database.lead_operations_models import LeadRequestRecord, OutboxEventRecord
from backend.main import app
from backend.services.lead_service import DEMO_CONTACT_PURPOSE


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires PostgreSQL with permission to create a dedicated test database",
)


@pytest.fixture
def api(database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database.url.render_as_string(hide_password=False))
    monkeypatch.setenv("PROPERTY_BACKEND", "postgres")
    monkeypatch.setattr("backend.services.postgres_property_service.get_engine", lambda: database)
    return database


def payload():
    return {
        "property_id": " p001 ", "customer_name": " Demo Alex ", "phone": "+60 12-345 6789",
        "message": "Demo viewing", "consent": True, "idempotency_key": str(uuid4()),
    }


def counts(engine):
    with Session(engine) as session:
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in (
            LeadRecord, LeadRequestRecord, OutboxEventRecord,
        ))


def increment(before, amount=1):
    return tuple(count + amount for count in before)


def test_confirmation_and_persistent_replay(api):
    data = payload()
    before = counts(api)
    earliest = datetime.now(timezone.utc)
    with TestClient(app) as client:
        first = client.post("/leads/confirm", json=data)
    assert first.status_code == 200
    lead_id = first.json()["lead_id"]
    assert first.json() == {
        "lead_id": lead_id, "property_id": "P001", "status": "NEW", "confirmed": True,
    }
    assert counts(api) == increment(before)

    with Session(api) as session:
        lead = session.get(LeadRecord, lead_id)
        request = session.get(LeadRequestRecord, data["idempotency_key"])
        outbox = session.scalars(select(OutboxEventRecord).filter_by(aggregate_id=lead_id)).one()
        consent_at = lead.consented_at
        assert earliest <= consent_at <= datetime.now(timezone.utc)
        assert consent_at.utcoffset() is not None
        assert (lead.customer_name, lead.phone, lead.message) == ("Demo Alex", "+60123456789", "Demo viewing")
        assert (lead.contact_purpose, lead.status, lead.source) == (DEMO_CONTACT_PURPOSE, "NEW", "STREAMLIT")
        assert request.lead_id == lead_id and len(request.request_hash) == 64
        assert (outbox.event_type, outbox.delivery_status, outbox.attempts) == ("LeadCreated", "PENDING", 0)
        assert outbox.payload == {"lead_id": lead_id, "property_id": "P001"}

    # A different client and fresh service engine read the original committed operation.
    with TestClient(app) as client:
        replay = client.post("/leads/confirm", json={**data, "phone": "+60123456789"})
        conflict = client.post("/leads/confirm", json={**data, "phone": "+60198765432"})
        assert replay.status_code == 200 and replay.json() == first.json()
        assert conflict.status_code == 409
        assert counts(api) == increment(before)
        distinct = client.post("/leads/confirm", json={**data, "idempotency_key": str(uuid4())})
        assert distinct.status_code == 200 and distinct.json()["lead_id"] != lead_id
    assert counts(api) == increment(before, 2)
    with Session(api) as session:
        assert session.get(LeadRecord, lead_id).consented_at == consent_at


@pytest.mark.parametrize("different_payload", [False, True])
def test_concurrent_duplicate_confirms(api, different_payload):
    data = payload()
    other = {**data, "phone": "+60198765432"} if different_payload else data
    before = counts(api)
    barrier = Barrier(2, timeout=10)

    def wait_for_both_transactions(session, flush_context, instances):
        if any(isinstance(row, LeadRecord) for row in session.new):
            barrier.wait()

    def confirm(body):
        with TestClient(app) as client:
            return client.post("/leads/confirm", json=body)

    event.listen(Session, "before_flush", wait_for_both_transactions)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(confirm, (data, other))
    finally:
        event.remove(Session, "before_flush", wait_for_both_transactions)

    expected = [200, 409] if different_payload else [200, 200]
    assert sorted([first.status_code, second.status_code]) == expected
    if not different_payload:
        assert first.json()["lead_id"] == second.json()["lead_id"]
    assert counts(api) == increment(before)


def test_outbox_constraint_failure_rolls_back_every_insert(api):
    data = payload()
    before = counts(api)

    def invalidate_event(session, flush_context, instances):
        for row in session.new:
            if isinstance(row, OutboxEventRecord):
                row.attempts = -1  # PostgreSQL rejects ck_outbox_attempts.

    with TestClient(app) as client:
        event.listen(Session, "before_flush", invalidate_event)
        try:
            failed = client.post("/leads/confirm", json=data)
        finally:
            event.remove(Session, "before_flush", invalidate_event)
        assert failed.status_code == 500
        assert failed.json() == {"detail": "Could not confirm lead"}
        assert counts(api) == before
        retried = client.post("/leads/confirm", json=data)
        assert retried.status_code == 200
    assert counts(api) == increment(before)


def test_prepare_unknown_property_and_missing_consent_leave_counts_unchanged(api):
    data = payload()
    draft = {key: value for key, value in data.items() if key not in {"idempotency_key", "consent"}}
    before = counts(api)
    with TestClient(app) as client:
        assert client.post("/leads/prepare", json=draft).status_code == 200
        assert client.get("/properties/P001").status_code == 200
        assert client.post("/leads/confirm", json={**data, "property_id": "P999"}).status_code == 404
        del data["consent"]
        assert client.post("/leads/confirm", json=data).status_code == 422
    assert counts(api) == before


@contextmanager
def live_server():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config("backend.main:app", log_level="warning"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 10
            while not server.started and thread.is_alive() and monotonic() < deadline:
                sleep(0.02)
            assert server.started
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def test_live_http_and_server_restart(api):
    data = payload()
    before = counts(api)

    def run_server_request():
        with live_server() as base_url, httpx.Client(base_url=base_url, trust_env=False) as client:
            assert client.get("/docs").status_code == 200
            assert "post" in client.get("/openapi.json").json()["paths"]["/leads/confirm"]
            response = client.post("/leads/confirm", json=data)
            assert response.status_code == 200
            return response.json()

    first = run_server_request()
    assert run_server_request() == first
    assert counts(api) == increment(before)
    with Session(api) as session:
        assert session.get(LeadRecord, first["lead_id"]) is not None
        assert session.get(LeadRequestRecord, data["idempotency_key"]).lead_id == first["lead_id"]
        assert session.scalar(
            select(func.count()).select_from(OutboxEventRecord).filter_by(aggregate_id=first["lead_id"])
        ) == 1


def test_streamlit_enquiry_over_live_http(api, monkeypatch):
    before = counts(api)
    original_urlopen = urllib.request.urlopen
    drop_next_confirmation = True

    def lose_first_confirmation_response(request, timeout):
        nonlocal drop_next_confirmation
        response = original_urlopen(request, timeout=timeout)
        if drop_next_confirmation and request.full_url.endswith("/leads/confirm"):
            drop_next_confirmation = False
            with response:
                assert response.status == 200
            raise TimeoutError("Simulated lost response after the transaction committed")
        return response

    monkeypatch.setattr(urllib.request, "urlopen", lose_first_confirmation_response)
    with live_server() as base_url:
        monkeypatch.setenv("PROPERTY_API_BASE_URL", base_url)
        app_file = Path(__file__).resolve().parents[1] / "frontend" / "app.py"
        ui = AppTest.from_file(app_file).run()

        def click(label):
            next(widget for widget in ui.button if widget.label == label).click().run()
            assert not ui.exception

        ui.selectbox(key="lead_property").select("P001")
        ui.text_input(key="lead_name").set_value(" Demo Alex ")
        ui.text_input(key="lead_phone").set_value("+60 12-345 6789")
        ui.text_area(key="lead_message").set_value("Demo viewing")
        click("Review Enquiry")
        assert counts(api) == before
        assert not ui.checkbox(key="lead_consent").value
        draft = ui.session_state.lead_preview["draft"]
        key = ui.session_state.lead_idempotency_key
        ui.checkbox(key="lead_consent").check().run()
        click("Confirm & Submit")
        assert not ui.success
        assert "submission status is unknown" in ui.error[0].value
        assert ui.session_state.lead_idempotency_key == key
        assert counts(api) == increment(before)
        with Session(api) as session:
            committed_lead_id = session.get(LeadRequestRecord, key).lead_id
        click("Confirm & Submit")
        result = ui.session_state.lead_confirmation_result
        lead_id = result["lead_id"]
        assert lead_id == committed_lead_id
        assert any(lead_id in line.value for line in ui.text)
        assert result["confirmed"] is True
        assert counts(api) == increment(before)
        with Session(api) as session:
            assert session.get(LeadRecord, lead_id).phone == "+60123456789"
            assert session.get(LeadRequestRecord, key).lead_id == lead_id
            outbox = session.scalars(select(OutboxEventRecord).filter_by(aggregate_id=lead_id)).one()
            assert outbox.delivery_status == "PENDING"
            assert outbox.payload == {"lead_id": lead_id, "property_id": "P001"}

        retry = {field: draft[field] for field in ("property_id", "customer_name", "phone", "message")}
        retry.update(consent=True, idempotency_key=key)
        with httpx.Client(base_url=base_url, trust_env=False) as client:
            replay = client.post("/leads/confirm", json=retry)
            assert replay.status_code == 200 and replay.json() == result
            conflict = client.post("/leads/confirm", json={**retry, "phone": "+60198765432"})
            assert conflict.status_code == 409
        ui.run()
        assert not ui.exception
        assert all(widget.label != "Confirm & Submit" for widget in ui.button)
        assert counts(api) == increment(before)
