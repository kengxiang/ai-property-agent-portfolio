import numpy as np
import pytest

from rag.embeddings import EMBEDDING_DIMENSIONS
from rag.retrieval import Retriever, build_index


class FakeEmbedder:
    model = "fake-test-model"

    def embed(self, texts):
        vectors = np.zeros(
            (len(texts), EMBEDDING_DIMENSIONS),
            dtype=np.float32,
        )

        for index, text in enumerate(texts):
            if "parking" in text.lower():
                vectors[index, 1] = 1.0
            else:
                vectors[index, 0] = 1.0

        return vectors


@pytest.fixture
def sample_chunks():
    return [
        {
            "chunk_id": "C001",
            "document_id": "D001",
            "property_id": "P001",
            "filename": "P001_rules.pdf",
            "page": 1,
            "content": "Pets are allowed.",
        },
        {
            "chunk_id": "C002",
            "document_id": "D002",
            "property_id": "P001",
            "filename": "P001_parking.pdf",
            "page": 1,
            "content": "Parking is available.",
        },
        {
            "chunk_id": "C003",
            "document_id": "D003",
            "property_id": "P002",
            "filename": "P002_rules.pdf",
            "page": 1,
            "content": "Pets are not permitted.",
        },
    ]


def test_property_isolation(tmp_path, sample_chunks):

    embedder = FakeEmbedder()

    build_index(
        chunks=sample_chunks,
        embedder=embedder,
        index_dir=tmp_path,
    )

    retriever = Retriever.from_disk(
        embedder,
        index_dir=tmp_path,
    )

    results = retriever.search(
        query="Are pets allowed?",
        property_id="P001",
        top_k=3,
    )

    assert len(results) == 2

    assert all(
        result["property_id"] == "P001"
        for result in results
    )

    assert all(
        "P002" not in result["filename"]
        for result in results
    )


def test_unknown_property(tmp_path, sample_chunks):

    embedder = FakeEmbedder()

    build_index(
        sample_chunks,
        embedder,
        index_dir=tmp_path,
    )

    retriever = Retriever.from_disk(
        embedder,
        index_dir=tmp_path,
    )

    results = retriever.search(
        query="Are pets allowed?",
        property_id="P999",
    )

    assert results == []


def test_relevant_document_ranks_first(
    tmp_path,
    sample_chunks,
):

    embedder = FakeEmbedder()

    build_index(
        sample_chunks,
        embedder,
        index_dir=tmp_path,
    )

    retriever = Retriever.from_disk(
        embedder,
        index_dir=tmp_path,
    )

    results = retriever.search(
        query="parking",
        property_id="P001",
    )

    assert results[0]["filename"] == "P001_parking.pdf"


def test_missing_property_id(tmp_path, sample_chunks):

    embedder = FakeEmbedder()

    build_index(
        sample_chunks,
        embedder,
        index_dir=tmp_path,
    )

    retriever = Retriever.from_disk(
        embedder,
        index_dir=tmp_path,
    )

    with pytest.raises(ValueError):
        retriever.search(
            query="pets",
            property_id="",
        )


def test_embedding_model_mismatch(
    tmp_path,
    sample_chunks,
):

    embedder = FakeEmbedder()

    build_index(
        sample_chunks,
        embedder,
        index_dir=tmp_path,
    )

    class DifferentEmbedder(FakeEmbedder):
        model = "different-model"

    with pytest.raises(ValueError):
        Retriever.from_disk(
            DifferentEmbedder(),
            index_dir=tmp_path,
        )
