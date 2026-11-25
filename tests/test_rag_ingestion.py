import pytest

from rag.ingestion import (
    chunk_text,
    load_document_chunks,
)


def test_six_documents_loaded():

    chunks = load_document_chunks()

    document_ids = {
        chunk["document_id"]
        for chunk in chunks
    }

    assert len(document_ids) == 6
    assert len(chunks) >= 6


def test_property_metadata():

    chunks = load_document_chunks()

    p001 = [
        c for c in chunks
        if c["property_id"] == "P001"
    ]

    assert len(p001) >= 2

    for chunk in p001:
        assert chunk["page"] >= 1
        assert chunk["filename"].endswith(".pdf")
        assert len(chunk["content_hash"]) == 64


def test_opposite_pet_policies():

    chunks = load_document_chunks()

    p001_text = " ".join(
        c["content"]
        for c in chunks
        if c["property_id"] == "P001"
    )

    p002_text = " ".join(
        c["content"]
        for c in chunks
        if c["property_id"] == "P002"
    )

    assert "Pets are allowed" in p001_text
    assert "Pets are not permitted" in p002_text


def test_chunk_overlap():

    chunks = chunk_text(
        "a b c d e f",
        max_words=4,
        overlap_words=1
    )

    assert chunks == [
        "a b c d",
        "d e f",
    ]


def test_invalid_chunk_settings():

    with pytest.raises(ValueError):
        chunk_text(
            "hello world",
            max_words=5,
            overlap_words=5
        )
