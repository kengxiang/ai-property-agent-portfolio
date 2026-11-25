"""Run against an isolated, migrated test database with RUN_POSTGRES_TESTS=1."""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import DateTime, inspect, text

from backend.main import app
from backend.services import postgres_property_service


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires isolated PostgreSQL",
)


@pytest.fixture
def engine(database, monkeypatch):
    monkeypatch.setattr(postgres_property_service, "get_engine", lambda: database)
    return database


def test_lead_schema(engine):
    schema = inspect(engine)
    columns = {column["name"]: column for column in schema.get_columns("leads")}
    assert {name for name, column in columns.items() if not column["nullable"]} == {
        "lead_id", "property_id", "customer_name", "phone", "contact_purpose",
        "consented_at", "status", "source", "created_at",
    }
    assert {name for name, column in columns.items() if column["nullable"]} == {"message"}
    assert columns["lead_id"]["type"].length == 36
    assert columns["property_id"]["type"].length == 20
    for name in ("consented_at", "created_at"):
        assert isinstance(columns[name]["type"], DateTime)
        assert columns[name]["type"].timezone
    assert "NEW" in columns["status"]["default"]
    assert "STREAMLIT" in columns["source"]["default"]
    assert schema.get_pk_constraint("leads")["constrained_columns"] == ["lead_id"]
    foreign_key, = schema.get_foreign_keys("leads")
    assert foreign_key["constrained_columns"] == ["property_id"]
    assert foreign_key["referred_table"] == "properties"
    assert foreign_key["referred_columns"] == ["property_id"]
    assert foreign_key["options"]["ondelete"] == "RESTRICT"


def test_repeated_prepare_preserves_lead_count(engine, monkeypatch):
    monkeypatch.setenv("PROPERTY_BACKEND", "postgres")
    with engine.connect() as connection:
        property_name = connection.execute(
            text("SELECT name FROM properties WHERE property_id = 'P001'")
        ).scalar_one()
        before = connection.execute(text("SELECT COUNT(*) FROM leads")).scalar_one()
        payload = {"property_id": " p001 ", "customer_name": " Demo Alex ", "phone": "+60 12-345 6789"}

        with TestClient(app) as client:
            for _ in range(2):
                response = client.post("/leads/prepare", json=payload)
                assert response.status_code == 200
                body = response.json()
                assert body["draft"]["property_name"] == property_name
                assert body["draft"]["phone"] == "+60123456789"
                assert body["status"] == "AWAITING_CONFIRMATION"
                assert body["confirmation_required"] is True

        after = connection.execute(text("SELECT COUNT(*) FROM leads")).scalar_one()
    assert after == before
