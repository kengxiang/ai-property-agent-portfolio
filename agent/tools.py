import os
from typing import Any

from pydantic import ConfigDict

from backend.schemas.property_filters import PropertyFilters
from backend.services.repository_factory import create_property_repository

from functools import lru_cache
from rag.embeddings import OllamaEmbedder
from rag.retrieval import Retriever

# Reuse our existing property repository
repository = create_property_repository()


class ToolPropertyFilters(PropertyFilters):
    model_config = ConfigDict(extra="forbid")


def search_properties(**filters: Any) -> list[dict]:
    """
    Search properties using validated filters.

    Example:
    search_properties(
        location="KL",
        property_type="CONDO",
        max_price=600000,
        near_mrt=True
    )
    """

    validated_filters = ToolPropertyFilters.model_validate(filters)

    results = repository.search(validated_filters)

    return [
        property.model_dump(mode="json")
        for property in results
    ]


def get_property_details(property_id: str) -> dict | None:
    """
    Retrieve one property by its ID.
    Returns None if the property does not exist.
    """

    if not isinstance(property_id, str) or not property_id.strip():
        raise ValueError("A valid property_id is required")

    result = repository.get_by_id(property_id.strip())

    if result is None:
        return None

    return result.model_dump(mode="json")
@lru_cache(maxsize=1)
def get_rag_retriever():
    """Load the existing RAG index once per process."""

    backend = os.getenv("RAG_BACKEND", "numpy").strip().lower()
    embedder = OllamaEmbedder()
    if backend == "numpy":
        return Retriever.from_disk(embedder)
    if backend == "postgres":
        from rag.postgres_retrieval import PostgresRetriever
        return PostgresRetriever(embedder=embedder)
    raise ValueError(f"Unsupported RAG_BACKEND: {backend}")


def search_documents(
    query: str,
    property_id: str,
    top_k: int = 3,
) -> list[dict]:
    """Search PDF documents for one specific property."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query cannot be empty")

    if not isinstance(property_id, str) or not property_id.strip():
        raise ValueError("Property ID is required")

    if (
        not isinstance(top_k, int)
        or isinstance(top_k, bool)
        or not 1 <= top_k <= 5
    ):
        raise ValueError("top_k must be between 1 and 5")

    retriever = get_rag_retriever()

    return retriever.search(
        query=query.strip(),
        property_id=property_id.strip().upper(),
        top_k=top_k,
    )
