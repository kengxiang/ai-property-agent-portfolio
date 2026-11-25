"""Fast offline tests: no DB server or Ollama calls required."""
import pytest

from backend.services.repository_factory import create_property_repository
from backend.services.property_service import PropertyRepository
from backend.services.postgres_property_service import PostgresPropertyRepository
from backend.schemas.property_filters import PropertyFilters


def test_factory_csv_default(monkeypatch):
    monkeypatch.delenv("PROPERTY_BACKEND", raising=False)
    assert isinstance(create_property_repository(), PropertyRepository)


def test_factory_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("PROPERTY_BACKEND", "unrecognized")
    with pytest.raises(ValueError):
        create_property_repository()


def test_pg_filter_validation_still_applies():
    with pytest.raises(ValueError):
        PropertyFilters(min_price=700000, max_price=600000)


def test_pg_repository_has_same_methods():
    assert callable(PostgresPropertyRepository.search)
    assert callable(PostgresPropertyRepository.get_by_id)
