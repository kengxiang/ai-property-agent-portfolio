"""Derive property availability filters from user text before the tool runs."""

import re

from backend.schemas.property import AvailabilityStatus, Property, PropertyType


INCLUDE_ALL = re.compile(
    r"\b(?:includ(?:e|ing)|show even) (?:the )?(?:unavailable|all (?:availability )?statuses)\b"
    r"|\bregardless of (?:availability|status)\b|包括(?:不可用|已售和已预留)|不限状态",
    re.IGNORECASE,
)
STATUS_REQUEST = re.compile(
    r"\b(?:available|reserved|sold|unavailable)"
    r"(?:\s*(?:,|and|or)\s*(?:available|reserved|sold|unavailable))*"
    r"\s+(?:(?:property|listing)\s+)?(?:IDs?|properties|listings|homes|condos|apartments|units|ones)\b"
    r"|\b(?:with (?:availability )?status(?: of| is)?|(?:includ(?:e|ing)|show) also|includ(?:e|ing))"
    r"\s+(?:the\s+)?(?:available|reserved|sold|unavailable)\b"
    r"|(?:已预留|已預留|已售)(?:的)?(?:房产|房產|房源|单位|單位)",
    re.IGNORECASE,
)
STATUS_WORDS = {
    AvailabilityStatus.AVAILABLE: r"\bavailable\b|可用",
    AvailabilityStatus.RESERVED: r"\breserved\b|已预留|已預留",
    AvailabilityStatus.SOLD: r"\bsold\b|已售",
}
EXCLUSION = re.compile(
    r"\b(?:exclude|excluding|without|except|not|no|don't include|do not include)\s+"
    r"(?:the\s+)?(?:any\s+)?(?:reserved(?:\s*(?:and|or|,)\s*sold)?|"
    r"sold(?:\s*(?:and|or|,)\s*reserved)?|unavailable|available)\b",
    re.IGNORECASE,
)


def apply_search_scope(args, question: str) -> None:
    """Replace model-supplied availability with the user's explicit scope."""
    question = EXCLUSION.sub("", question)
    include_all = bool(INCLUDE_ALL.search(question))
    scope_text = " ".join(match.group() for match in STATUS_REQUEST.finditer(question))
    statuses = [
        status.value for status, pattern in STATUS_WORDS.items()
        if re.search(pattern, scope_text, re.IGNORECASE)
    ]
    if re.search(r"\bunavailable\b", scope_text, re.IGNORECASE) and not include_all:
        statuses = [AvailabilityStatus.RESERVED.value, AvailabilityStatus.SOLD.value]
    if re.search(r"\binclud(?:e|ing)\b", question, re.IGNORECASE) and statuses:
        if AvailabilityStatus.AVAILABLE.value not in statuses:
            statuses.insert(0, AvailabilityStatus.AVAILABLE.value)
    if args.get("include_unavailable") is not None or include_all:
        args["include_unavailable"] = include_all
    args.pop("availability_statuses", None)
    if not include_all and any(status != AvailabilityStatus.AVAILABLE.value for status in statuses):
        args["availability_statuses"] = statuses


PROPERTY_TYPES = {
    PropertyType.CONDO: r"\b(?:condos?|condominiums?)\b",
    PropertyType.APARTMENT: r"\bapartments?\b",
    PropertyType.LANDED: r"\blanded\s+(?:homes?|properties|houses?|listings?|IDs?)\b",
}
TYPE_EXCLUSION = re.compile(r"\b(?:exclude|excluding|without|except|not|no)\s+(?:any\s+)?(?:condos?|condominiums?|apartments?|landed)\b", re.IGNORECASE)


def apply_explicit_property_type(args, question: str) -> None:
    """Keep an expressly named single type when the model substitutes another."""
    if TYPE_EXCLUSION.search(question):
        return
    types = [
        property_type.value for property_type, pattern in PROPERTY_TYPES.items()
        if re.search(pattern, question, re.IGNORECASE)
    ]
    model_type = Property.normalize_property_type(args.get("property_type"))
    if len(types) == 1 and model_type in list(PropertyType):
        args["property_type"] = types[0]
