"""Explicit backend switch; CSV remains the safe default for existing tests/demo."""
import os

from backend.services.property_service import PropertyRepository


def create_property_repository():
    backend = os.getenv("PROPERTY_BACKEND", "csv").strip().lower()
    if backend == "csv":
        return PropertyRepository()
    if backend == "postgres":
        from backend.services.postgres_property_service import PostgresPropertyRepository
        return PostgresPropertyRepository()
    raise ValueError(f"Unsupported PROPERTY_BACKEND: {backend}")
