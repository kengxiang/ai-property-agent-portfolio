"""Property enquiry preview and confirmation contracts."""

import re
from typing import Literal

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator


class LeadDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    property_id: str = Field(min_length=1, max_length=20)
    customer_name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=8, max_length=25)
    message: str | None = Field(default=None, max_length=500)

    @field_validator("property_id", mode="before")
    @classmethod
    def normalize_property_id(cls, value):
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        """Accept human-entered numbers; store a normalized demo contact value."""
        if not re.fullmatch(r"\+?[0-9 ()\-\.]+", value):
            raise ValueError("Phone may contain digits, one leading +, spaces or separators")
        normalized = re.sub(r"[ ()\-\.]", "", value)
        if not re.fullmatch(r"\+?[0-9]{8,15}", normalized):
            raise ValueError("Phone must contain 8 to 15 digits")
        return normalized


class PreparedLead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: str
    property_name: str
    customer_name: str
    phone: str
    message: str | None


class LeadPrepareResponse(BaseModel):
    status: Literal["AWAITING_CONFIRMATION"] = "AWAITING_CONFIRMATION"
    draft: PreparedLead
    contact_purpose: str
    confirmation_required: Literal[True] = True
    notice: str = "Preview only. No lead has been saved or submitted."


class LeadConfirmRequest(LeadDraft):
    consent: Literal[True]
    idempotency_key: UUID4

    @field_validator("consent", mode="before")
    @classmethod
    def require_explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Consent must be true")
        return value


class LeadConfirmResponse(BaseModel):
    lead_id: UUID4
    property_id: str
    status: str
    confirmed: Literal[True] = True
