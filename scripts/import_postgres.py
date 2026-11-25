"""One-time, repeatable Phase 2 synthetic data import. Run from repository root.

Imports validated CSV properties and saved BGE-M3 vectors.
Optionally rebuilds document vectors with Ollama before importing.
"""
import argparse
import csv
import hashlib
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database.connection import get_database_url
from backend.database.models import PropertyRecord, DocumentRecord, DocumentChunkRecord
from backend.schemas.property import Property
from rag.embeddings import EMBEDDING_DIMENSIONS, OllamaEmbedder
from rag.ingestion import load_document_chunks
from rag.retrieval import build_index

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "synthetic_properties.csv"
DOC_DIR = ROOT / "data" / "sample_documents"
INDEX_DIR = ROOT / "rag" / "storage"


def import_properties(engine) -> int:
    with CSV_PATH.open(newline="", encoding="utf-8") as stream:
        parsed = [Property.model_validate(row) for row in csv.DictReader(stream)]
    ids = [item.property_id for item in parsed]
    if not parsed or len(set(ids)) != len(ids):
        raise ValueError("Property CSV is empty or contains duplicate IDs")
    # Import is explicit; rerunning updates the same primary keys (no duplicates).
    with Session(engine) as session, session.begin():
        for item in parsed:
            session.merge(PropertyRecord(**item.model_dump(mode="json")))
    return len(parsed)


def load_verified_document_index(index_dir=INDEX_DIR):
    manifest = json.loads((DOC_DIR / "manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((index_dir / "property_index.json").read_text(encoding="utf-8"))
    with np.load(index_dir / "property_index.npz", allow_pickle=False) as archive:
        vectors = np.asarray(archive["embeddings"], dtype=np.float32)
    chunks = metadata["chunks"]
    model = os.getenv("EMBEDDING_MODEL", "bge-m3")
    if metadata.get("embedding_model") != model:
        raise ValueError("Index model differs from EMBEDDING_MODEL; rebuild index first")
    if metadata.get("dimensions") != EMBEDDING_DIMENSIONS or vectors.shape != (len(chunks), EMBEDDING_DIMENSIONS):
        raise ValueError("Embedding index shape/dimension mismatch")
    if len(chunks) == 0 or not np.isfinite(vectors).all():
        raise ValueError("No chunks or invalid embedding values")
    if np.any(np.linalg.norm(vectors, axis=1) <= 0):
        raise ValueError("Embedding contains zero vector")
    doc_ids = [entry["document_id"] for entry in manifest]
    if len(set(doc_ids)) != len(doc_ids):
        raise ValueError("Duplicate document IDs in manifest")
    lookup = {entry["document_id"]: entry for entry in manifest}
    docs = []
    for entry in manifest:
        filename = entry["filename"]
        if Path(filename).name != filename or not filename.lower().endswith(".pdf"):
            raise ValueError(f"Unsafe document filename: {filename}")
        path = DOC_DIR / filename
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        page_count = len(PdfReader(path).pages)
        if page_count <= 0:
            raise ValueError(f"Document has no pages: {filename}")
        docs.append((entry, digest, page_count))
    hashes = {entry["document_id"]: digest for entry, digest, _ in docs}
    pages = {entry["document_id"]: count for entry, _, count in docs}
    seen_chunks = set()
    for chunk in chunks:
        chunk_id, document_id = chunk["chunk_id"], chunk["document_id"]
        if chunk_id in seen_chunks:
            raise ValueError(f"Duplicate chunk ID: {chunk_id}")
        seen_chunks.add(chunk_id)
        if document_id not in lookup:
            raise ValueError(f"Unknown document ID: {document_id}")
        if (chunk["property_id"] != lookup[document_id]["property_id"]
                or chunk["filename"] != lookup[document_id]["filename"]
                or chunk["content_hash"] != hashes[document_id]):
            raise ValueError(f"Stale/mismatched PDF metadata: {chunk_id}")
        page = chunk["page"]
        if type(page) is not int or not (1 <= page <= pages[document_id]):
            raise ValueError(f"Invalid page for {chunk_id}")
        if not isinstance(chunk["content"], str) or not chunk["content"].strip():
            raise ValueError(f"Empty chunk: {chunk_id}")
        match = re.fullmatch(rf"{re.escape(document_id)}-p{page}-c([1-9][0-9]*)", chunk_id)
        if match is None:
            raise ValueError(f"Unexpected chunk ID format: {chunk_id}")
    missing = set(lookup) - {chunk["document_id"] for chunk in chunks}
    if missing:
        filenames = ", ".join(lookup[document_id]["filename"] for document_id in sorted(missing))
        raise ValueError(f"No indexed text chunks for: {filenames}")
    return docs, chunks, vectors, model


def import_documents(engine, index_dir=INDEX_DIR) -> tuple[int, int]:
    docs, chunks, vectors, model = load_verified_document_index(index_dir)
    # One DB transaction: either all docs/chunks write, or none do.
    with Session(engine) as session, session.begin():
        for entry, digest, page_count in docs:
            old = session.get(DocumentRecord, entry["document_id"])
            if old is not None and old.content_hash != digest:
                raise ValueError(f"Document changed in database: {entry['document_id']}. Reindex/sync explicitly.")
            session.merge(DocumentRecord(
                document_id=entry["document_id"],
                property_id=entry["property_id"],
                filename=entry["filename"],
                page_count=page_count,
                content_hash=digest,
                version=1,
            ))
        session.flush()  # Ensure FK parent rows exist before chunks.
        for chunk, vector in zip(chunks, vectors, strict=True):
            page = chunk["page"]
            n = int(chunk["chunk_id"].rsplit("-c", 1)[1])
            session.merge(DocumentChunkRecord(
                chunk_id=chunk["chunk_id"],
                document_id=chunk["document_id"],
                page_num=page,
                chunk_number=n,
                content=chunk["content"],
                embedding_model=model,
                embedding_model_version=None,
                embedding=vector.tolist(),
            ))
    return len(docs), len(chunks)


def rebuild_and_import_documents(engine) -> tuple[int, int]:
    chunks = load_document_chunks(DOC_DIR)
    with TemporaryDirectory(prefix="property-rag-") as directory:
        index_dir = Path(directory)
        build_index(chunks, OllamaEmbedder(), index_dir=index_dir, batch_size=1)
        return import_documents(engine, index_dir)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["properties", "documents", "all"], default="all")
    parser.add_argument("--rebuild-document-index", action="store_true",
                        help="Extract PDFs and regenerate embeddings with the configured Ollama model")
    args = parser.parse_args()
    if args.only == "properties" and args.rebuild_document_index:
        parser.error("--rebuild-document-index requires a document import")
    engine = create_engine(get_database_url(), pool_pre_ping=True)
    try:
        if args.only in ("properties", "all"):
            print("Properties upserted:", import_properties(engine))
        if args.only in ("documents", "all"):
            importer = rebuild_and_import_documents if args.rebuild_document_index else import_documents
            n_docs, n_chunks = importer(engine)
            print(f"Documents upserted: {n_docs}; chunks upserted: {n_chunks}")
        print("P2-01 import: PASSED")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
