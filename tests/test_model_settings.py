import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.database.admin_models import AdminAccount
from backend.main import app
from backend.routers.admin import admin_engine
from backend.services import model_settings
from backend.services.admin_ingestion import hash_password

pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="requires isolated PostgreSQL")


def test_provider_permissions_persistence_and_cloud_checks(database, monkeypatch):
    monkeypatch.setenv("ADMIN_INGESTION_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", database.url.render_as_string(hide_password=False))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    password = "synthetic provider test password"
    with Session(database) as session, session.begin():
        for role in ("admin", "staff"):
            session.add(AdminAccount(username=role, role=role, password_hash=hash_password(password)))
    app.dependency_overrides[admin_engine] = lambda: database
    try:
        with TestClient(app) as client:
            def headers(role, route):
                response = client.post(f"/{route}/login", json={"username": role, "password": password})
                assert response.status_code == 200
                return {"Authorization": "Bearer " + response.json()["access_token"]}

            admin, staff = headers("admin", "admin"), headers("staff", "staff")
            endpoint = "/admin/model"
            cloud = {"provider": "cloud"}
            assert client.put(endpoint, json=cloud).status_code == 401
            assert client.put(endpoint, json=cloud, headers=staff).status_code == 403
            assert client.put(endpoint, json={"provider": "other"}, headers=admin).status_code == 422
            assert client.get(endpoint, headers=admin).json()["active_provider"] == "local"
            assert client.put(endpoint, json=cloud, headers=admin).status_code == 409

            monkeypatch.setenv("GEMINI_API_KEY", "mock credential")
            monkeypatch.setenv("GEMINI_MODEL", "mock model configuration")
            monkeypatch.setattr(model_settings, "create_cloud_model", lambda: None)
            assert client.put(endpoint, json=cloud, headers=admin).status_code == 409
            assert model_settings.status(database)["active_provider"] == "local"

            class FakeGemini:
                def bind_tools(self, tools, tool_choice):
                    assert tools == [model_settings.VerifyCloudTool]
                    assert tool_choice == "VerifyCloudTool"
                    return self

                def invoke(self, message):
                    assert message == "Call VerifyCloudTool with value 'ready'."
                    return SimpleNamespace(tool_calls=[{"name": "VerifyCloudTool", "args": {"value": "ready"}}])

            monkeypatch.setattr(model_settings, "create_cloud_model", FakeGemini)
            assert client.put(endpoint, json=cloud, headers=admin).json()["active_provider"] == "cloud"
            assert client.get(endpoint, headers=admin).json()["gemini_status"] == "verified"
            monkeypatch.setenv("GEMINI_MODEL", "different model configuration")
            assert client.get(endpoint, headers=admin).json()["active_provider"] == "local"
            assert client.put(endpoint, json={"provider": "local"}, headers=admin).status_code == 200

            prompt = {"message": "List a property", "provider": "cloud", "cloud_consent": True}
            assert client.post("/agent/chat", json=prompt).status_code == 403
            assert client.post("/agent/chat", json=prompt, headers=staff).status_code == 403
            assert client.post("/agent/chat", json={**prompt, "cloud_consent": False}, headers=admin).status_code == 422
            assert client.post("/agent/chat", json={**prompt, "message": "Write to synthetic@example.test"},
                               headers=admin).status_code == 422
    finally:
        app.dependency_overrides.pop(admin_engine)
