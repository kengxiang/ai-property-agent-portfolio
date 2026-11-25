"""Persist model selection and verify cloud configuration before enabling it."""

import os

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent.graph import create_cloud_model
from backend.database.connection import get_engine
from backend.database.model_settings import ModelSetting


class VerifyCloudTool(BaseModel):
    value: str


def cloud_configuration():
    key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "").strip()
    return model if key and model else None


def verify_cloud():
    model_id = cloud_configuration()
    if not model_id:
        raise HTTPException(409, "Gemini credentials and model are not configured")
    try:
        model = create_cloud_model()
        reply = model.bind_tools([VerifyCloudTool], tool_choice="VerifyCloudTool").invoke(
            "Call VerifyCloudTool with value 'ready'."
        )
        if not any(call["name"] == "VerifyCloudTool" and call["args"] == {"value": "ready"}
                   for call in reply.tool_calls):
            raise ValueError("Synthetic capability check failed")
    except Exception:
        raise HTTPException(409, "Gemini authentication or tool capability check failed") from None
    return model_id


def status(engine):
    with Session(engine) as session:
        setting = session.get(ModelSetting, "primary")
        configured = setting.provider if setting else "local"
        model = cloud_configuration()
        active = "cloud" if configured == "cloud" and model and setting.cloud_model == model else "local"
        return {"configured_provider": configured, "active_provider": active,
                "gemini_status": "verified" if active == "cloud" else ("configured" if model else "unconfigured")}


def current_status():
    if os.getenv("PROPERTY_BACKEND", "csv").strip().lower() != "postgres":
        return {"configured_provider": "local", "active_provider": "local",
                "gemini_status": "configured" if cloud_configuration() else "unconfigured"}
    try:
        engine = get_engine()
        try:
            return status(engine)
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValueError, RuntimeError):
        raise HTTPException(503, "Model settings are unavailable") from None


def set_primary(engine, provider):
    cloud_model = verify_cloud() if provider == "cloud" else None
    values = {"provider": provider, "cloud_model": cloud_model}
    statement = insert(ModelSetting).values(name="primary", **values)
    with Session(engine) as session, session.begin():
        session.execute(statement.on_conflict_do_update(index_elements=[ModelSetting.name], set_=values))
    return status(engine)
