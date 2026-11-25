import csv
import pytest

from pydantic import ValidationError
from backend.schemas.property import Property


@pytest.fixture
def rows():
    with open(
        "data/synthetic_properties.csv",
        newline="",
        encoding="utf-8"
    ) as file:
        return list(csv.DictReader(file))


def test_all_properties_valid(rows):
    properties = [Property.model_validate(row) for row in rows]

    assert len(properties) == 24


def test_unique_property_ids(rows):
    ids = [row["property_id"] for row in rows]

    assert len(ids) == len(set(ids))


def test_negative_price_rejected(rows):
    data = rows[0].copy()
    data["price_myr"] = "-100"

    with pytest.raises(ValidationError):
        Property.model_validate(data)


def test_invalid_property_type_rejected(rows):
    data = rows[0].copy()
    data["property_type"] = "UNKNOWN"

    with pytest.raises(ValidationError):
        Property.model_validate(data)


@pytest.mark.parametrize(
    "alias",
    ["KL", "K.L.", "Kuala Lumpur"]
)
def test_location_normalization(rows, alias):
    data = rows[0].copy()
    data["location_code"] = alias

    result = Property.model_validate(data)

    assert result.location_code.value == "KUALA_LUMPUR"


@pytest.mark.parametrize(
    "alias",
    ["Condo", "Condominium", "CONDO"]
)
def test_property_type_normalization(rows, alias):
    data = rows[0].copy()
    data["property_type"] = alias

    result = Property.model_validate(data)

    assert result.property_type.value == "CONDO"


def test_empty_mrt_distance(rows):
    data = rows[0].copy()
    data["mrt_distance_m"] = ""

    result = Property.model_validate(data)

    assert result.mrt_distance_m is None


def test_negative_mrt_distance_rejected(rows):
    data = rows[0].copy()
    data["mrt_distance_m"] = "-50"

    with pytest.raises(ValidationError):
        Property.model_validate(data)


def test_missing_required_field_rejected(rows):
    data = rows[0].copy()
    del data["property_id"]

    with pytest.raises(ValidationError):
        Property.model_validate(data)


def test_extra_field_rejected(rows):
    data = rows[0].copy()
    data["unknown_field"] = "test"

    with pytest.raises(ValidationError):
        Property.model_validate(data)
