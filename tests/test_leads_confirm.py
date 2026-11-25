from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.lead import LeadConfirmRequest
from backend.services.lead_service import confirmation_request_hash


def payload():
    return {
        "property_id": " p001 ",
        "customer_name": " Demo Alex ",
        "phone": "+60 12-345 6789",
        "consent": True,
        "idempotency_key": str(uuid4()),
    }


@pytest.mark.parametrize(("field", "value"), [
    ("consent", False), ("consent", "true"), ("consent", 1),
    ("customer_name", "A"), ("phone", "Demo private example"), ("property_id", " "),
    ("message", "x" * 501), ("idempotency_key", ""), ("idempotency_key", "invalid"),
    ("idempotency_key", "00000000-0000-1000-8000-000000000000"),
    ("lead_id", "client-id"), ("consented_at", "2026-11-24"), ("status", "NEW"),
])
def test_invalid_confirm_payload(field, value, monkeypatch):
    def unexpected_engine():
        pytest.fail("Invalid input reached the database")

    monkeypatch.setattr("backend.services.lead_service.get_engine", unexpected_engine)
    with TestClient(app) as client:
        response = client.post("/leads/confirm", json={**payload(), field: value})
    assert response.status_code == 422
    issue = response.json()["detail"][0]
    assert issue["loc"] == ["body", field]
    assert set(issue) == {"loc", "msg", "type"}
    assert "Demo private example" not in response.text


@pytest.mark.parametrize("field", ["property_id", "customer_name", "phone", "consent", "idempotency_key"])
def test_missing_confirm_field(field):
    data = payload()
    del data[field]
    with TestClient(app) as client:
        response = client.post("/leads/confirm", json=data)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", field]


def test_hash_uses_validated_business_data():
    data = payload()
    first = LeadConfirmRequest.model_validate(data)
    equivalent = LeadConfirmRequest.model_validate({
        **data, "property_id": "P001", "phone": "+60123456789", "customer_name": "Demo Alex",
        "idempotency_key": str(uuid4()), "message": None,
    })
    changed = LeadConfirmRequest.model_validate({**data, "message": "Different enquiry"})
    assert first.phone == "+60123456789"
    assert confirmation_request_hash(first) == confirmation_request_hash(equivalent)
    assert len(confirmation_request_hash(first)) == 64
    assert confirmation_request_hash(first) != confirmation_request_hash(changed)


def test_missing_database_configuration_is_safe(monkeypatch):
    def unavailable():
        raise RuntimeError("private database configuration")

    monkeypatch.setattr("backend.services.lead_service.get_engine", unavailable)
    with TestClient(app) as client:
        response = client.post("/leads/confirm", json=payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "Lead database is unavailable"}
