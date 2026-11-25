"""Conservative evidence fallback for unsupported transaction terms."""

import json
import re

from backend.schemas.property import Property


TRANSACTION_TERMS = re.compile(
    r"\b(?:rent(?:al|als|ing|ed|s)?|lease(?:d|s)?|leasing|tenancy|purchase(?:d|s)?|"
    r"for sale|sale price|selling price|(?:buy|sell)(?:ing)?|monthly payment)\b"
    r"|出租|租赁|租賃|租金|出售|购买|購買",
    re.IGNORECASE,
)


def guard_transaction_terms(question, answer, tool_results):
    """Use catalog fields when prose would infer sale or rental from status."""
    if not (TRANSACTION_TERMS.search(question) or TRANSACTION_TERMS.search(answer)):
        return answer

    properties = {}
    for result in tool_results:
        if result.name not in {"get_property_details", "search_properties"} or not result.content:
            continue
        records = json.loads(result.content)
        if records is None:
            continue
        for record in records if isinstance(records, list) else [records]:
            property = Property.model_validate(record)
            properties[property.property_id] = property

    uncertainty = "I cannot confirm sale or rental terms from the verified information."
    summary = [
        f"- {item.property_id} ({item.name}): recorded price RM{item.price_myr:,}, "
        f"status {item.availability_status.value}, {item.bedrooms} bedrooms, "
        f"{item.bathrooms} bathrooms, MRT distance "
        f"{f'{item.mrt_distance_m:,} m' if item.mrt_distance_m is not None else 'not recorded'}."
        for item in properties.values()
    ]
    if not summary:
        return uncertainty
    return "\n".join([*summary, "", uncertainty])
