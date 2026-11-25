import json
import os
import urllib.request

import numpy as np


EMBEDDING_DIMENSIONS = 1024


class OllamaEmbedder:
    """Generate embeddings using Ollama BGE-M3."""

    def __init__(
        self,
        model=None,
        base_url=None,
        timeout=120,
    ):
        self.model = (
            model
            or os.getenv("EMBEDDING_MODEL")
            or "bge-m3"
        )

        self.base_url = (
            base_url
            or os.getenv("OLLAMA_BASE_URL")
            or "http://127.0.0.1:11434"
        ).rstrip("/")

        self.timeout = timeout

    def embed(self, texts: list[str]) -> np.ndarray:
        """Convert texts into normalized 1024-dimensional vectors."""

        if not texts or any(
            not isinstance(text, str) or not text.strip()
            for text in texts
        ):
            raise ValueError("Embedding input must contain non-empty texts")

        payload = {
            "model": self.model,
            "input": texts,
            "truncate": False,
        }

        request = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(
            request,
            timeout=self.timeout,
        ) as response:
            result = json.load(response)

        vectors = np.asarray(
            result["embeddings"],
            dtype=np.float32,
        )

        expected_shape = (
            len(texts),
            EMBEDDING_DIMENSIONS,
        )

        if vectors.shape != expected_shape:
            raise ValueError(
                f"Invalid embedding shape: {vectors.shape}. "
                f"Expected: {expected_shape}"
            )

        if not np.isfinite(vectors).all():
            raise ValueError("Embedding contains invalid values")

        # Normalize vectors for cosine similarity.
        norms = np.linalg.norm(
            vectors,
            axis=1,
            keepdims=True,
        )

        if np.any(norms == 0):
            raise ValueError("Embedding contains a zero vector")

        return vectors / norms
