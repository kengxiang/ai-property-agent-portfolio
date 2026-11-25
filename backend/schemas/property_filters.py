from pydantic import BaseModel, Field, field_validator, model_validator

from backend.schemas.property import (
    AvailabilityStatus,
    LocationCode,
    PropertyType,
    Property,
)


class PropertyFilters(BaseModel):

    location: LocationCode | None = None
    property_type: PropertyType | None = None

    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)

    max_mrt_distance_m: int | None = Field(default=None, ge=0)

    near_mrt: bool = False
    include_unavailable: bool = False
    availability_statuses: list[AvailabilityStatus] | None = Field(default=None, min_length=1)

    @property
    def allowed_statuses(self) -> list[AvailabilityStatus]:
        if self.availability_statuses is not None:
            return self.availability_statuses
        return list(AvailabilityStatus) if self.include_unavailable else [AvailabilityStatus.AVAILABLE]

    @field_validator("location", mode="before")
    @classmethod
    def normalize_location(cls, value):
        return Property.normalize_location(value)

    @field_validator("property_type", mode="before")
    @classmethod
    def normalize_property_type(cls, value):
        return Property.normalize_property_type(value)

    @model_validator(mode="after")
    def validate_price_range(self):

        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError(
                "min_price cannot be greater than max_price"
            )

        return self
