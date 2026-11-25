import json
from pathlib import Path

import numpy as np

from rag.embeddings import (
    OllamaEmbedder,
    EMBEDDING_DIMENSIONS,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

INDEX_DIR = PROJECT_ROOT / "rag" / "storage"


def build_index(
    chunks: list[dict],
    embedder: OllamaEmbedder,
    index_dir=INDEX_DIR,
    batch_size=4,
):
    """Create and save a local vector index."""

    if not chunks:
        raise ValueError("Cannot build an empty index")

    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    texts = [chunk["content"] for chunk in chunks]

    all_vectors = []

    # Generate embeddings in small batches.
    for start in range(0, len(texts), batch_size):

        batch = texts[start:start + batch_size]

        try:
            vectors = embedder.embed(batch)
        except Exception as error:
            filenames = ", ".join(sorted({chunk["filename"] for chunk in chunks[start:start + batch_size]}))
            error.add_note(f"Embedding failed for: {filenames}")
            raise

        all_vectors.append(vectors)

    matrix = np.vstack(all_vectors).astype(np.float32)

    if matrix.shape != (
        len(chunks),
        EMBEDDING_DIMENSIONS,
    ):
        raise ValueError("Invalid index dimensions")

    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)

    # Save vectors.
    np.savez_compressed(
        index_dir / "property_index.npz",
        embeddings=matrix,
    )

    # Save searchable metadata.
    metadata = {
        "embedding_model": embedder.model,
        "dimensions": EMBEDDING_DIMENSIONS,
        "chunks": chunks,
    }

    (index_dir / "property_index.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    return len(chunks)


class Retriever:
    """Search property-specific document chunks."""

    def __init__(
        self,
        embedder,
        chunks,
        vectors,
    ):
        self.embedder = embedder
        self.chunks = chunks

        self.vectors = np.asarray(
            vectors,
            dtype=np.float32,
        )

        if self.vectors.shape != (
            len(chunks),
            EMBEDDING_DIMENSIONS,
        ):
            raise ValueError("Index and metadata do not match")

        if not np.isfinite(self.vectors).all():
            raise ValueError("Index contains invalid vectors")

        norms = np.linalg.norm(
            self.vectors,
            axis=1,
            keepdims=True,
        )

        if np.any(norms == 0):
            raise ValueError("Index contains zero vectors")

        self.vectors = self.vectors / norms

    @classmethod
    def from_disk(
        cls,
        embedder,
        index_dir=INDEX_DIR,
    ):
        """Load a previously saved vector index."""

        index_dir = Path(index_dir)

        metadata = json.loads(
            (index_dir / "property_index.json").read_text(
                encoding="utf-8"
            )
        )

        if metadata["embedding_model"] != embedder.model:
            raise ValueError(
                "Embedding model mismatch. Rebuild the index."
            )

        if metadata["dimensions"] != EMBEDDING_DIMENSIONS:
            raise ValueError(
                "Embedding dimensions mismatch."
            )

        with np.load(
            index_dir / "property_index.npz",
            allow_pickle=False,
        ) as data:
            vectors = data["embeddings"]

        return cls(
            embedder=embedder,
            chunks=metadata["chunks"],
            vectors=vectors,
        )

    def search(
        self,
        query: str,
        property_id: str,
        top_k: int = 3,
    ) -> list[dict]:
        """Search only documents belonging to the specified property."""

        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query cannot be empty")

        if (
            not isinstance(property_id, str)
            or not property_id.strip()
        ):
            raise ValueError("property_id is required")

        if (
            not isinstance(top_k, int)
            or isinstance(top_k, bool)
            or top_k < 1
        ):
            raise ValueError("top_k must be a positive integer")

        property_id = property_id.strip()

        # SECURITY: Filter by property BEFORE similarity ranking.
        matching_indices = [
            index
            for index, chunk in enumerate(self.chunks)
            if chunk["property_id"] == property_id
        ]

        if not matching_indices:
            return []

        # Embed the user's question.
        query_vector = self.embedder.embed([query])[0]

        # Search only within the requested property's documents.
        property_vectors = self.vectors[matching_indices]

        scores = property_vectors @ query_vector

        # Highest cosine similarity first.
        ranking = np.argsort(
            -scores,
            kind="stable",
        )[:top_k]

        results = []

        for position in ranking:

            chunk_index = matching_indices[int(position)]

            chunk = self.chunks[chunk_index]

            results.append({
                "chunk_id": chunk["chunk_id"],
                "document_id": chunk["document_id"],
                "property_id": chunk["property_id"],
                "filename": chunk["filename"],
                "page": chunk["page"],
                "content": chunk["content"],
                "score": round(float(scores[position]), 4),
                "citation": (
                    f'{chunk["filename"]}, page {chunk["page"]}'
                ),
            })

        return results
