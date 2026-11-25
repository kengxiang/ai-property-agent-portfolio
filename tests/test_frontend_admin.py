import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from frontend.admin import validation_messages


APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setenv("ADMIN_INGESTION_ENABLED", "1")
    calls, results, uploads = [], {}, {}

    def urlopen(request, timeout):
        url = urlsplit(request.full_url)
        key = url.path + ("?" + url.query if url.query else "")
        calls.append((key, request.data))
        result = results[key]
        if isinstance(result, HTTPError):
            raise result
        if result == 204:
            return Reply(status=204)
        return Reply(json.dumps(result).encode())

    class Reply(io.BytesIO):
        def __init__(self, content=b"", status=200):
            super().__init__(content)
            self.status = status

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(st, "file_uploader", lambda *args, key, **kwargs: uploads.get(key))
    results.update({"/admin/login": {"access_token": "demo"}, "/admin/logout": 204, "/properties": []})
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    app.toggle[0].set_value(True).run()
    app.text_input[0].set_value("admin")
    app.text_input[1].set_value("demo")
    click(app, "Sign in")
    return app, calls, results, uploads


def click(app, label):
    next(widget for widget in app.button if widget.label == label).click().run()
    assert not app.exception


def failure(status, detail):
    return HTTPError("", status, "", {}, io.BytesIO(json.dumps({"detail": detail}).encode()))


def test_property_failure_preserves_values_and_success_clears(admin):
    app, calls, results, _ = admin
    app.text_input[0].set_value("P206-DEMO")
    app.text_input[1].set_value("Demo residence")
    app.number_input[0].set_value(100)
    results["/admin/properties"] = failure(422, [{"loc": ["body", "location_code"], "type": "enum"}])
    click(app, "Create Property")
    assert "valid location" in app.error[0].value
    assert app.text_input[0].value == "P206-DEMO"
    assert app.number_input[0].value == 100
    results["/admin/properties"] = {"property_id": "P206-DEMO"}
    click(app, "Create Property")
    assert app.success[0].value == "Created property P206-DEMO."
    assert all(not field.value for field in [*app.text_input, *app.text_area])
    assert all(field.value is None for field in [*app.number_input, *app.selectbox])
    assert app.session_state.admin_token == "demo"
    app.run()
    assert sum(path == "/admin/properties" for path, _ in calls) == 2
    click(app, "Logout")
    assert "admin_token" not in app.session_state
    assert any(widget.label == "Sign in" for widget in app.button)


def test_csv_failure_disarms_preview_and_success_clears_upload(admin):
    app, calls, results, uploads = admin
    preview, confirm = "/admin/properties/csv?preview=true", "/admin/properties/csv"
    uploads["admin_csv_0"] = SimpleNamespace(size=1, getvalue=lambda: b"A")
    results[preview] = failure(422, [{"row": 3, "fields": [{"field": "price_myr", "type": "greater_than_equal"}]}])
    app.run()
    click(app, "Preview CSV")
    assert app.error[0].value == "Row 3: Property price cannot be negative."
    assert not any(widget.label == "Confirm Import" for widget in app.button)

    results[preview] = {"properties": [{"property_id": "NEW-A"}]}
    click(app, "Preview CSV")
    uploads["admin_csv_0"] = SimpleNamespace(size=1, getvalue=lambda: b"B")
    app.run()
    assert not any(widget.label == "Confirm Import" for widget in app.button)
    uploads["admin_csv_0"] = SimpleNamespace(size=1, getvalue=lambda: b"A")
    app.run()
    assert not any(widget.label == "Confirm Import" for widget in app.button)
    click(app, "Preview CSV")
    results[confirm] = failure(409, {"conflicts": [{"row": 2, "property_id": "NEW-A"}]})
    click(app, "Confirm Import")
    assert app.error and app.dataframe
    assert next(widget for widget in app.button if widget.label == "Confirm Import").disabled
    click(app, "Preview CSV")
    results[confirm] = {"count": 1}
    click(app, "Confirm Import")
    assert app.success[0].value == "Created 1 properties."
    assert app.session_state.admin_csv_version == 1
    assert not app.dataframe and not any(widget.label == "Confirm Import" for widget in app.button)
    app.run()
    assert [body for path, body in calls if path == confirm] == [b"A", b"A"]


def test_readable_validation_details():
    assert validation_messages([{"row": 4, "fields": [
        {"field": "name", "type": "missing"},
        {"field": "property_type", "type": "enum"},
    ]}]) == ["Row 4: Property name is required.", "Row 4: Choose a valid property type: CONDO, APARTMENT, or LANDED."]
    assert validation_messages({"row": 5, "message": "Duplicate property ID in CSV"}) == [
        "Row 5: Property ID appears more than once in the CSV.",
    ]
    assert validation_messages({"conflicts": [{"row": None, "property_id": "P001"}]}) == [
        "Property ID P001 already exists. No properties were created.",
    ]
