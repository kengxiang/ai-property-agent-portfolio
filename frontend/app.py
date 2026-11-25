import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException
from uuid import UUID, uuid4

import streamlit as st


API_BASE_URL = os.getenv(
    "PROPERTY_API_BASE_URL",
    "http://127.0.0.1:8000",
).rstrip("/")


# --------------------------------------------------
# Backend API Client
# --------------------------------------------------

class BackendHTTPError(RuntimeError):
    def __init__(self, status, detail):
        self.status = status
        self.detail = detail
        super().__init__(f"Backend HTTP {status}: {detail}")


class BackendConnectionError(RuntimeError):
    pass


def api_request(method, path, payload=None, params=None, *, base_url=API_BASE_URL, raw=None, token=None):
    """Send HTTP requests to our FastAPI backend."""

    url = f"{base_url}{path}"

    if params:
        url += "?" + urllib.parse.urlencode(params)

    data = raw

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    headers = {"Content-Type": "text/csv" if raw is not None else "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=180,
        ) as response:
            if getattr(response, "status", None) == 204:
                return True
            return json.load(response)

    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read())
            detail = body.get("detail", "Unknown error")
        except (ValueError, AttributeError, OSError, HTTPException):
            detail = "Unable to read error response"

        raise BackendHTTPError(error.code, detail) from error

    except (urllib.error.URLError, TimeoutError, ConnectionError, HTTPException) as error:
        raise BackendConnectionError(
            "Cannot connect to FastAPI. "
            "Please check that the backend is running."
        ) from error

    except ValueError as error:
        raise RuntimeError("Unable to read the backend response.") from error


def chat_with_agent(message, cloud_consent, history):
    """Send a user question to LangGraph through FastAPI."""

    turns = [
        {"question": item["question"][:4000], "answer": item["response"]["answer"][:4000]}
        for item in history
        if item.get("response", {}).get("answer")
    ]
    return api_request(
        method="POST",
        path="/agent/chat",
        payload={
            "message": message,
            "cloud_consent": cloud_consent,
            "history": turns[-6:],
        },
    )


def fetch_property_results(tool_calls):
    """Fetch structured property results when Agent used search_properties."""

    allowed_filters = {
        "location",
        "property_type",
        "min_price",
        "max_price",
        "max_mrt_distance_m",
        "near_mrt",
        "include_unavailable",
    }

    for call in tool_calls:

        if call.get("name") != "search_properties":
            continue

        arguments = call.get("args", {})

        if not isinstance(arguments, dict):
            return []

        params = {}

        for key, value in arguments.items():

            if key not in allowed_filters or value is None:
                continue

            if isinstance(value, bool):
                value = str(value).lower()

            params[key] = value

        results = api_request(
            method="GET",
            path="/properties",
            params=params,
        )

        return results if isinstance(results, list) else []

    return []


@st.cache_data(ttl=60, show_spinner=False)
def load_enquiry_properties(api_base_url):
    properties = api_request("GET", "/properties", base_url=api_base_url)
    if not isinstance(properties, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("property_id"), str)
        or not isinstance(item.get("name"), str)
        or type(item.get("price_myr")) is not int
        for item in properties
    ):
        raise RuntimeError("Unexpected property listing response.")
    return {
        item["property_id"]: f"{item['property_id']} — {item['name']} — RM{item['price_myr']:,}"
        for item in properties
    }


def enquiry_errors(error, *, confirming=False):
    if error.status == 404:
        return ["This property could not be found. Please select another property."]
    if error.status != 422:
        if confirming:
            if error.status == 409:
                return ["This confirmation key was already used with different details."]
            if error.status == 503:
                return ["The backend is temporarily unavailable. Retry this enquiry using the same details."]
            return ["The backend could not confirm the result. Retry this enquiry using the same details."]
        return ["Unable to review your enquiry. Please try again later."]

    messages = {
        "property_id": "Please select a property.",
        "customer_name": "Please enter your name (2–100 characters).",
        "phone": "Please enter a valid phone number with 8–15 digits.",
        "message": "Please keep your enquiry message to 500 characters or fewer.",
    }
    fields = set()
    if isinstance(error.detail, list):
        for issue in error.detail:
            location = issue.get("loc") if isinstance(issue, dict) else None
            if isinstance(location, list) and location and isinstance(location[-1], str):
                fields.add(location[-1])
    return [message for field, message in messages.items() if field in fields] or [
        "Please check your enquiry details."
    ]


def validate_enquiry_preview(preview, property_id):
    if (
        not isinstance(preview, dict)
        or preview.get("status") != "AWAITING_CONFIRMATION"
        or preview.get("confirmation_required") is not True
        or any(not isinstance(preview.get(key), str) or not preview[key].strip()
               for key in ("contact_purpose", "notice"))
    ):
        raise RuntimeError("Unexpected enquiry preview.")

    draft = preview.get("draft")
    if (
        not isinstance(draft, dict)
        or draft.get("property_id") != property_id
        or any(not isinstance(draft.get(key), str) or not draft[key].strip()
               for key in ("property_name", "customer_name", "phone"))
        or "message" not in draft
        or (draft["message"] is not None and not isinstance(draft["message"], str))
    ):
        raise RuntimeError("Unexpected enquiry draft.")


def reset_enquiry():
    st.session_state.lead_preview = None
    st.session_state.lead_consent = False
    st.session_state.lead_idempotency_key = None
    st.session_state.lead_confirmation_result = None
    st.session_state.lead_confirmation_errors = []


def edit_enquiry():
    draft = st.session_state.lead_preview["draft"]
    reset_enquiry()
    st.session_state.lead_property = draft["property_id"]
    st.session_state.lead_name = draft["customer_name"]
    st.session_state.lead_phone = draft["phone"]
    st.session_state.lead_message = draft["message"] or ""


def confirm_enquiry():
    preview = st.session_state.get("lead_preview")
    key = st.session_state.get("lead_idempotency_key")
    if not preview or not key or not st.session_state.get("lead_consent"):
        return
    if st.session_state.get("lead_confirmation_result") is not None:
        return

    draft = preview["draft"]
    payload = {field: draft[field] for field in ("property_id", "customer_name", "phone", "message")}
    payload.update(consent=True, idempotency_key=key)
    st.session_state.lead_confirmation_errors = []
    try:
        result = api_request("POST", "/leads/confirm", payload=payload)
        validate_confirmation(result, draft["property_id"])
    except BackendHTTPError as error:
        st.session_state.lead_confirmation_errors = enquiry_errors(error, confirming=True)
    except BackendConnectionError:
        st.session_state.lead_confirmation_errors = [
            "The backend did not respond or the connection failed. The submission status is unknown. "
            "Retry this enquiry to check safely."
        ]
    except RuntimeError:
        st.session_state.lead_confirmation_errors = [
            "The backend returned an unexpected response. The submission status is unknown. "
            "Retry this enquiry to check safely."
        ]
    else:
        st.session_state.lead_confirmation_result = result
        st.session_state.lead_preview = None
        st.session_state.lead_consent = False


def validate_confirmation(result, property_id):
    if (
        not isinstance(result, dict)
        or result.get("confirmed") is not True
        or result.get("property_id") != property_id
        or not isinstance(result.get("status"), str)
        or not result["status"].strip()
        or not isinstance(result.get("lead_id"), str)
    ):
        raise RuntimeError("Unexpected confirmation response.")
    try:
        lead_id = UUID(result["lead_id"])
    except ValueError as error:
        raise RuntimeError("Invalid Lead ID.") from error
    if lead_id.version != 4:
        raise RuntimeError("Invalid Lead ID.")


def show_enquiry_form():
    try:
        options = load_enquiry_properties(API_BASE_URL)
    except RuntimeError:
        st.error("Unable to load properties. Check the backend connection and try again.")
        options = {}
    else:
        if not options:
            st.info("No available properties were found.")

    if st.session_state.get("lead_property") not in options:
        st.session_state.pop("lead_property", None)
    with st.form("lead_enquiry"):
        property_id = st.selectbox(
            "Property",
            options=list(options),
            index=None,
            format_func=options.__getitem__,
            placeholder="Search by property ID or name",
            disabled=not options,
            key="lead_property",
        )
        name = st.text_input("Your Name", placeholder="Demo Alex", key="lead_name")
        phone = st.text_input("Phone Number", placeholder="+60123456789", key="lead_phone")
        message = st.text_area("Message (Optional)", key="lead_message")
        review = st.form_submit_button("Review Enquiry", type="primary", disabled=not options)

    if review:
        reset_enquiry()
        if property_id is None:
            st.error("Please select a property.")
        else:
            try:
                preview = api_request("POST", "/leads/prepare", payload={
                    "property_id": property_id,
                    "customer_name": name,
                    "phone": phone,
                    "message": message or None,
                })
            except BackendHTTPError as error:
                for message_text in enquiry_errors(error):
                    st.error(message_text)
            except RuntimeError:
                st.error("Unable to review your enquiry. Check the backend connection and try again.")
            else:
                try:
                    validate_enquiry_preview(preview, property_id)
                except RuntimeError:
                    st.error("The backend returned an unexpected enquiry preview. Please try again.")
                else:
                    st.session_state.lead_preview = preview
                    st.session_state.lead_idempotency_key = str(uuid4())
                    st.rerun()


def show_enquiry_review(preview):
    draft = preview["draft"]
    st.subheader("Review Your Enquiry")
    for label, field in (
        ("Property ID", "property_id"), ("Property Name", "property_name"),
        ("Customer Name", "customer_name"), ("Phone Number", "phone"), ("Message", "message"),
    ):
        st.text(f"{label}: {draft[field] or '—'}")
    st.text("Contact Purpose")
    st.text(preview["contact_purpose"])
    consent = st.checkbox(
        "I agree to submit my contact details for this property enquiry.",
        key="lead_consent",
    )
    st.info(preview["notice"])
    for message in st.session_state.get("lead_confirmation_errors", []):
        st.error(message)
    st.button("Confirm & Submit", type="primary", disabled=not consent, on_click=confirm_enquiry)
    st.button("Edit Enquiry", on_click=edit_enquiry)
    st.button("Cancel Enquiry", on_click=reset_enquiry)


def show_property_enquiry():
    st.header("Property Enquiry")
    st.caption("Use synthetic contact details for this demo.")
    result = st.session_state.get("lead_confirmation_result")
    preview = st.session_state.get("lead_preview")
    if result is not None:
        st.success("Enquiry Submitted Successfully")
        st.text(f"Lead Reference ID: {result['lead_id']}")
        st.text(f"Property ID: {result['property_id']}")
        st.text(f"Confirmation Status: {result['status']}")
        st.button("Start New Enquiry", on_click=reset_enquiry)
    elif preview is not None:
        show_enquiry_review(preview)
    else:
        show_enquiry_form()


# --------------------------------------------------
# Streamlit UI
# --------------------------------------------------

st.set_page_config(
    page_title="AI Property Agent",
    page_icon="🏠",
    layout="wide",
)

if os.getenv("ADMIN_INGESTION_ENABLED") == "1" and st.sidebar.toggle("Administration"):
    from frontend.admin import render_admin

    render_admin(api_request, BackendHTTPError, load_enquiry_properties.clear)
    st.stop()

if os.getenv("ADMIN_INGESTION_ENABLED") == "1" and st.sidebar.toggle("Staff Dashboard"):
    from frontend.staff import render_staff

    render_staff(api_request, BackendHTTPError)
    st.stop()

st.title("🏠 AI Property Agent")

st.caption(
    "AI-powered Malaysian property search "
    "with document-based RAG and citations."
)

st.info(
    "Demo environment: property listings and PDF "
    "documents are synthetic, not real market listings."
)


# --------------------------------------------------
# Sidebar
# --------------------------------------------------

with st.sidebar:

    st.header("⚙️ Settings")

    st.caption(f"Backend: {API_BASE_URL}")
    cloud_consent = st.toggle("Allow Gemini to process this chat if selected by an administrator",
                               help="Use synthetic questions and keep contact details out of chat.")

    if st.button("Check Backend Connection"):

        try:
            result = api_request(
                method="GET",
                path="/health",
            )

            st.success("Backend connected successfully.")

        except RuntimeError as error:
            st.error(str(error))

    if st.button("Clear Chat"):
        st.session_state.history = []
        st.rerun()

    st.divider()

    st.caption(
        "Recent chat turns are used for context in this session. "
        "Use Clear Chat to start over."
    )


# --------------------------------------------------
# Chat History
# --------------------------------------------------

if "history" not in st.session_state:
    st.session_state.history = []

show_property_enquiry()
st.divider()
st.header("AI Property Chat")


def show_response(response, properties=None):

    st.markdown(response.get("answer", "No answer returned."))

    # Display structured property results.
    if properties:
        st.subheader("🏘️ Property Results")

        rows = []

        for property_data in properties:
            rows.append({
                "ID": property_data.get("property_id"),
                "Property": property_data.get("name"),
                "Location": property_data.get("display_location"),
                "Type": property_data.get("property_type"),
                "Price (RM)": property_data.get("price_myr"),
                "Bedrooms": property_data.get("bedrooms"),
                "MRT (m)": property_data.get("mrt_distance_m"),
            })

        st.dataframe(
            rows,
            hide_index=True,
            use_container_width=True,
        )

    # Display RAG sources.
    sources = response.get("sources", [])

    if sources:
        st.subheader("📄 Retrieved Sources")

        for source in sources:
            st.markdown(
                f"**{source['filename']}** — "
                f"Page {source['page']} "
                f"({source['property_id']})"
            )

    # Technical details useful for demonstrations.
    with st.expander("🔧 Agent Execution Details"):

        st.write(
            "Model:",
            response.get("model", "Unknown"),
        )

        st.write(
            "Latency:",
            response.get("latency_ms", "N/A"),
            "ms",
        )

        st.json(response.get("tool_calls", []))


for item in st.session_state.history:

    with st.chat_message("user"):
        st.markdown(item["question"])

    with st.chat_message("assistant"):

        if "error" in item:
            st.error(item["error"])
        else:
            show_response(
                item["response"],
                item.get("properties"),
            )


# --------------------------------------------------
# New User Message
# --------------------------------------------------

if prompt := st.chat_input(
    "Ask about properties, prices, facilities or house rules..."
):

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):

        with st.spinner("AI Agent is working..."):

            try:

                response = chat_with_agent(
                    message=prompt,
                    cloud_consent=cloud_consent,
                    history=st.session_state.history,
                )

                # Property cards are an additional API lookup.
                # Failure here should not discard the AI answer.
                properties = []

                try:
                    properties = fetch_property_results(
                        response.get("tool_calls", [])
                    )
                except RuntimeError as error:
                    st.warning(
                        f"Property table unavailable: {error}"
                    )

                show_response(response, properties)

                st.session_state.history.append({
                    "question": prompt,
                    "response": response,
                    "properties": properties,
                })

            except RuntimeError as error:

                st.error(str(error))

                st.session_state.history.append({
                    "question": prompt,
                    "error": str(error),
                })
