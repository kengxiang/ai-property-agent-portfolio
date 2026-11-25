"""Exercise real PostgreSQL claims and a local mock webhook receiver."""

import json
import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Thread

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from automation.outbox_worker import Settings, claim_next, deliver, finish_claim, process_one
from backend.database.lead_operations_models import OutboxEventRecord as Event
from backend.main import app
from test_leads_confirm_postgres import payload


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires a dedicated PostgreSQL test database",
)


@pytest.fixture
def engine(database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database.url.render_as_string(hide_password=False))
    with database.begin() as connection:
        connection.execute(delete(Event))
    return database


def confirm_event(engine):
    with TestClient(app) as client:
        response = client.post("/leads/confirm", json=payload())
        assert response.status_code == 200
    with Session(engine) as session:
        return session.scalar(select(Event.event_id).filter_by(aggregate_id=response.json()["lead_id"]))


def load(engine, event_id):
    with Session(engine) as session:
        return session.get(Event, event_id)


def make_due(engine, event_id, column):
    with engine.begin() as connection:
        connection.execute(update(Event).where(Event.event_id == event_id).values({
            column: func.clock_timestamp() - timedelta(seconds=1),
        }))


@pytest.fixture
def webhook():
    received = []
    replies = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers.get("X-Webhook-Secret"), body))
            status = replies.pop(0) if replies else 200
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"event_id": body["event_id"], "accepted": True}).encode())

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Receiver) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/webhook"
            yield Settings(url, "local-demo-secret", 1, 1, 2, 10), received, replies
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_cli_delivery_and_delivered_is_not_sent_twice(engine, webhook, monkeypatch):
    settings, received, _ = webhook
    event_id = confirm_event(engine)
    monkeypatch.setenv("N8N_WEBHOOK_URL", settings.webhook_url)
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", settings.webhook_secret)

    def run_worker():
        # A clean interpreter must register every model it needs without importing FastAPI.
        result = subprocess.run(
            [sys.executable, "-m", "automation.outbox_worker", "--once"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr

    run_worker()
    row = load(engine, event_id)
    assert row.delivery_status == "DELIVERED"
    assert row.delivered_at is not None and row.attempts == 1
    assert row.next_attempt_at is None and row.claim_token is None and row.lease_expires_at is None
    assert received == [("/webhook", settings.webhook_secret, {
        "event_id": event_id, "event_type": "LeadCreated",
        "lead_id": row.aggregate_id, "property_id": "P001",
    })]
    run_worker()
    assert len(received) == 1


def test_http_failure_retries_then_stops_at_limit(engine, webhook):
    settings, received, replies = webhook
    replies.extend([503, 503])
    event_id = confirm_event(engine)
    with httpx.Client(trust_env=False) as client:
        assert process_one(engine, client, settings)
        row = load(engine, event_id)
        assert (row.delivery_status, row.attempts, row.last_error) == ("RETRY", 1, "HTTP_503")
        with engine.connect() as connection:
            now = connection.scalar(select(func.clock_timestamp()))
        assert now < row.next_attempt_at <= now + timedelta(seconds=5)
        assert not process_one(engine, client, settings)
        make_due(engine, event_id, Event.next_attempt_at)
        assert process_one(engine, client, settings)
        assert not process_one(engine, client, settings)
    row = load(engine, event_id)
    assert (row.delivery_status, row.attempts, row.last_error) == ("FAILED", 2, "HTTP_503")
    assert row.next_attempt_at is None and row.delivered_at is None
    assert [request[2]["event_id"] for request in received] == [event_id, event_id]


def test_two_concurrent_workers_get_one_claim(engine, webhook):
    settings, _, _ = webhook
    event_id = confirm_event(engine)
    ready = Barrier(2, timeout=5)

    def claim():
        ready.wait()
        return claim_next(engine, settings)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(claim) for _ in range(2)]
        claims = [future.result() for future in futures]
    assert [claim.event_id for claim in claims if claim is not None] == [event_id]
    assert load(engine, event_id).attempts == 1


def test_locked_event_does_not_block_another_claim(engine, webhook):
    settings, _, _ = webhook
    first = confirm_event(engine)
    second = confirm_event(engine)
    with Session(engine) as session, session.begin():
        session.execute(select(Event.event_id).where(Event.event_id == first).with_for_update())
        assert claim_next(engine, settings).event_id == second
        assert claim_next(engine, settings) is None
    assert claim_next(engine, settings).event_id == first


def test_recovery_replays_event_and_rejects_stale_finish(engine, webhook):
    settings, received, _ = webhook
    event_id = confirm_event(engine)
    old = claim_next(engine, settings)
    assert claim_next(engine, settings) is None
    with httpx.Client(trust_env=False) as client:
        # The receiver accepted the event, then the worker exited before recording the ACK.
        assert deliver(client, settings, old) is None
        make_due(engine, event_id, Event.lease_expires_at)
        assert not finish_claim(engine, settings, old, None)
        current = claim_next(engine, settings)
        assert current.event_id == event_id and current.token != old.token and current.attempts == 2
        assert not finish_claim(engine, settings, old, "HTTP_503")
        assert load(engine, event_id).claim_token == current.token
        assert deliver(client, settings, current) is None
        assert finish_claim(engine, settings, current, None)
    assert load(engine, event_id).delivery_status == "DELIVERED"
    assert [request[2]["event_id"] for request in received] == [event_id, event_id]


def test_abandoned_final_attempt_expires_to_failed(engine, webhook):
    settings, _, _ = webhook
    settings = replace(settings, max_attempts=1)
    event_id = confirm_event(engine)
    claim_next(engine, settings)
    make_due(engine, event_id, Event.lease_expires_at)
    assert claim_next(engine, settings) is None
    row = load(engine, event_id)
    assert (row.delivery_status, row.last_error) == ("FAILED", "ATTEMPT_LIMIT_REACHED")
    assert row.claim_token is None and row.lease_expires_at is None


def test_connection_refused_records_retry(engine, webhook):
    settings, _, _ = webhook
    event_id = confirm_event(engine)
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{unused.getsockname()[1]}/webhook"
        with httpx.Client(trust_env=False) as client:
            assert process_one(engine, client, replace(settings, webhook_url=url))
    row = load(engine, event_id)
    assert (row.delivery_status, row.last_error) == ("RETRY", "CONNECTION_ERROR")


@pytest.mark.parametrize(("failure", "error"), [("timeout", "TIMEOUT"), ("bad_ack", "INVALID_ACK")])
def test_failed_delivery_keeps_a_retryable_event(engine, webhook, failure, error):
    settings, _, _ = webhook
    event_id = confirm_event(engine)

    def receiver(request):
        # The processing status is visible to another connection and the row is unlocked.
        with engine.begin() as connection:
            row = connection.execute(
                select(Event.delivery_status).where(Event.event_id == event_id).with_for_update(nowait=True)
            ).scalar_one()
            assert row == "PROCESSING"
        if failure == "timeout":
            raise httpx.ReadTimeout("mock receiver did not respond")
        return httpx.Response(200, stream=httpx.ByteStream(b'{"accepted":true}'))

    with httpx.Client(transport=httpx.MockTransport(receiver)) as client:
        assert process_one(engine, client, settings)
    row = load(engine, event_id)
    assert (row.delivery_status, row.last_error) == ("RETRY", error)
    assert row.delivered_at is None and row.next_attempt_at is not None
