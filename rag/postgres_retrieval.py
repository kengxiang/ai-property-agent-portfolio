"""Property-scoped exact cosine pgvector retrieval (no approximate index)."""
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.connection import get_engine
from backend.database.models import DocumentChunkRecord, DocumentRecord
from rag.embeddings import OllamaEmbedder, EMBEDDING_DIMENSIONS


class PostgresRetriever:
    def __init__(self, embedder=None, engine=None):
        self.embedder = embedder if embedder is not None else OllamaEmbedder()
        self.engine = engine if engine is not None else get_engine()

    def search(self, query: str, property_id: str, top_k: int = 3) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query cannot be empty")
        if not isinstance(property_id, str) or not property_id.strip():
            raise ValueError("property_id is required")
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        property_id = property_id.strip().upper()

        with Session(self.engine) as session:
            # Early return: missing property documents should not call Ollama.
            has_docs = session.scalar(
                select(DocumentRecord.document_id)
                .where(DocumentRecord.property_id == property_id)
                .limit(1)
            )
            if has_docs is None:
                return []

            # Disallow a silent mix of embedding models within a property.
            models = session.scalars(
                select(DocumentChunkRecord.embedding_model)
                .join(DocumentRecord, DocumentChunkRecord.document_id == DocumentRecord.document_id)
                .where(DocumentRecord.property_id == property_id)
                .distinct()
            ).all()
            if any(model != self.embedder.model for model in models):
                raise ValueError("Stored embedding model differs from query embedding model")
            if not models:
                return []

            vector = np.asarray(self.embedder.embed([query])[0], dtype=np.float32)
            if vector.shape != (EMBEDDING_DIMENSIONS,) or not np.isfinite(vector).all():
                raise ValueError("Invalid query embedding")
            distance = DocumentChunkRecord.embedding.cosine_distance(vector.tolist())
            stmt = (
                select(DocumentChunkRecord, DocumentRecord, distance.label("distance"))
                .join(DocumentRecord, DocumentChunkRecord.document_id == DocumentRecord.document_id)
                .where(DocumentRecord.property_id == property_id)
                .order_by(distance, DocumentChunkRecord.chunk_id)
                .limit(top_k)
            )
            rows = session.execute(stmt).all()
            return [
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": doc.document_id,
                    "property_id": doc.property_id,
                    "filename": doc.filename,
                    "page": chunk.page_num,
                    "content": chunk.content,
                    "score": round(1.0 - float(dist), 4),
                    "citation": f"{doc.filename}, page {chunk.page_num}",
                }
                for chunk, doc, dist in rows
            ]
