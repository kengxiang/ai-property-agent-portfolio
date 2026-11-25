"""Employee dashboard backed exclusively by authenticated FastAPI requests."""

import streamlit as st


EVENT_STATUS = {
    "PENDING": "Queued", "PROCESSING": "Sending", "RETRY": "Waiting to retry",
    "DELIVERED": "Delivered", "FAILED": "Stopped after delivery failures",
}


FOLLOW_UP_STATUS = {
    "PENDING": "Pending", "IN_PROGRESS": "In progress", "COMPLETED": "Completed",
    "AUTOMATION_FAILED": "Automation failed", "NO_TASK": "No task",
}


def show_details(data):
    for key, value in data.items():
        st.text(f"{key.replace('_', ' ').title()}: {value if value is not None else '—'}")


def show_event(event):
    if event["requires_attention"]:
        st.warning(f"REQUIRES ATTENTION — {event['attention_reason']}")
    elif event["attention_reason"]:
        st.warning(event["attention_reason"])
    details = {key: value for key, value in event.items() if key not in ("requires_attention", "attention_reason")}
    details["delivery_status"] = EVENT_STATUS.get(event["delivery_status"], event["delivery_status"])
    details["last_failure_at"] = event["last_failure_at"] or "Not recorded"
    show_details(details)


def render_staff(request, http_error):
    st.title("Staff Dashboard")
    state = st.session_state
    if not state.get("staff_token"):
        with st.form("staff_login", clear_on_submit=True):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in"):
                try:
                    result = request("POST", "/staff/login", {"username": username, "password": password})
                    state.staff_token = result["access_token"]
                except RuntimeError:
                    st.error("Sign-in failed. Check your credentials or try again later.")
                else:
                    st.rerun()
        return

    def call(method, path, **kwargs):
        try:
            return request(method, path, token=state.staff_token, **kwargs)
        except http_error as error:
            if error.status in (401, 403):
                state.pop("staff_token", None)
                st.rerun()
            st.error("Task status changed. Refresh the dashboard and try again." if error.status == 409
                     else "The dashboard request could not be completed.")
        except RuntimeError:
            st.error("The dashboard is unavailable.")

    if st.button("Sign out", key="staff_logout"):
        if call("POST", "/staff/logout"):
            state.pop("staff_token", None)
            st.rerun()

    section = st.radio("Section", ["Overview", "Leads", "Follow-up tasks", "Automation"], horizontal=True)
    if section == "Overview":
        metrics = call("GET", "/staff/overview")
        if metrics is None:
            return
        st.subheader("Business Overview")
        columns = st.columns(4)
        columns[0].metric("Total Leads", metrics["total_leads"])
        for column, status in zip(columns[1:], ("PENDING", "IN_PROGRESS", "COMPLETED")):
            column.metric(FOLLOW_UP_STATUS[status] + " Tasks", metrics["tasks"][status])
        st.subheader("Automation Health")
        columns = st.columns(4)
        columns[0].metric("Delivered Events", metrics["delivered"])
        columns[1].metric("Failed Events", metrics["failed"])
        columns[2].metric("Requires Attention", metrics["requires_attention"])
        rate = metrics["overall_event_delivery_rate"]
        columns[3].metric("Overall Event Delivery Rate", f"{rate:.1%}" if rate is not None else "N/A")
        st.caption("DELIVERED ÷ (DELIVERED + FAILED). Includes historical terminal events. Queued, sending and retrying events are excluded.")
        return

    endpoint = {"Leads": "leads", "Follow-up tasks": "tasks", "Automation": "events"}[section]
    page = st.number_input("Page", min_value=1, max_value=4001, step=1, key=f"staff_page_{endpoint}")
    rows = call("GET", f"/staff/{endpoint}", params={"limit": 25, "offset": (page - 1) * 25})
    if not rows:
        return
    if section == "Automation":
        summary = []
        for row in rows:
            summary.append({
                "Event ID": row["event_id"], "Lead ID": row["aggregate_id"],
                "Property": row["property_id"], "Delivery": EVENT_STATUS.get(row["delivery_status"], row["delivery_status"]),
                "Attempts": row["attempts"], "Follow-up Task": row["task_id"] or "Missing",
                "Attention": "REQUIRES ATTENTION" if row["requires_attention"] else "—",
                "Failure reason": row["error_summary"], "Last failure": row["last_failure_at"] or "Not recorded",
            })
        st.dataframe(summary, hide_index=True)
        event_id = st.selectbox("Investigate event", [row["event_id"] for row in rows], index=None)
        if event_id and (details := call("GET", f"/staff/events/{event_id}")):
            show_event(details)
        return
    if section == "Leads":
        st.dataframe([{**row, "follow_up_status": FOLLOW_UP_STATUS[row["follow_up_status"]]} for row in rows],
                     hide_index=True, column_config={"follow_up_status": "Follow-up Status",
                                                     "follow_up_note": "Follow-up note"})
        lead_id = st.selectbox("Inspect lead", [row["lead_id"] for row in rows], index=None)
        if lead_id and (details := call("GET", f"/staff/leads/{lead_id}")):
            if details["follow_up_status"] == "AUTOMATION_FAILED":
                st.warning("The automation stopped before a valid follow-up task was confirmed.")
            show_details(details)
            if details["task_id"]:
                with st.expander("View related task"):
                    task = call("GET", f"/staff/tasks/{details['task_id']}")
                    if task:
                        show_details(task)
            elif details["event_id"]:
                with st.expander("Investigate related event"):
                    event = call("GET", f"/staff/events/{details['event_id']}")
                    if event:
                        show_event(event)
        return

    st.dataframe(rows, hide_index=True)
    task_id = st.selectbox("Inspect task", [row["task_id"] for row in rows], index=None)
    if not task_id:
        return
    task = next(row for row in rows if row["task_id"] == task_id)
    st.text(f"Status: {task['status']}")
    transition = {"PENDING": ("Start task", "IN_PROGRESS"), "IN_PROGRESS": ("Complete task", "COMPLETED")}
    if task["status"] in transition:
        label, target = transition[task["status"]]
        if st.button(label) and call("PATCH", f"/staff/tasks/{task_id}", payload={"status": target}):
            st.rerun()
