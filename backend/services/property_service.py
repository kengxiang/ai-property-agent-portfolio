"""Read-only CSV-backed property repository for the Phase 1 MVP."""

import csv
from pathlib import Path

from backend.schemas.property import Property
from backend.schemas.property_filters import PropertyFilters


DEFAULT_DATA_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "synthetic_properties.csv"
)


class PropertyRepository:
    def __init__(self, csv_path: Path = DEFAULT_DATA_PATH):
        with Path(csv_path).open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))

        properties = [Property.model_validate(row) for row in rows]
        ids = [item.property_id for item in properties]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate property_id in CSV dataset")

        self._properties = properties
        self._by_id = {item.property_id: item for item in properties}

    def search(self, filters: PropertyFilters) -> list[Property]:
        # Explicit max distance takes precedence over the 800m near_mrt rule.
        mrt_limit = filters.max_mrt_distance_m
        if mrt_limit is None and filters.near_mrt:
            mrt_limit = 800

        allowed_statuses = filters.allowed_statuses
        matches = []
        for item in self._properties:
            if item.availability_status not in allowed_statuses:
                continue
            if filters.location is not None and item.location_code != filters.location:
                continue
            if filters.property_type is not None and item.property_type != filters.property_type:
                continue
            if filters.min_price is not None and item.price_myr < filters.min_price:
                continue
            if filters.max_price is not None and item.price_myr > filters.max_price:
                continue
            if mrt_limit is not None and (
                item.mrt_distance_m is None or item.mrt_distance_m > mrt_limit
            ):
                continue
            matches.append(item)

        return matches

    def get_by_id(self, property_id: str) -> Property | None:
        return self._by_id.get(property_id)
