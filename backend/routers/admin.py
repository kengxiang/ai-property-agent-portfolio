"""Administration endpoints. Authentication is enforced by FastAPI dependencies."""

import os
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database.connection import get_engine
from backend.services import admin_ingestion as service, model_settings


router = APIRouter(prefix="/admin", tags=["Administration"])
bearer = HTTPBearer(auto_error=False)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, username):
        return username.strip().lower()


def admin_engine():
    if os.getenv("ADMIN_INGESTION_ENABLED") != "1":
        raise HTTPException(404, "Administration is disabled")
    try:
        engine = get_engine()
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(503, "Administration database unavailable") from None
    try:
        yield engine
    finally:
        engine.dispose()


Engine = Annotated[object, Depends(admin_engine)]


def database_call(fn, *args):
    try:
        return fn(*args)
    except SQLAlchemyError:
        raise HTTPException(503, "Administration database operation failed") from None


def admin_identity(engine: Engine, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    token = credentials.credentials if credentials else None
    return database_call(service.authorize, engine, token), token


Identity = Annotated[tuple[str, str], Depends(admin_identity)]


@router.post("/login")
def login(request: Credentials, response: Response, engine: Engine):
    response.headers["Cache-Control"] = "no-store"
    return database_call(service.login, engine, request.username, request.password.get_secret_value())


@router.post("/logout", status_code=204)
def logout(engine: Engine, identity: Identity):
    username, token = identity
    database_call(service.logout, engine, token, username)
    return Response(status_code=204)


@router.post("/properties", status_code=201)
def create_property(request: service.AdminProperty, engine: Engine, identity: Identity):
    return database_call(service.ingest, engine, identity[0], "create", [(None, request)])[0]


def import_csv(engine, username, raw, preview):
    action = "csv_preview" if preview else "csv_import"
    try:
        rows = service.parse_csv(raw)
    except HTTPException:
        with Session(engine) as session, session.begin():
            service.audit(session, username, action, False)
        raise
    properties = service.ingest(engine, username, action, rows, preview)
    return {"count": len(properties), "properties": properties}


@router.post("/properties/csv")
async def csv_upload(request: Request, engine: Engine, identity: Identity, preview: bool = False):
    if request.headers.get("content-type", "").split(";")[0] != "text/csv":
        raise HTTPException(415, "Use Content-Type: text/csv")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > service.MAX_CSV_BYTES:
            raise HTTPException(413, "CSV exceeds 1 MiB")
        body.extend(chunk)
    return await run_in_threadpool(database_call, import_csv, engine, identity[0], body, preview)

class ProviderSetting(BaseModel):
    provider: Literal["local", "cloud"]


@router.get("/model")
def provider_settings(engine: Engine, identity: Identity):
    return database_call(model_settings.status, engine)


@router.put("/model")
def update_provider(request: ProviderSetting, engine: Engine, identity: Identity):
    return database_call(model_settings.set_primary, engine, request.provider)
