import pytest
from pydantic import ValidationError

from agent.tools import search_properties, get_property_details


def test_agent_property_search():
    results = search_properties(
        location="KL",
        property_type="CONDO",
        max_price=600000,
        near_mrt=True
    )

    ids = [item["property_id"] for item in results]

    assert ids == ["P001", "P003", "P010"]


def test_default_available_only():
    results = search_properties()

    assert len(results) == 20
    assert all(
        item["availability_status"] == "AVAILABLE"
        for item in results
    )


def test_include_unavailable():
    results = search_properties(include_unavailable=True)

    assert len(results) == 24


def test_invalid_price_range():
    with pytest.raises(ValidationError):
        search_properties(
            min_price=700000,
            max_price=600000
        )


def test_negative_price():
    with pytest.raises(ValidationError):
        search_properties(max_price=-100)


def test_unknown_filter():
    with pytest.raises(ValidationError):
        search_properties(unknown_filter="test")


def test_no_matching_properties():
    results = search_properties(max_price=100)

    assert results == []


def test_get_property_details():
    result = get_property_details("P001")

    assert result["property_id"] == "P001"
    assert result["price_myr"] == 550000


def test_unknown_property():
    result = get_property_details("P999")

    assert result is None


def test_empty_property_id():
    with pytest.raises(ValueError):
        get_property_details("")
