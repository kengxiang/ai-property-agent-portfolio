"""Administrator authentication and transactional, create-only property ingestion."""

import csv
import hashlib
import hmac
import io
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field, ValidationError, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database.admin_models import AdminAccount, AdminAudit, AdminSession
from backend.database.models import PropertyRecord
from backend.schemas.property import Property


MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 500
TOKEN_TTL = timedelta(hours=1)
DUMMY_SALT = b"\0" * 16


class AdminProperty(Property):
    property_id: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    display_location: str = Field(min_length=1, max_length=100)
    price_myr: int = Field(ge=0, le=2147483647)
    bedrooms: int = Field(ge=0, le=2147483647)
    bathrooms: int = Field(ge=0, le=2147483647)
    mrt_distance_m: int | None = Field(default=None, ge=0, le=2147483647)

    @field_validator("property_id", mode="before")
    @classmethod
    def normalize_id(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("property_id", "name", "display_location", "description")
    @classmethod
    def reject_null(cls, value):
        if "\0" in value:
            raise ValueError("NUL is not supported")
        return value


def password_digest(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1,
                          maxmem=64 * 1024**2, dklen=32)


def hash_password(password):
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{password_digest(password, salt).hex()}"


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def audit(session, username, action, succeeded, ids=()):
    session.add(AdminAudit(audit_id=str(uuid4()), username=username,
                           action=action, property_ids=list(ids), succeeded=succeeded))


def login(engine, username, password, roles=("admin",)):
    now = datetime.now(timezone.utc)
    with Session(engine) as session, session.begin():
        account = session.get(AdminAccount, username, with_for_update=True)
        if account is None:
            password_digest(password, DUMMY_SALT)
            raise HTTPException(401, "Invalid credentials")
        failures = session.scalar(select(func.count()).select_from(AdminAudit).where(
            AdminAudit.username == username, AdminAudit.action == "login",
            AdminAudit.succeeded.is_(False), AdminAudit.created_at > now - timedelta(minutes=5),
        ))
        if failures >= 5:
            raise HTTPException(429, "Too many attempts; retry later")
        salt, expected = account.password_hash.split(":")
        verified = hmac.compare_digest(password_digest(password, bytes.fromhex(salt)).hex(), expected)
        authorized = verified and account.role in roles
        audit(session, username, "login", authorized)
        if authorized:
            session.execute(delete(AdminSession).where(AdminSession.expires_at <= now))
            token = secrets.token_urlsafe(32)
            expiry = now + TOKEN_TTL
            session.add(AdminSession(token_hash=token_digest(token), username=username, expires_at=expiry))
    if not verified:
        raise HTTPException(401, "Invalid credentials")
    if not authorized:
        raise HTTPException(403, "Administrator access required")
    return {"access_token": token, "token_type": "bearer", "expires_at": expiry}


def authorize(engine, token, roles=("admin",)):
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    with Session(engine) as session:
        stored = session.get(AdminSession, token_digest(token))
        if stored is None or stored.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(401, "Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
        account = session.get(AdminAccount, stored.username)
        if account is None or account.role not in roles:
            raise HTTPException(403, "Administrator access required")
        return account.username


def logout(engine, token, username):
    with Session(engine) as session, session.begin():
        session.execute(delete(AdminSession).where(AdminSession.token_hash == token_digest(token)))
        audit(session, username, "logout", True)


def parse_csv(raw):
    fields = AdminProperty.model_fields
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader, [])
        required = {name for name, info in fields.items() if info.is_required()}
        if len(header) != len(set(header)) or set(header) - fields.keys() or required - set(header):
            raise HTTPException(422, {"row": 1, "message": "Invalid CSV columns",
                                      "missing_columns": sorted(required - set(header))})
        rows, errors, seen = [], [], set()
        for index, values in enumerate(reader):
            line = reader.line_num
            if index >= MAX_CSV_ROWS:
                raise HTTPException(413, "CSV exceeds row limit")
            if len(values) != len(header):
                errors.append({"row": line, "message": "Incorrect column count"})
                continue
            try:
                item = AdminProperty.model_validate(dict(zip(header, values)))
            except ValidationError as error:
                errors.append({"row": line, "fields": [
                    {"field": issue["loc"][0], "type": issue["type"]} for issue in error.errors()
                ]})
                continue
            if item.property_id in seen:
                errors.append({"row": line, "message": "Duplicate property ID in CSV"})
            seen.add(item.property_id)
            rows.append((line, item))
    except UnicodeDecodeError:
        raise HTTPException(422, "CSV must be UTF-8") from None
    except csv.Error:
        raise HTTPException(422, {"row": reader.line_num, "message": "Malformed CSV"}) from None
    if errors or not rows:
        raise HTTPException(422, errors or "CSV contains no rows")
    return rows


def ingest(engine, username, action, rows, preview=False):
    ids = [item.property_id for _, item in rows]
    conflicts = []
    try:
        with Session(engine) as session, session.begin():
            existing = set(session.scalars(select(PropertyRecord.property_id).where(PropertyRecord.property_id.in_(ids))))
            conflicts = [{"row": line, "property_id": item.property_id} for line, item in rows if item.property_id in existing]
            if not conflicts and not preview:
                session.add_all(PropertyRecord(**item.model_dump(mode="json")) for _, item in rows)
            audit(session, username, action, not conflicts, ids)
    except SQLAlchemyError as error:
        try:
            with Session(engine) as session, session.begin():
                audit(session, username, action, False, ids)
        except SQLAlchemyError:
            # A database outage can also prevent recording the failure.
            pass
        if isinstance(error, IntegrityError):
            diagnostics = getattr(error.orig, "diag", None)
            if getattr(error.orig, "sqlstate", None) == "23505" and getattr(diagnostics, "constraint_name", None) == "properties_pkey":
                raise HTTPException(409, "Property ID already exists; no rows imported") from None
        raise
    if conflicts:
        raise HTTPException(409, {"message": "Property ID already exists; no rows imported", "conflicts": conflicts})
    return [item.model_dump(mode="json") for _, item in rows]
