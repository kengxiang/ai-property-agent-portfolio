"""Claim and deliver PostgreSQL Outbox events independently of FastAPI."""

import argparse
import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import timedelta
from time import sleep
from traceback import extract_tb
from uuid import uuid4

import httpx
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database.connection import get_engine
from backend.database.lead_models import LeadRecord  # Registers the Outbox foreign-key target.
from backend.database.lead_operations_models import OutboxEventRecord as Event


logger = logging.getLogger(__name__)
MAX_ACK_BYTES = 4096


@dataclass(frozen=True)
class Settings:
    webhook_url: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    http_timeout: float
    poll_interval: float
    max_attempts: int
    lease_seconds: float

    @classmethod
    def from_env(cls):
        try:
            url = httpx.URL(os.getenv("N8N_WEBHOOK_URL", ""))
        except httpx.InvalidURL:
            raise ValueError("N8N_WEBHOOK_URL must be a valid HTTP(S) URL") from None
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.fragment:
            raise ValueError("N8N_WEBHOOK_URL must be an HTTP(S) URL without credentials or fragments")
        secret = os.getenv("N8N_WEBHOOK_SECRET", "")
        if not secret or not all(33 <= ord(character) <= 126 for character in secret):
            raise ValueError("N8N_WEBHOOK_SECRET must contain visible ASCII characters without spaces")

        timeout = positive_env("OUTBOX_HTTP_TIMEOUT", 10.0)
        poll = positive_env("OUTBOX_POLL_INTERVAL", 2.0)
        attempts = positive_env("OUTBOX_MAX_ATTEMPTS", 5)
        lease = positive_env("OUTBOX_LEASE_SECONDS", 30.0)
        if lease <= timeout:
            raise ValueError("OUTBOX_LEASE_SECONDS must exceed OUTBOX_HTTP_TIMEOUT")
        return cls(str(url), secret, timeout, poll, attempts, lease)


def positive_env(name, default):
    try:
        value = type(default)(os.getenv(name, str(default)))
        if math.isfinite(value) and value > 0:
            return value
    except (ValueError, OverflowError):
        pass
    raise ValueError(f"{name} must be a positive finite {type(default).__name__}")


@dataclass(frozen=True)
class Claim:
    event_id: str
    event_type: str
    lead_id: str
    property_id: str | None
    token: str
    attempts: int


def claim_next(engine, settings):
    while True:
        with Session(engine) as session, session.begin():
            now = func.clock_timestamp()
            event = session.scalar(
                select(Event)
                .where(or_(
                    and_(
                        Event.delivery_status.in_(("PENDING", "RETRY")),
                        or_(Event.next_attempt_at.is_(None), Event.next_attempt_at <= now),
                    ),
                    and_(
                        Event.delivery_status == "PROCESSING",
                        or_(Event.lease_expires_at.is_(None), Event.lease_expires_at <= now),
                    ),
                ))
                .order_by(func.coalesce(Event.next_attempt_at, Event.lease_expires_at, Event.created_at),
                          Event.event_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if event is None:
                return None
            if event.attempts >= settings.max_attempts:
                event.delivery_status = "FAILED"
                event.last_error = event.last_error or "ATTEMPT_LIMIT_REACHED"
                event.next_attempt_at = event.lease_expires_at = event.claim_token = None
                continue

            event.delivery_status = "PROCESSING"
            event.claim_token = str(uuid4())
            event.lease_expires_at = session.scalar(select(now)) + timedelta(seconds=settings.lease_seconds)
            event.next_attempt_at = None
            event.attempts += 1
            payload = event.payload
            return Claim(
                event.event_id, event.event_type, event.aggregate_id,
                payload.get("property_id") if isinstance(payload, dict) else None,
                event.claim_token, event.attempts,
            )


def deliver(client, settings, claim):
    if (
        claim.event_type != "LeadCreated"
        or not isinstance(claim.property_id, str)
        or not claim.property_id.strip()
        or len(claim.property_id) > 20
    ):
        return "INVALID_EVENT"

    try:
        with client.stream(
            "POST", settings.webhook_url,
            headers={"X-Webhook-Secret": settings.webhook_secret, "Accept-Encoding": "identity"},
            json={
                "event_id": claim.event_id, "event_type": claim.event_type,
                "lead_id": claim.lead_id, "property_id": claim.property_id,
            },
            timeout=settings.http_timeout, follow_redirects=False,
        ) as response:
            if not response.is_success:
                return f"HTTP_{response.status_code}"
            body = bytearray()
            for chunk in response.iter_raw(chunk_size=MAX_ACK_BYTES + 1):
                if len(body) + len(chunk) > MAX_ACK_BYTES:
                    return "INVALID_ACK"
                body.extend(chunk)
            acknowledgement = json.loads(body)
    except httpx.TimeoutException:
        return "TIMEOUT"
    except httpx.RequestError:
        return "CONNECTION_ERROR"
    except ValueError:
        return "INVALID_ACK"

    if (
        isinstance(acknowledgement, dict)
        and acknowledgement.get("event_id") == claim.event_id
        and acknowledgement.get("accepted") is True
    ):
        return None
    return "INVALID_ACK"


def retry_delay(attempts):
    return timedelta(seconds=min(300, 5 * 2 ** min(attempts - 1, 6)))


def finish_claim(engine, settings, claim, error):
    with Session(engine) as session, session.begin():
        now = session.scalar(select(func.clock_timestamp()))
        status = "DELIVERED"
        next_attempt_at = None
        if error is not None:
            status = "FAILED" if claim.attempts >= settings.max_attempts else "RETRY"
            if status == "RETRY":
                next_attempt_at = now + retry_delay(claim.attempts)

        updated_id = session.scalar(
            update(Event)
            .where(
                Event.event_id == claim.event_id,
                Event.delivery_status == "PROCESSING",
                Event.claim_token == claim.token,
                Event.lease_expires_at > func.clock_timestamp(),
            )
            .values(
                delivery_status=status, next_attempt_at=next_attempt_at, claim_token=None,
                lease_expires_at=None, last_error=error, delivered_at=now if error is None else None,
            )
            .returning(Event.event_id)
        )
        return updated_id is not None


def process_one(engine, client, settings):
    claim = claim_next(engine, settings)
    if claim is None:
        return False
    error = deliver(client, settings, claim)
    if not finish_claim(engine, settings, claim, error):
        logger.warning("Outbox claim expired or changed for event %s", claim.event_id)
    elif error is not None:
        logger.warning("Outbox attempt %s failed for event %s (%s)", claim.attempts, claim.event_id, error)
    else:
        logger.info("Outbox event %s delivered", claim.event_id)
    return True


def log_database_error(level, message, error):
    # Driver messages and rendered SQL can contain credentials or bound values.
    driver_error = getattr(error, "orig", None)
    sqlstate = getattr(driver_error, "sqlstate", None)
    if not isinstance(sqlstate, str) or len(sqlstate) != 5 or not sqlstate.isascii() or not sqlstate.isalnum():
        sqlstate = "n/a"
    location = next(
        (f"{frame.name}:{frame.lineno}" for frame in reversed(extract_tb(error.__traceback__))
         if frame.filename == __file__),
        "unknown",
    )
    logger.log(
        level, "%s (location=%s, exception=%s, driver=%s, SQLSTATE=%s)",
        message, location, type(error).__name__,
        type(driver_error).__name__ if driver_error is not None else "n/a", sqlstate,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one eligible event and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # httpx INFO logs include URLs, which may have sensitive path or query values.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings.from_env()
    except ValueError as error:
        logger.error("%s", error)
        return 2

    try:
        engine = get_engine()
    except (RuntimeError, SQLAlchemyError, ValueError) as error:
        log_database_error(logging.ERROR, "Outbox database configuration is unavailable", error)
        return 2
    try:
        with httpx.Client(trust_env=False) as client:
            if args.once:
                process_one(engine, client, settings)
                return 0
            while True:
                try:
                    if process_one(engine, client, settings):
                        continue
                except OperationalError as error:
                    log_database_error(logging.WARNING, "Outbox database temporarily unavailable", error)
                sleep(settings.poll_interval)
    except KeyboardInterrupt:
        return 0
    except SQLAlchemyError as error:
        log_database_error(logging.ERROR, "Outbox database operation failed", error)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
