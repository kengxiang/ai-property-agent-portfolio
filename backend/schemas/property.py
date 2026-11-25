from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LocationCode(str, Enum):
    KUALA_LUMPUR = "KUALA_LUMPUR"
    SELANGOR = "SELANGOR"
    JOHOR_BAHRU = "JOHOR_BAHRU"


class PropertyType(str, Enum):
    CONDO = "CONDO"
    APARTMENT = "APARTMENT"
    LANDED = "LANDED"


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    SOLD = "SOLD"


class Property(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid"
    )

    property_id: str = Field(min_length=1)
    name: str = Field(min_length=1)

    location_code: LocationCode
    display_location: str = Field(min_length=1)

    property_type: PropertyType

    price_myr: int = Field(ge=0)
    bedrooms: int = Field(ge=0)
    bathrooms: int = Field(ge=0)

    mrt_distance_m: int | None = Field(default=None, ge=0)

    availability_status: AvailabilityStatus
    description: str = Field(min_length=1)

    @field_validator("location_code", mode="before")
    @classmethod
    def normalize_location(cls, value):
        if isinstance(value, str):
            cleaned = value.strip().upper().replace(".", "")

            aliases = {
                "KL": "KUALA_LUMPUR",
                "KUALA LUMPUR": "KUALA_LUMPUR",
                "JOHOR BAHRU": "JOHOR_BAHRU",
                "JB": "JOHOR_BAHRU",
            }

            return aliases.get(cleaned, cleaned)

        return value

    @field_validator("property_type", mode="before")
    @classmethod
    def normalize_property_type(cls, value):
        if isinstance(value, str):
            cleaned = value.strip().upper()

            aliases = {
                "CONDOMINIUM": "CONDO",
            }

            return aliases.get(cleaned, cleaned)

        return value

    @field_validator("mrt_distance_m", mode="before")
    @classmethod
    def normalize_mrt_distance(cls, value):
        if value == "":
            return None

        return value
