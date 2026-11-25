import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from uuid import UUID

import pytest
from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"
PROPERTY = {"property_id": "P001", "name": "KL Central Residence", "price_myr": 550000}
PREVIEW = {
    "status": "AWAITING_CONFIRMATION",
    "confirmation_required": True,
    "draft": {
        "property_id": "P001",
        "property_name": PROPERTY["name"],
        "customer_name": "Demo Alex",
        "phone": "+60123456789",
        "message": "Demo viewing",
    },
    "contact_purpose": "Follow up on the property enquiry.",
    "notice": "Preview only.",
}
CONFIRMED = {
    "lead_id": "27c72f31-3ab0-416e-9e5e-c17f93e8351f",
    "property_id": "P001",
    "status": "NEW",
    "confirmed": True,
}


def button(app, label):
    return next(widget for widget in app.button if widget.label == label)


@pytest.fixture
def backend(monkeypatch):
    calls = []
    replies = {"/properties": [PROPERTY], "/leads/prepare": PREVIEW, "/leads/confirm": CONFIRMED,
               "/agent/chat": {"answer": "Demo answer", "tool_calls": []}}

    def urlopen(request, timeout):
        path = urlsplit(request.full_url).path
        calls.append((request.method, path, json.loads(request.data) if request.data else None))
        body = replies[path]
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return calls, replies


def start(monkeypatch, url):
    monkeypatch.setenv("PROPERTY_API_BASE_URL", url)
    app = AppTest.from_file(str(APP)).run()
    assert not app.exception
    return app


def enter_details(app):
    app.selectbox(key="lead_property").select("P001")
    app.text_input[0].set_value(" Demo Alex ")
    app.text_input[1].set_value("+60 12-345 6789")
    app.text_area[0].set_value("Demo viewing")


def test_enquiry_review_consent_cancel_and_chat(monkeypatch, backend):
    calls, _ = backend
    app = start(monkeypatch, "http://enquiry-flow.test")
    assert {header.value for header in app.header} >= {"Property Enquiry", "AI Property Chat"}
    assert app.selectbox(key="lead_property").options == ["P001 — KL Central Residence — RM550,000"]
    assert app.selectbox(key="lead_property").value is None
    button(app, "Review Enquiry").click().run()
    assert app.error[0].value == "Please select a property."
    assert not any(path == "/leads/prepare" for _, path, _ in calls)

    app.chat_input[0].set_value("Find a property").run()
    assert app.session_state.history[0]["response"]["answer"] == "Demo answer"
    enter_details(app)
    button(app, "Review Enquiry").click().run()
    assert calls[-1] == ("POST", "/leads/prepare", {
        "property_id": "P001", "customer_name": " Demo Alex ",
        "phone": "+60 12-345 6789", "message": "Demo viewing",
    })
    assert any(item.value == "Phone Number: +60123456789" for item in app.text)
    assert not app.checkbox(key="lead_consent").value
    assert button(app, "Confirm & Submit").disabled
    assert not app.text_input
    first_key = app.session_state.lead_idempotency_key
    app.checkbox(key="lead_consent").check().run()
    assert app.checkbox(key="lead_consent").value
    assert not button(app, "Confirm & Submit").disabled
    button(app, "Edit Enquiry").click().run()
    assert not app.exception
    assert app.session_state.lead_idempotency_key is None
    assert not app.session_state.lead_consent
    assert app.text_input(key="lead_phone").value == "+60123456789"
    button(app, "Review Enquiry").click().run()
    assert not app.checkbox(key="lead_consent").value
    assert app.session_state.lead_idempotency_key != first_key

    app.checkbox(key="lead_consent").check().run()
    button(app, "Cancel Enquiry").click().run()
    assert not app.exception
    assert not app.checkbox
    assert app.session_state.lead_preview is None
    assert not app.session_state.lead_consent
    assert app.session_state.lead_idempotency_key is None
    assert app.session_state.lead_confirmation_errors == []
    assert len(app.session_state.history) == 1
    button(app, "Clear Chat").click().run()
    assert app.session_state.history == []
    assert sum(path == "/properties" for _, path, _ in calls) == 1
    assert not any(path == "/leads/confirm" for _, path, _ in calls)


@pytest.mark.parametrize(("status", "detail", "expected"), [
    (422, [{"loc": ["body", "customer_name"], "input": "private"},
           {"loc": ["body", "phone"]}],
     ["Please enter your name (2–100 characters).",
      "Please enter a valid phone number with 8–15 digits."]),
    (404, "Property not found",
     ["This property could not be found. Please select another property."]),
])
def test_friendly_backend_validation(monkeypatch, backend, status, detail, expected):
    _, replies = backend
    replies["/leads/prepare"] = HTTPError(
        "", status, "", {}, io.BytesIO(json.dumps({"detail": detail}).encode())
    )
    app = start(monkeypatch, f"http://validation-{status}.test")
    enter_details(app)
    button(app, "Review Enquiry").click().run()
    assert [error.value for error in app.error] == expected
    assert not app.checkbox


@pytest.mark.parametrize(("response", "message"), [
    ([], "No available properties were found."),
    (URLError("private details"), "Unable to load properties."),
    ({}, "Unable to load properties."),
], ids=["empty", "offline", "invalid"])
def test_property_list_unavailable(monkeypatch, backend, response, message, request):
    _, replies = backend
    replies["/properties"] = response
    app = start(monkeypatch, f"http://unavailable-{request.node.callspec.id}.test")
    assert any(element.value.startswith(message) for element in [*app.info, *app.error])
    assert button(app, "Review Enquiry").disabled


def test_malformed_preview(monkeypatch, backend):
    _, replies = backend
    replies["/leads/prepare"] = {**PREVIEW, "draft": {"property_id": "P001"}}
    app = start(monkeypatch, "http://bad-preview.test")
    enter_details(app)
    button(app, "Review Enquiry").click().run()
    assert "unexpected enquiry preview" in app.error[0].value
    assert not app.checkbox
    assert app.session_state.lead_idempotency_key is None


def test_confirm_sends_reviewed_data_and_success_prevents_resubmit(monkeypatch, backend):
    calls, _ = backend
    app = start(monkeypatch, "http://confirm-success.test")
    enter_details(app)
    button(app, "Review Enquiry").click().run()
    key = app.session_state.lead_idempotency_key
    assert UUID(key).version == 4
    app.run()
    assert app.session_state.lead_idempotency_key == key
    assert not any(path == "/leads/confirm" for _, path, _ in calls)
    app.checkbox(key="lead_consent").check().run()
    button(app, "Confirm & Submit").click().run()
    assert not app.exception
    assert calls[-1] == ("POST", "/leads/confirm", {
        "property_id": "P001", "customer_name": "Demo Alex",
        "phone": "+60123456789", "message": "Demo viewing", "consent": True,
        "idempotency_key": key,
    })
    assert app.success[0].value == "Enquiry Submitted Successfully"
    assert any(CONFIRMED["lead_id"] in line.value for line in app.text)
    app.run()
    assert all(item.label != "Confirm & Submit" for item in app.button)
    assert sum(path == "/leads/confirm" for _, path, _ in calls) == 1
    button(app, "Start New Enquiry").click().run()
    assert not app.exception and not app.success
    assert app.session_state.lead_confirmation_result is None
    assert app.session_state.lead_preview is None
    assert app.session_state.lead_idempotency_key is None
    assert not app.session_state.lead_consent
    assert app.selectbox(key="lead_property").value is None
    enter_details(app)
    button(app, "Review Enquiry").click().run()
    assert UUID(app.session_state.lead_idempotency_key).version == 4
    assert app.session_state.lead_idempotency_key != key


@pytest.mark.parametrize(("status", "message"), [
    (409, "already used with different details"),
    (422, "Please enter a valid phone number"),
    (500, "Retry this enquiry using the same details"),
    (503, "Retry this enquiry using the same details"),
    ("timeout", "submission status is unknown"),
    ("connection", "submission status is unknown"),
    ("invalid", "unexpected response"),
])
def test_confirmation_failure_retains_operation(monkeypatch, backend, status, message):
    calls, replies = backend
    failures = {
        "timeout": TimeoutError("private timeout"),
        "connection": URLError("private connection details"),
        "invalid": {**CONFIRMED, "property_id": "P002"},
    }
    if status in failures:
        replies["/leads/confirm"] = failures[status]
    else:
        detail = [{"loc": ["body", "phone"], "input": "private details"}]
        replies["/leads/confirm"] = HTTPError(
            "", status, "", {}, io.BytesIO(json.dumps({"detail": detail}).encode())
        )
    app = start(monkeypatch, f"http://confirm-{status}.test")
    enter_details(app)
    button(app, "Review Enquiry").click().run()
    key = app.session_state.lead_idempotency_key
    app.checkbox(key="lead_consent").check().run()
    button(app, "Confirm & Submit").click().run()
    assert not app.exception and not app.success
    assert message in app.error[0].value
    assert "private" not in app.error[0].value
    assert app.session_state.lead_idempotency_key == key
    failed_payload = calls[-1]
    app.run()
    assert sum(path == "/leads/confirm" for _, path, _ in calls) == 1

    if status in (500, 503, "timeout", "connection", "invalid"):
        replies["/leads/confirm"] = CONFIRMED
        button(app, "Confirm & Submit").click().run()
        assert calls[-1] == failed_payload
        assert app.success[0].value == "Enquiry Submitted Successfully"
    else:
        button(app, "Cancel Enquiry").click().run()
        assert not app.exception and not app.error
        assert app.session_state.lead_idempotency_key is None


def test_chat_forwards_only_current_session_turns(monkeypatch, backend):
    calls, _ = backend
    app = start(monkeypatch, "http://multi-turn-chat.test")
    app.chat_input[0].set_value("Introduce P022").run()
    app.chat_input[0].set_value("continue").run()
    assert not app.exception and not app.error
    assert calls[-1] == ("POST", "/agent/chat", {
        "message": "continue", "cloud_consent": False,
        "history": [{"question": "Introduce P022", "answer": "Demo answer"}],
    })

    another_session = start(monkeypatch, "http://multi-turn-chat.test")
    another_session.chat_input[0].set_value("continue").run()
    assert calls[-1][2]["history"] == []

    button(app, "Clear Chat").click().run()
    app.chat_input[0].set_value("continue").run()
    assert calls[-1][2]["history"] == []
