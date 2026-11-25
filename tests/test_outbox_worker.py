import json
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from automation.outbox_worker import Claim, Settings, deliver, main, retry_delay


@pytest.fixture
def settings():
    return Settings("http://receiver.test/webhook", "demo-secret", 1, 1, 3, 10)


@pytest.fixture
def claim():
    return Claim(str(uuid4()), "LeadCreated", str(uuid4()), "P003", str(uuid4()), 1)


def test_authenticated_delivery(settings, claim):
    def receiver(request):
        assert request.method == "POST"
        assert request.headers["X-Webhook-Secret"] == "demo-secret"
        assert json.loads(request.content) == {
            "event_id": claim.event_id, "event_type": "LeadCreated",
            "lead_id": claim.lead_id, "property_id": "P003",
        }
        body = json.dumps({"event_id": claim.event_id, "accepted": True}).encode()
        return httpx.Response(200, stream=httpx.ByteStream(body))

    with httpx.Client(transport=httpx.MockTransport(receiver)) as client:
        assert deliver(client, settings, claim) is None


@pytest.mark.parametrize(("status", "body", "error"), [
    (503, b"internal details", "HTTP_503"),
    (302, b"", "HTTP_302"),
    (204, b"", "INVALID_ACK"),
    (200, b"not json", "INVALID_ACK"),
    (200, b"x" * 4097, "INVALID_ACK"),
    (200, b'{"event_id":"different","accepted":true}', "INVALID_ACK"),
    (200, b'{"event_id":"matching","accepted":1}', "INVALID_ACK"),
    (200, b'{"event_id":"matching","accepted":false}', "INVALID_ACK"),
])
def test_invalid_delivery_remains_retryable(settings, claim, status, body, error):
    def receiver(request):
        assert request.url == settings.webhook_url
        return httpx.Response(status, stream=httpx.ByteStream(body), headers={"Location": "http://other.test"})

    with httpx.Client(transport=httpx.MockTransport(receiver), follow_redirects=True) as client:
        assert deliver(client, settings, replace(claim, event_id="matching")) == error


@pytest.mark.parametrize(("exception", "error"), [
    (httpx.ReadTimeout, "TIMEOUT"), (httpx.ConnectError, "CONNECTION_ERROR"),
])
def test_transport_failures(settings, claim, exception, error):
    def receiver(request):
        raise exception("private error details")

    with httpx.Client(transport=httpx.MockTransport(receiver)) as client:
        assert deliver(client, settings, claim) == error


def test_invalid_event_is_not_sent(settings, claim):
    def unexpected_request(request):
        pytest.fail("Malformed event reached the network")

    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as client:
        assert deliver(client, settings, replace(claim, property_id=None)) == "INVALID_EVENT"


@pytest.mark.parametrize("missing", ["N8N_WEBHOOK_URL", "N8N_WEBHOOK_SECRET"])
def test_missing_configuration_exits_before_database(monkeypatch, caplog, missing):
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://receiver.test/webhook")
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "demo-secret")
    monkeypatch.delenv(missing)
    monkeypatch.setattr("automation.outbox_worker.get_engine", lambda: pytest.fail("DB was opened"))
    assert main(["--once"]) == 2
    assert missing in caplog.text
    assert "demo-secret" not in caplog.text


def test_defaults_and_retry_schedule(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://receiver.test/webhook")
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "demo-secret")
    for name in ("OUTBOX_HTTP_TIMEOUT", "OUTBOX_POLL_INTERVAL", "OUTBOX_MAX_ATTEMPTS", "OUTBOX_LEASE_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    assert Settings.from_env() == Settings("http://receiver.test/webhook", "demo-secret", 10, 2, 5, 30)
    assert [retry_delay(n) for n in (1, 2, 3, 100)] == [
        timedelta(seconds=n) for n in (5, 10, 20, 300)
    ]
    monkeypatch.setenv("OUTBOX_HTTP_TIMEOUT", "30")
    with pytest.raises(ValueError, match="must exceed"):
        Settings.from_env()


def test_polling_waits_when_idle_and_disposes_on_shutdown(settings, monkeypatch):
    from unittest.mock import Mock

    engine = Mock()
    process = Mock(side_effect=[True, False, KeyboardInterrupt])
    pause = Mock()
    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    monkeypatch.setattr("automation.outbox_worker.get_engine", lambda: engine)
    monkeypatch.setattr("automation.outbox_worker.process_one", process)
    monkeypatch.setattr("automation.outbox_worker.sleep", pause)

    assert main([]) == 0
    assert process.call_count == 3
    pause.assert_called_once_with(settings.poll_interval)
    engine.dispose.assert_called_once()


def test_database_failure_logs_location_and_sqlstate_without_private_data(settings, monkeypatch, caplog):
    from unittest.mock import Mock

    from sqlalchemy.exc import OperationalError

    class DriverAuthError(Exception):
        sqlstate = "28P01"

    engine = Mock()
    def fail_claim(*args):
        raise OperationalError(
            "private SQL statement", {"password": "private binding"},
            DriverAuthError("private database URL and credentials"),
        )

    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    monkeypatch.setattr("automation.outbox_worker.get_engine", lambda: engine)
    monkeypatch.setattr("automation.outbox_worker.claim_next", fail_claim)
    assert main(["--once"]) == 1
    assert "location=process_one:" in caplog.text
    assert "exception=OperationalError" in caplog.text
    assert "driver=DriverAuthError" in caplog.text
    assert "SQLSTATE=28P01" in caplog.text
    assert "private" not in caplog.text
    engine.dispose.assert_called_once()
