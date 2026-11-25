from streamlit.testing.v1 import AppTest


def test_staff_sections_navigation_and_task_controls():
    app = AppTest.from_string("""
import streamlit as st
from frontend.staff import render_staff

class APIError(RuntimeError):
    status = 401

def request(method, path, payload=None, token=None, **kwargs):
    if path == "/staff/login":
        return {"access_token": "demo"}
    if token != "demo":
        raise APIError()
    current = st.session_state.get("demo_task", "PENDING")
    task = {"task_id": "task-a", "status": current}
    event = {
        "event_id": "event-a", "aggregate_id": "lead-a", "delivery_status": "FAILED",
        "attempts": 5, "property_id": "P001", "task_id": None,
        "error_summary": "Webhook returned HTTP 403. The request was rejected.",
        "last_failure_at": None, "requires_attention": True, "attention_reason": "Task missing",
    }
    lead = {"lead_id": "lead-a", "follow_up_status": "AUTOMATION_FAILED",
            "follow_up_note": "Task missing", "event_id": "event-a", "task_id": None}
    if path == "/staff/tasks/task-a" and method == "PATCH":
        st.session_state.demo_task = payload["status"]
        return task
    return {
        "/staff/logout": True,
        "/staff/overview": {"total_leads": 1, "tasks": {state: int(state == current)
                              for state in ("PENDING", "IN_PROGRESS", "COMPLETED")},
                            "delivered": 0, "failed": 1, "requires_attention": 1,
                            "overall_event_delivery_rate": 0},
        "/staff/leads": [lead], "/staff/leads/lead-a": lead,
        "/staff/events": [event], "/staff/events/event-a": event, "/staff/tasks": [task],
    }[path]

render_staff(request, APIError)
""").run()

    def click(label):
        next(button for button in app.button if button.label == label).click().run()
        assert not app.exception

    assert not app.metric
    click("Sign in")
    assert next(m.value for m in app.metric if m.label == "Overall Event Delivery Rate") == "0.0%"
    app.radio[0].set_value("Leads").run()
    assert app.dataframe[0].value["follow_up_status"].tolist() == ["Automation failed"]
    app.selectbox[0].set_value("lead-a").run()
    assert any(e.label == "Investigate related event" for e in app.expander)
    app.radio[0].set_value("Automation").run()
    assert app.dataframe[0].value["Attention"].tolist() == ["REQUIRES ATTENTION"]
    app.selectbox[0].set_value("event-a").run()
    assert any("request was rejected" in t.value for t in app.text)
    app.radio[0].set_value("Follow-up tasks").run()
    app.selectbox[0].set_value("task-a").run()
    click("Start task")
    click("Complete task")
    app.run()
    assert any("COMPLETED" in t.value for t in app.text)
    app.radio[0].set_value("Overview").run()
    assert next(m.value for m in app.metric if m.label == "Completed Tasks") == "1"
    click("Sign out")
    assert not app.metric
