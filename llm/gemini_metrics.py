"""Collect safe, provider-reported metadata without retaining prompts or tool data."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re

import httpx
from google.genai.errors import APIError
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import (
    ModelAuthenticationError, ModelNotFoundError, ModelPermissionDeniedError, ModelRateLimitError,
)


def failure(error):
    if isinstance(error, (ModelAuthenticationError, ModelNotFoundError, ModelPermissionDeniedError)):
        return 503, "provider_configuration"
    if isinstance(error, ModelRateLimitError):
        return 429, "rate_limit"
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return 504, "provider_timeout"
    if isinstance(error, APIError):
        if error.code in (401, 403, 404):
            return 503, "provider_configuration"
        if error.code == 429:
            return 429, "rate_limit"
        if error.code in (408, 504):
            return 504, "provider_timeout"
    return 503, "provider_error"


def retry_after_seconds(value):
    """Parse an HTTP Retry-After hint without returning the raw header."""
    if not value:
        return None
    if re.fullmatch(r"[0-9]{1,12}", value):
        return int(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return max(0, math.ceil((date - datetime.now(timezone.utc)).total_seconds()))
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None


def error_metadata(error):
    """Unwrap SDK exceptions and allow only status, delay and quota categories."""
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, APIError):
            break
        current = current.__cause__
    if not isinstance(current, APIError) or not isinstance(current.code, int):
        return {}
    meta = {"upstream_status": current.code}
    response = current.response
    delay = retry_after_seconds(response.headers.get("Retry-After")) if response is not None else None
    body = current.details
    details = body.get("error", {}).get("details", []) if isinstance(body, dict) and isinstance(body.get("error"), dict) else []
    for item in details if isinstance(details, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
            raw = item.get("retryDelay")
            if isinstance(raw, str) and re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,9})?s", raw):
                delay = max(delay or 0, math.ceil(float(raw[:-1])))
        if item.get("@type") == "type.googleapis.com/google.rpc.QuotaFailure":
            violations = item.get("violations", [])
            for violation in violations if isinstance(violations, list) else []:
                quota_id = violation.get("quotaId", "") if isinstance(violation, dict) else ""
                if not isinstance(quota_id, str) or meta.get("quota_scope") in {"daily", "billing"}:
                    continue
                quota_id = quota_id.casefold()
                if "perday" in quota_id:
                    meta["quota_scope"] = "daily"
                elif "perminute" in quota_id:
                    if "token" in quota_id:
                        meta["quota_scope"] = "tokens_per_minute"
                    elif "request" in quota_id:
                        meta["quota_scope"] = "requests_per_minute"
        if item.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo" and item.get("reason") in {
            "BILLING_DISABLED", "BILLING_NOT_ACTIVE", "SPEND_CAP_EXCEEDED",
        }:
            meta["quota_scope"] = "billing"
    if delay is not None:
        meta["retry_after_seconds"] = delay
    return meta


class GeminiUsage(BaseCallbackHandler):
    def __init__(self):
        self.calls = 0
        self.reported = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.model_versions = set()

    def on_llm_error(self, error, **kwargs):
        self.calls += 1

    def on_llm_end(self, response, **kwargs):
        self.calls += 1
        message = response.generations[0][0].message
        if version := message.response_metadata.get("model_name"):
            self.model_versions.add(version)
        if usage := message.usage_metadata:
            self.reported += 1
            self.input_tokens += usage["input_tokens"]
            self.output_tokens += usage["output_tokens"]

    def summary(self):
        return {
            "calls": self.calls, "calls_with_usage": self.reported,
            "input_tokens": self.input_tokens if self.reported else None,
            "output_tokens": self.output_tokens if self.reported else None,
            "model_versions": sorted(self.model_versions),
        }
