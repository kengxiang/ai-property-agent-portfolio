import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.database.admin_models import AdminAccount
from backend.database.lead_models import LeadRecord
from backend.database.lead_operations_models import FollowUpTaskRecord, OutboxEventRecord
from backend.main import app
from backend.routers.admin import admin_engine
from backend.routers.staff import overview_query
from backend.services.admin_ingestion import hash_password

pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="requires isolated PostgreSQL")
PASSWORD = "synthetic staff test password"


def test_derived_statuses_monitoring_and_kpis(database):
    assert overview_query(database)["overall_event_delivery_rate"] is None
    now = datetime.now(timezone.utc)
    with Session(database) as session, session.begin():
        session.add(AdminAccount(username="test-monitor", role="staff", password_hash=hash_password(PASSWORD)))
    scenarios = [
        ("DELIVERED", "PENDING", "PENDING", None, False),
        ("DELIVERED", "IN_PROGRESS", "IN_PROGRESS", None, False),
        ("FAILED", "COMPLETED", "COMPLETED", "HTTP_403", False),
        ("FAILED", None, "AUTOMATION_FAILED", "HTTP_403", True),
        ("FAILED", None, "AUTOMATION_FAILED", "HTTP_404", True),
        ("PENDING", None, "NO_TASK", None, False),
        ("PROCESSING", None, "NO_TASK", None, False),
        ("RETRY", None, "NO_TASK", "TIMEOUT", False),
        (None, None, "NO_TASK", None, False),
        ("DELIVERED", None, "NO_TASK", None, True),
        ("DELIVERED", "UNKNOWN", "NO_TASK", None, True),
        ("FAILED", "MISMATCH", "AUTOMATION_FAILED", "sensitive content", True),
    ]
    cases = []
    with Session(database) as session, session.begin():
        for delivery, task_status, *_ in scenarios:
            lead = LeadRecord(lead_id=str(uuid4()), property_id="P001", customer_name="Synthetic",
                              phone="0000000000", contact_purpose="Synthetic", consented_at=now)
            session.add(lead)
            cases.append((lead.lead_id, str(uuid4())))
        session.flush()
        for index, (delivery, task_status, _, error, _) in enumerate(scenarios):
            lead_id, event_id = cases[index]
            if delivery:
                session.add(OutboxEventRecord(event_id=event_id, event_type="LeadCreated", aggregate_id=lead_id,
                                              payload={"private": "must not be exposed"}, delivery_status=delivery,
                                              attempts=5 if error else 1, last_error=error))
            # A different event type can coexist. Its failure does not affect the LeadCreated status.
            if not delivery or index == 0:
                session.add(OutboxEventRecord(event_id=str(uuid4()), event_type="Other", aggregate_id=lead_id,
                                              payload={}, delivery_status="FAILED"))
        session.flush()
        for index, (_, status, *_) in enumerate(scenarios):
            if status:
                lead_id, event_id = cases[index]
                session.add(FollowUpTaskRecord(task_id=str(uuid4()), event_id=event_id,
                                              lead_id=cases[0][0] if status == "MISMATCH" else lead_id,
                                              property_id="P001",
                                              status="PENDING" if status == "MISMATCH" else status))

    app.dependency_overrides[admin_engine] = lambda: database
    try:
        with TestClient(app) as client:
            login = client.post("/staff/login", json={"username": "test-monitor", "password": PASSWORD})
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            assert client.get("/staff/events").status_code == 401
            assert client.get(f"/staff/events/{cases[0][1]}").status_code == 401
            assert client.get("/staff/events?limit=101", headers=headers).status_code == 422
            assert len(client.get("/staff/events?limit=2", headers=headers).json()) == 2
            leads = {row["lead_id"]: row for row in client.get("/staff/leads", headers=headers).json()}
            events = {row["event_id"]: row for row in client.get("/staff/events", headers=headers).json()}
            for (lead_id, event_id), (delivery, task_status, expected, error, attention) in zip(cases, scenarios):
                lead = leads[lead_id]
                assert lead["follow_up_status"] == expected
                if not delivery:
                    assert lead["event_id"] is None
                    continue
                event = events[event_id]
                assert event["attempts"] == (5 if error else 1)
                assert event["requires_attention"] is attention
                assert event["property_id"] == "P001"
                assert event["task_id"] == lead["task_id"]
                assert event["last_failure_at"] is None
                assert "payload" not in event
                assert client.get(f"/staff/events/{event_id}", headers=headers).json() == event
                if error:
                    assert event["error_summary"] == {
                        "HTTP_403": "Webhook returned HTTP 403. The request was rejected.",
                        "HTTP_404": "Webhook returned HTTP 404. The endpoint or event was not found.",
                        "TIMEOUT": "Webhook timed out",
                        "sensitive content": "Delivery error",
                    }[error]
            metrics = client.get("/staff/overview", headers=headers).json()
            assert metrics == {
                "total_leads": 12, "tasks": {"PENDING": 2, "IN_PROGRESS": 1, "COMPLETED": 1},
                "delivered": 4, "failed": 4, "terminal_events": 8, "requires_attention": 5,
                "overall_event_delivery_rate": 0.5,
            }
    finally:
        app.dependency_overrides.pop(admin_engine)
