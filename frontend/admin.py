"""Sandbox admin panel. FastAPI authorizes every operation."""

import hashlib

import streamlit as st


FIELD_LABELS = {
    "property_id": "Property ID", "name": "Property name", "location_code": "Location code",
    "display_location": "Display location", "property_type": "Property type",
    "price_myr": "Property price", "bedrooms": "Bedroom count", "bathrooms": "Bathroom count",
    "mrt_distance_m": "MRT distance", "availability_status": "Availability",
    "description": "Description",
}


def validation_messages(detail):
    """Translate the API's validation detail without displaying raw inputs."""
    if isinstance(detail, str):
        return [detail]
    if isinstance(detail, list):
        return [message for issue in detail for message in validation_messages(issue)]
    if not isinstance(detail, dict):
        return ["The request was rejected."]
    prefix = f"Row {detail['row']}: " if detail.get("row") is not None else ""
    if conflicts := detail.get("conflicts"):
        return [
            (f"Row {item['row']}: " if item.get("row") is not None else "")
            + f"Property ID {item['property_id']} already exists. No properties were created."
            for item in conflicts
        ]
    if columns := detail.get("missing_columns"):
        return [prefix + "Missing required columns: " + ", ".join(FIELD_LABELS.get(c, c) for c in columns) + "."]
    if fields := detail.get("fields"):
        return [prefix + message for message in validation_messages(fields)]
    if message := detail.get("message"):
        messages = {
            "Duplicate property ID in CSV": "Property ID appears more than once in the CSV.",
            "Invalid CSV columns": "Check for unexpected or repeated column names.",
            "Incorrect column count": "This row has a different number of values than the header.",
            "Malformed CSV": "The CSV is malformed. Check the delimiter and quotation marks.",
        }
        return [prefix + messages.get(message, message)]

    location = detail.get("loc", [])
    field = detail.get("field") or (location[-1] if location else "")
    label = FIELD_LABELS.get(field, field or "This value")
    kind = detail.get("type")
    if kind in {"missing", "string_too_short"} or (kind == "string_type" and field in FIELD_LABELS):
        message = f"{label} is required."
    elif kind == "greater_than_equal":
        message = f"{label} cannot be negative."
    elif kind == "less_than_equal":
        message = f"{label} is too large."
    elif kind == "string_too_long":
        message = f"{label} is too long."
    elif kind in {"int_type", "int_parsing", "int_from_float"}:
        message = f"{label} must be a whole number."
    elif field == "property_type" and kind == "enum":
        message = "Choose a valid property type: CONDO, APARTMENT, or LANDED."
    elif field == "location_code" and kind == "enum":
        message = "Enter a valid location such as KL, Selangor, or Johor Bahru."
    elif field == "availability_status" and kind == "enum":
        message = "Choose AVAILABLE, RESERVED, or SOLD."
    elif kind == "extra_forbidden":
        message = f"{label} is not an accepted field."
    else:
        message = detail.get("msg") or f"Check {label}."
    return [prefix + message]


def clear_preview(state):
    state.pop("admin_preview", None)


def render_admin(api_request, http_error, clear_properties):
    st.header("Property Administration")
    state = st.session_state
    for message in state.pop("admin_pending_errors", []):
        st.error(message)
    token = state.get("admin_token")
    if not token:
        with st.form("admin_login", clear_on_submit=True):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Sign in")
        if submit:
            try:
                result = api_request("POST", "/admin/login", {"username": username, "password": password})
            except http_error as error:
                st.error("Invalid credentials or administrator access unavailable."
                         if error.status in (401, 403) else str(error.detail))
            except RuntimeError:
                st.error("The administration service is unavailable.")
            else:
                state.admin_token = result["access_token"]
                st.rerun()
        return

    def call(path, *, method="POST", defer_error=False, **kwargs):
        try:
            return api_request(method, path, token=token, **kwargs)
        except http_error as error:
            if error.status in (401, 403):
                state.pop("admin_token", None)
                clear_preview(state)
                messages = ["Administrator session expired or no longer authorized. Sign in again."]
            else:
                messages = validation_messages(error.detail)
        except RuntimeError:
            messages = ["The administration service is unavailable. Check the result before retrying."]
        if defer_error:
            state.admin_pending_errors = messages
        else:
            for message in messages:
                st.error(message)
        return None

    if st.button("Logout"):
        # The API returns an empty 204 response after revocation.
        if call("/admin/logout") is not None or state.get("admin_token") != token:
            state.pop("admin_token", None)
            clear_preview(state)
            state.admin_csv_version = state.get("admin_csv_version", 0) + 1
            state.admin_property_version = state.get("admin_property_version", 0) + 1
            st.rerun()
        return

    if st.toggle("Model settings"):
        model = call("/admin/model", method="GET")
        if model:
            st.text(f"Configured: {model['configured_provider']} · Active: {model['active_provider']}")
            st.text(f"Gemini: {model['gemini_status']}")
            selected = st.selectbox("Primary provider", ("local", "cloud"),
                                    index=int(model["configured_provider"] == "cloud"),
                                    format_func=lambda value: "Qwen" if value == "local" else "Gemini")
            if st.button("Save provider") and call("/admin/model", method="PUT", payload={"provider": selected}):
                st.rerun()
        return

    if message := state.pop("admin_success", None):
        st.success(message)

    st.subheader("Add New Property")
    with st.form(f"admin_property_{state.get('admin_property_version', 0)}"):
        property_id = st.text_input("Property ID")
        name = st.text_input("Property name")
        location_code = st.text_input("Location code or alias")
        display_location = st.text_input("Display location")
        property_type = st.selectbox("Property type", ["CONDO", "APARTMENT", "LANDED"], index=None)
        price_myr = st.number_input("Price (RM)", min_value=0, step=1, value=None)
        bedrooms = st.number_input("Bedrooms", min_value=0, step=1, value=None)
        bathrooms = st.number_input("Bathrooms", min_value=0, step=1, value=None)
        mrt_distance_m = st.text_input("MRT distance in metres (optional)")
        availability_status = st.selectbox("Availability", ["AVAILABLE", "RESERVED", "SOLD"], index=None)
        description = st.text_area("Description")
        create = st.form_submit_button("Create Property")
    if create:
        result = call("/admin/properties", payload={
            "property_id": property_id, "name": name, "location_code": location_code,
            "display_location": display_location, "property_type": property_type,
            "price_myr": price_myr, "bedrooms": bedrooms, "bathrooms": bathrooms,
            "mrt_distance_m": mrt_distance_m, "availability_status": availability_status,
            "description": description,
        })
        if result is not None:
            clear_properties()
            state.admin_property_version = state.get("admin_property_version", 0) + 1
            state.admin_success = f"Created property {result['property_id']}."
            st.rerun()

    st.subheader("Upload Property CSV")
    upload = st.file_uploader(
        "Choose UTF-8 CSV (up to 1 MiB)", type=["csv"],
        key=f"admin_csv_{state.get('admin_csv_version', 0)}",
    )
    if upload is None:
        clear_preview(state)
        return
    if upload.size > 1024 * 1024:
        clear_preview(state)
        st.error("CSV exceeds 1 MiB.")
        return
    raw = upload.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    preview = state.get("admin_preview")
    if preview and preview["digest"] != digest:
        clear_preview(state)
        preview = None
    if st.button("Preview CSV"):
        if preview:
            preview["ready"] = False
        result = call("/admin/properties/csv", params={"preview": "true"}, raw=raw)
        if result is not None:
            preview = state.admin_preview = {
                "digest": digest, "rows": result["properties"], "ready": True,
            }
    if preview and state.get("admin_preview") is not None:
        st.dataframe(preview["rows"], hide_index=True)
        if not preview["ready"]:
            st.caption("Run Preview CSV again before confirming.")
        if st.button("Confirm Import", disabled=not preview["ready"]) and preview["ready"]:
            preview["ready"] = False
            result = call("/admin/properties/csv", raw=raw, defer_error=True)
            if result is not None:
                clear_properties()
                clear_preview(state)
                state.admin_csv_version = state.get("admin_csv_version", 0) + 1
                state.admin_success = f"Created {result['count']} properties."
            st.rerun()
