"""PostgreSQL read-only property repository, same public interface as CSV repository."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.connection import get_engine
from backend.database.models import PropertyRecord
from backend.schemas.property import Property
from backend.schemas.property_filters import PropertyFilters


def _to_property(row: PropertyRecord) -> Property:
    data = {column.name: getattr(row, column.name) for column in PropertyRecord.__table__.columns}
    return Property.model_validate(data)


class PostgresPropertyRepository:
    def __init__(self, engine=None):
        self.engine = engine if engine is not None else get_engine()

    def search(self, filters: PropertyFilters) -> list[Property]:
        q = select(PropertyRecord).order_by(PropertyRecord.property_id)
        q = q.where(PropertyRecord.availability_status.in_(
            [status.value for status in filters.allowed_statuses]
        ))
        if filters.location is not None:
            q = q.where(PropertyRecord.location_code == filters.location.value)
        if filters.property_type is not None:
            q = q.where(PropertyRecord.property_type == filters.property_type.value)
        if filters.min_price is not None:
            q = q.where(PropertyRecord.price_myr >= filters.min_price)
        if filters.max_price is not None:
            q = q.where(PropertyRecord.price_myr <= filters.max_price)
        mrt_limit = filters.max_mrt_distance_m
        if mrt_limit is None and filters.near_mrt:
            mrt_limit = 800
        if mrt_limit is not None:
            q = q.where(PropertyRecord.mrt_distance_m <= mrt_limit)
        with Session(self.engine) as session:
            return [_to_property(row) for row in session.scalars(q).all()]

    def get_by_id(self, property_id: str) -> Property | None:
        with Session(self.engine) as session:
            row = session.get(PropertyRecord, property_id)
            return _to_property(row) if row is not None else None
