"""
embeddings.py
=============
Embedding backends behind one tiny interface.

* `SentenceTransformerEmbeddings` — default, multilingual (Nepali + English).
  E5-family models need "query: " / "passage: " prefixes; the wrapper adds them
  automatically for model names containing "e5" (override with
  EMBEDDING_QUERY_PREFIX / EMBEDDING_PASSAGE_PREFIX in the env).
* `HashingEmbeddings` — dependency-free, deterministic bag-of-n-grams vectors.
  Selected with EMBEDDING_MODEL=hashing. Intended for tests/CI and offline
  smoke tests only; retrieval quality is far below the real model.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol

HASHING_MODEL = "hashing"


class Embeddings(Protocol):
    """Minimal embedding interface used by indexing and retrieval."""

    name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbeddings:
    """sentence-transformers model with correct query/passage prefixes."""

    def __init__(self, model_name: str, batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self.batch_size = batch_size
        is_e5 = "e5" in model_name.lower()
        self.query_prefix = os.getenv("EMBEDDING_QUERY_PREFIX", "query: " if is_e5 else "")
        self.passage_prefix = os.getenv("EMBEDDING_PASSAGE_PREFIX", "passage: " if is_e5 else "")
        self._model = SentenceTransformer(model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(
            [self.passage_prefix + t for t in texts],
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 200,
        )
        return vecs.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([self.query_prefix + text], normalize_embeddings=True)[0].tolist()


class HashingEmbeddings:
    """Deterministic character-n-gram hashing vectors (tests / offline only)."""

    def __init__(self, dim: int = 512) -> None:
        self.name = HASHING_MODEL
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in re.findall(r"[\w\u0900-\u097F]+", text.lower()):
            padded = f"#{word}#"
            grams = [padded[i: i + 3] for i in range(max(1, len(padded) - 2))] + [word]
            for g in grams:
                h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
                v[h % self.dim] += 1.0 if (h >> 9) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def load_embeddings(model_name: str, batch_size: int = 32) -> Embeddings:
    """Factory: "hashing" -> HashingEmbeddings, anything else -> sentence-transformers."""
    if model_name == HASHING_MODEL:
        return HashingEmbeddings()
    return SentenceTransformerEmbeddings(model_name, batch_size)
