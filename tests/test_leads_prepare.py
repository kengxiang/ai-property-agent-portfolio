"""Offline tests: do not require PostgreSQL, Ollama, or customer data."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def stub_repository(monkeypatch):
    class ReadOnlyRepo:
        def get_by_id(self, property_id):
            if property_id == "P001":
                return SimpleNamespace(name="KL Central Residence")
            return None
    monkeypatch.setattr(
        "backend.services.lead_service.create_property_repository",
        lambda: ReadOnlyRepo(),
    )


VALID = {
    "property_id": " p001 ",
    "customer_name": " Demo Alex ",
    "phone": "+60 12-345 6789",
    "message": "Please contact me about viewing.",
}


def test_prepare_returns_only_preview():
    response = client.post("/leads/prepare", json=VALID)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "AWAITING_CONFIRMATION"
    assert body["confirmation_required"] is True
    assert body["draft"]["property_id"] == "P001"
    assert body["draft"]["customer_name"] == "Demo Alex"
    assert body["draft"]["phone"] == "+60123456789"
    assert body["draft"]["property_name"] == "KL Central Residence"
    assert body["draft"]["message"] == "Please contact me about viewing."
    assert "follow up on this property enquiry" in body["contact_purpose"]
    assert "No lead has been saved" in body["notice"]


def test_unknown_property_rejected():
    assert client.post("/leads/prepare", json={**VALID, "property_id": "P999"}).status_code == 404


@pytest.mark.parametrize("changes", [
    {"customer_name": " "},
    {"customer_name": " A "},
    {"customer_name": "x" * 101},
    {"phone": "1234"},
    {"phone": "abc12345678"},
    {"phone": "01234567890123456"},
    {"phone": "6012/3456789"},
    {"phone": "60+123456789"},
    {"property_id": " "},
    {"property_id": "X" * 21},
    {"message": "x" * 501},
])
def test_invalid_values_rejected(changes):
    assert client.post("/leads/prepare", json={**VALID, **changes}).status_code == 422


@pytest.mark.parametrize("field", ["property_id", "customer_name", "phone"])
def test_required_fields(field):
    payload = {key: value for key, value in VALID.items() if key != field}
    assert client.post("/leads/prepare", json=payload).status_code == 422


@pytest.mark.parametrize("field", [
    "consent", "lead_id", "status", "consented_at", "confirmation_required", "created_at",
])
def test_server_controlled_fields_are_forbidden(field):
    assert client.post("/leads/prepare", json={**VALID, field: True}).status_code == 422


@pytest.mark.parametrize("message", [None, "  Please contact me.  "])
def test_optional_message_is_normalized(message):
    payload = {key: value for key, value in VALID.items() if key != "message"}
    if message is not None:
        payload["message"] = message
    response = client.post("/leads/prepare", json=payload)
    assert response.status_code == 200
    assert response.json()["draft"]["message"] == (message.strip() if message else None)


def test_prepare_payload_cannot_confirm():
    response = client.post("/leads/confirm", json=VALID)
    assert response.status_code == 422
    assert {error["loc"][-1] for error in response.json()["detail"]} == {
        "consent", "idempotency_key",
    }


def test_legacy_property_search_still_available():
    response = client.get("/properties", params={"location": "KL"})
    assert response.status_code == 200
    assert any(property_item["property_id"] == "P001" for property_item in response.json())
