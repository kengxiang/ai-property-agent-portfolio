from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.property_filters import PropertyFilters
from backend.services.property_service import PropertyRepository


client = TestClient(app)


def ids(response):
    assert response.status_code == 200, response.text
    return [item["property_id"] for item in response.json()]


def test_health_endpoint_still_works():
    assert client.get("/health").json() == {"status": "healthy"}


def test_available_only_by_default():
    assert len(ids(client.get("/properties"))) == 20


def test_include_unavailable():
    assert len(ids(client.get("/properties?include_unavailable=true"))) == 24


def test_combined_filters_and_inclusive_boundaries():
    response = client.get(
        "/properties",
        params={
            "location": "KL",
            "property_type": "Condominium",
            "max_price": 600000,
            "near_mrt": "true",
        },
    )
    # P003 costs exactly RM600,000 and is exactly 800m from MRT.
    assert ids(response) == ["P001", "P003", "P010"]


def test_minimum_price_is_inclusive():
    assert ids(client.get("/properties?min_price=600000&max_price=600000")) == ["P003"]


def test_near_mrt_excludes_properties_without_distance():
    found = ids(client.get("/properties?near_mrt=true"))
    assert "P007" not in found
    assert "P019" not in found


def test_explicit_distance_overrides_near_mrt_default():
    found = ids(
        client.get(
            "/properties?location=KL&property_type=CONDO"
            "&near_mrt=true&max_mrt_distance_m=450&include_unavailable=true"
        )
    )
    assert found == ["P002", "P008"]


def test_unavailable_property_excluded_unless_requested():
    assert "P005" not in ids(client.get("/properties?location=KL"))
    assert "P005" in ids(client.get("/properties?location=KL&include_unavailable=true"))


def test_no_matches_returns_empty_list():
    assert client.get("/properties?min_price=2000000").json() == []


def test_get_property_detail():
    response = client.get("/properties/P001")
    assert response.status_code == 200
    assert response.json()["name"] == "KL Central Residence"


def test_missing_property_is_404():
    assert client.get("/properties/P999").status_code == 404


def test_location_alias_with_periods():
    assert "P001" in ids(client.get("/properties?location=K.L."))


def test_invalid_price_range_is_422():
    response = client.get("/properties?min_price=700000&max_price=600000")
    assert response.status_code == 422


def test_negative_price_is_422():
    assert client.get("/properties?max_price=-1").status_code == 422


def test_negative_mrt_distance_is_422():
    assert client.get("/properties?max_mrt_distance_m=-1").status_code == 422


def test_invalid_property_type_is_422():
    assert client.get("/properties?property_type=CASTLE").status_code == 422


def test_invalid_location_is_422():
    assert client.get("/properties?location=UNKNOWN").status_code == 422


def test_duplicate_property_id_in_csv_is_rejected(tmp_path):
    from backend.services.property_service import DEFAULT_DATA_PATH

    source = DEFAULT_DATA_PATH.read_text(encoding="utf-8")
    lines = source.strip().splitlines()
    duplicate_csv = tmp_path / "duplicates.csv"
    duplicate_csv.write_text("\n".join(lines + [lines[1]]) + "\n", encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match="Duplicate property_id"):
        PropertyRepository(duplicate_csv)


def test_filter_rejects_reversed_price_range():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PropertyFilters(min_price=900000, max_price=800000)


def test_exact_availability_statuses():
    assert ids(client.get("/properties?location=SELANGOR&availability_statuses=RESERVED")) == ["P018"]
    result = client.get("/properties?location=JB&availability_statuses=AVAILABLE&availability_statuses=SOLD&property_type=LANDED")
    assert set(ids(result)) == {"P021", "P024"}
    assert client.get("/properties?availability_statuses=UNKNOWN").status_code == 422
