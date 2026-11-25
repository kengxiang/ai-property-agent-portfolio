"""REST endpoints for read-only synthetic property listing searches."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from backend.schemas.property import Property
from backend.schemas.property_filters import PropertyFilters
from backend.services.repository_factory import create_property_repository


router = APIRouter(tags=["Properties"])
_repository = create_property_repository()


@router.get("/properties", response_model=list[Property])
def search_properties(filters: Annotated[PropertyFilters, Query()]):
    return _repository.search(filters)


@router.get("/properties/{property_id}", response_model=Property)
def get_property_details(property_id: str):
    result = _repository.get_by_id(property_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return result
