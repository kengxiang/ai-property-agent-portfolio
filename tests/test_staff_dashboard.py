import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.database.admin_models import AdminAccount
from backend.database.lead_operations_models import OutboxEventRecord
from backend.main import app
from backend.routers.admin import admin_engine
from backend.services.admin_ingestion import hash_password
from test_follow_up_postgres import PROCESS, confirm_event

pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="requires isolated PostgreSQL")
PASSWORD = "synthetic staff test password"


def test_staff_access_and_atomic_task_transitions(database, monkeypatch):
    event = confirm_event(database, monkeypatch)
    with database.begin() as connection:
        assert connection.scalar(PROCESS, event) == 200
        connection.execute(OutboxEventRecord.__table__.update().where(
            OutboxEventRecord.event_id == event["event_id"]
        ).values(delivery_status="DELIVERED", last_error="untrusted historical error with private content"))
    with Session(database) as session, session.begin():
        for role in ("admin", "staff", "customer"):
            session.add(AdminAccount(username=f"test-{role}", role=role, password_hash=hash_password(PASSWORD)))

    app.dependency_overrides[admin_engine] = lambda: database
    try:
        with TestClient(app) as client:
            def login(role, path="/staff/login"):
                return client.post(path, json={"username": f"test-{role}", "password": PASSWORD})

            assert client.get("/staff/leads").status_code == 401
            assert login("customer").status_code == 403
            assert login("staff", "/admin/login").status_code == 403
            assert login("admin").status_code == 200
            token = login("staff").json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            assert client.post("/admin/properties", headers=headers, json={}).status_code == 403
            assert client.get("/staff/leads?limit=101", headers=headers).status_code == 422
            lead, = client.get("/staff/leads", headers=headers).json()
            assert lead["lead_id"] == event["lead_id"]
            assert lead["follow_up_status"] == "PENDING"
            assert client.get(f"/staff/leads/{lead['lead_id']}", headers=headers).status_code == 200
            entry, = client.get("/staff/events", headers=headers).json()
            assert entry["error_summary"] == "Delivery error"
            assert "payload" not in entry and "last_error" not in entry
            path = "/staff/tasks/" + entry["task_id"]

            def change(target):
                return client.patch(path, headers=headers, json={"status": target})

            assert client.patch(path, json={"status": "IN_PROGRESS"}).status_code == 401
            assert change("COMPLETED").status_code == 409
            assert change("PENDING").status_code == 422
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(change, ("IN_PROGRESS", "IN_PROGRESS")))
            assert sorted(r.status_code for r in responses) == [200, 409]
            assert client.get(f"/staff/leads/{lead['lead_id']}", headers=headers).json()["follow_up_status"] == "IN_PROGRESS"
            result = change("COMPLETED")
            assert result.status_code == 200
            assert result.json()["updated_by"] == "test-staff"
            assert result.json()["updated_at"]
            assert change("IN_PROGRESS").status_code == 409
            task, = client.get("/staff/tasks", headers=headers).json()
            assert task["status"] == "COMPLETED"
            assert client.get(f"/staff/events/{entry['event_id']}", headers=headers).json()["delivery_status"] == "DELIVERED"
            assert client.get(f"/staff/leads/{lead['lead_id']}", headers=headers).json()["follow_up_status"] == "COMPLETED"
            assert client.get("/staff/overview", headers=headers).json() == {
                "total_leads": 1, "tasks": {"PENDING": 0, "IN_PROGRESS": 0, "COMPLETED": 1},
                "delivered": 1, "failed": 0, "terminal_events": 1, "requires_attention": 0,
                "overall_event_delivery_rate": 1.0,
            }
            assert client.post("/staff/logout", headers=headers).status_code == 204
            assert client.get("/staff/tasks", headers=headers).status_code == 401
    finally:
        app.dependency_overrides.pop(admin_engine)
