from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Protocol


class EmbeddingProvider(Protocol):
    name: str
    dimension: int

    def embed_documents(self, texts: Iterable[str]) -> List[List[float]]: ...

    def embed_query(self, text: str) -> List[float]: ...


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


@dataclass
class HashEmbeddingProvider:
    """Dependency-free embedding fallback for pipeline tests and local dry runs.

    This is not semantic-quality embedding. It makes the loading/chunking/vector-cache
    pipeline deterministic until sentence-transformers is installed.
    """

    dimension: int = 384
    name: str = "hash_embedding_v1"

    def embed_documents(self, texts: Iterable[str]) -> List[List[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        vector = [0.0] * int(self.dimension)
        tokens = _tokens(text)
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        return _normalize(vector)


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5", *, normalize: bool = True) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. Install it before using provider='sentence_transformer'."
            ) from exc
        self.model_name = model_name
        self.name = f"sentence_transformer:{model_name}"
        self._model = SentenceTransformer(model_name)
        self.dimension = int(self._model.get_sentence_embedding_dimension())
        self._normalize = normalize

    def embed_documents(self, texts: Iterable[str]) -> List[List[float]]:
        return self._encode(list(texts))

    def embed_query(self, text: str) -> List[float]:
        return self._encode([text])[0]

    def _encode(self, texts: List[str]) -> List[List[float]]:
        embeddings = self._model.encode(texts, normalize_embeddings=self._normalize)
        return [[float(value) for value in row] for row in embeddings]


def create_embedding_provider(provider: str | None = None, *, dimension: int = 384) -> EmbeddingProvider:
    selected = (provider or os.getenv("RAG_EMBEDDING_PROVIDER") or "hash").strip().lower()
    if selected in {"hash", "local_hash", "dry_run"}:
        return HashEmbeddingProvider(dimension=dimension)
    if selected in {"sentence_transformer", "sentence-transformers", "bge"}:
        model_name = os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
        return SentenceTransformerEmbeddingProvider(model_name=model_name)
    raise ValueError(f"Unsupported embedding provider: {provider}")


def _tokens(text: str) -> List[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text or "")]


def _normalize(vector: List[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right)))
