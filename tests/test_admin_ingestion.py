import csv
import io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from agent.tools import get_property_details
from backend.database.admin_models import AdminAccount, AdminAudit, AdminSession
from backend.database.models import PropertyRecord
from backend.main import app
from backend.routers.admin import admin_engine
from backend.services.admin_ingestion import hash_password, token_digest
from backend.services.postgres_property_service import PostgresPropertyRepository


PASSWORD = "test-only admin password!"
CSV = {"Content-Type": "text/csv"}
pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="requires PostgreSQL")


def property_data(property_id):
    return {
        "property_id": property_id, "name": "Synthetic P2-06 listing",
        "location_code": "Kuala Lumpur", "display_location": "Kuala Lumpur",
        "property_type": "condominium", "price_myr": 450000,
        "bedrooms": 2, "bathrooms": 2, "mrt_distance_m": "",
        "availability_status": "AVAILABLE", "description": "Synthetic integration fixture",
    }


def csv_payload(*rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


@pytest.fixture(scope="module")
def accounts(database):
    password_hash = hash_password(PASSWORD)
    with Session(database) as session, session.begin():
        session.add_all([
            AdminAccount(username="test-admin", password_hash=password_hash, role="admin"),
            AdminAccount(username="test-regular", password_hash=password_hash, role="customer"),
        ])
    return database


@pytest.fixture
def api(accounts, monkeypatch):
    repo = PostgresPropertyRepository(engine=accounts)
    monkeypatch.setattr("backend.routers.properties._repository", repo)
    monkeypatch.setattr("agent.tools.repository", repo)
    app.dependency_overrides[admin_engine] = lambda: accounts
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(admin_engine)


def login(api, username="test-admin", password=PASSWORD):
    return api.post("/admin/login", json={"username": username, "password": password})


@pytest.fixture
def headers(api):
    response = login(api)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_authentication_role_expiry_and_revocation(api, accounts):
    path = "/admin/properties"
    body = property_data("NEW-AUTH")
    assert api.post(path, json=body).status_code == 401
    assert api.post(path, json=body, headers={"Authorization": "Bearer invalid"}).status_code == 401
    bad = login(api, password="wrong password value")
    assert bad.status_code == 401 and "wrong password" not in bad.text
    invalid = login(api, password="x" * 257)
    assert invalid.status_code == 422 and "xxxxx" not in invalid.text
    assert login(api, username="test-regular").status_code == 403
    secret = "a" * 43
    with Session(accounts) as session, session.begin():
        session.add_all([
            AdminSession(token_hash=token_digest(secret), username="test-regular",
                         expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
            AdminSession(token_hash=token_digest("b" * 43), username="test-admin",
                         expires_at=datetime.now(timezone.utc) - timedelta(hours=1)),
        ])
    assert api.post(path, json=body, headers={"Authorization": f"Bearer {secret}"}).status_code == 403
    assert api.post(path, json=body, headers={"Authorization": "Bearer " + "b" * 43}).status_code == 401
    token = login(api).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    with Session(accounts) as session:
        assert session.get(AdminAccount, "test-admin").password_hash != PASSWORD
        assert session.get(AdminSession, token) is None
    assert api.post("/admin/logout", headers=headers).status_code == 204
    assert api.post(path, json=body, headers=headers).status_code == 401
    assert api.get("/properties/NEW-AUTH").status_code == 404


def test_create_normalizes_validates_and_never_overwrites(api, headers):
    path = "/admin/properties"
    payload = property_data(" new-one ")
    for invalid in ({**payload, "price_myr": -1}, {**payload, "property_type": "unknown"},
                    {key: value for key, value in payload.items() if key != "name"}):
        assert api.post(path, json=invalid, headers=headers).status_code == 422
    response = api.post(path, json=payload, headers=headers)
    assert response.status_code == 201
    prop = response.json()
    assert (prop["property_id"], prop["location_code"], prop["property_type"]) == ("NEW-ONE", "KUALA_LUMPUR", "CONDO")
    assert api.post(path, json={**payload, "name": "Overwrite?"}, headers=headers).status_code == 409
    assert api.get("/properties/NEW-ONE").json() == prop
    assert get_property_details("NEW-ONE") == prop


def test_concurrent_duplicate_requests(api, headers):
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: api.post("/admin/properties", json=property_data("NEW-RACE"), headers=headers), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]


def test_csv_validation_preview_conflicts_and_repeat(api, headers):
    path = "/admin/properties/csv"
    auth = {**headers, **CSV}
    first, second = property_data("NEW-CSV1"), property_data("NEW-CSV2")
    assert api.post(path, content=b"property_id\n123\n", headers=auth).status_code == 422
    assert api.post(path, content=b"\xff", headers=auth).status_code == 422
    assert api.post(path, content=csv_payload(first, {**first, "property_id": " new-csv1 "}), headers=auth).status_code == 422
    failed = api.post(path, content=csv_payload(first, {**second, "price_myr": -7}), headers=auth)
    assert failed.status_code == 422 and failed.json()["detail"][0]["row"] == 3
    assert api.post(path, content=csv_payload(first) + b'"unterminated', headers=auth).status_code == 422
    assert api.get("/properties/NEW-CSV1").status_code == 404
    assert api.post(path, content=b"x" * (1024**2 + 1), headers=auth).status_code == 413
    raw = csv_payload(first, second)
    preview = api.post(path, params={"preview": True}, content=raw, headers=auth)
    assert preview.status_code == 200 and preview.json()["count"] == 2
    assert api.get("/properties/NEW-CSV1").status_code == 404
    assert api.post(path, content=raw, headers=auth).json()["count"] == 2
    assert api.post(path, content=raw, headers=auth).status_code == 409
    assert api.post(path, content=csv_payload(first, property_data("NEW-BLOCKED")), headers=auth).status_code == 409
    assert api.get("/properties/NEW-BLOCKED").status_code == 404


def test_database_failure_rolls_back_csv_and_audits(api, headers, accounts):
    def invalidate(session, flush_context, instances):
        for row in session.new:
            if isinstance(row, PropertyRecord) and row.property_id == "NEW-FAIL":
                row.price_myr = -1

    records = csv_payload(property_data("NEW-GOOD"), property_data("NEW-FAIL"))
    event.listen(Session, "before_flush", invalidate)
    try:
        response = api.post("/admin/properties/csv", content=records, headers={**headers, **CSV})
    finally:
        event.remove(Session, "before_flush", invalidate)
    assert response.status_code == 503 and "INSERT" not in response.text
    assert api.get("/properties/NEW-GOOD").status_code == 404
    assert api.get("/properties/NEW-FAIL").status_code == 404
    with Session(accounts) as session:
        logs = session.scalars(select(AdminAudit).where(
            AdminAudit.action == "csv_import", AdminAudit.succeeded.is_(False),
        )).all()
        assert any(log.property_ids == ["NEW-GOOD", "NEW-FAIL"] for log in logs)
