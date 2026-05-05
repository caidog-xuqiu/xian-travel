from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from app.services.rag_document_loader import RagDocument


@dataclass(frozen=True)
class RagChunk:
    id: str
    document_id: str
    chunk_index: int
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


_BREAK_CHARS = ["\n\n", "\n", "。", "；", "，", ",", " "]


def chunk_text(text: str, *, chunk_size: int = 420, chunk_overlap: int = 60) -> List[str]:
    clean = (text or "").strip()
    if not clean:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be non-negative")
    if len(clean) <= chunk_size:
        return [clean]

    chunks: List[str] = []
    start = 0
    while start < len(clean):
        end = min(start + chunk_size, len(clean))
        if end < len(clean):
            split_at = _best_split(clean, start, end)
            if split_at > start:
                end = split_at
        chunk = clean[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(clean):
            break
        next_start = max(0, end - chunk_overlap)
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


def _best_split(text: str, start: int, end: int) -> int:
    window = text[start:end]
    best = -1
    for marker in _BREAK_CHARS:
        idx = window.rfind(marker)
        if idx > best:
            best = idx + len(marker)
    if best <= 0 or best < int((end - start) * 0.55):
        return end
    return start + best


def chunk_documents(
    documents: Iterable[RagDocument],
    *,
    chunk_size: int = 420,
    chunk_overlap: int = 60,
) -> List[RagChunk]:
    chunks: List[RagChunk] = []
    for document in documents:
        parts = chunk_text(document.text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        for index, text in enumerate(parts):
            metadata = dict(document.metadata or {})
            metadata.update(
                {
                    "document_id": document.id,
                    "chunk_index": index,
                    "chunk_count": len(parts),
                }
            )
            chunks.append(
                RagChunk(
                    id=f"{document.id}::chunk_{index:03d}",
                    document_id=document.id,
                    chunk_index=index,
                    text=text,
                    metadata=metadata,
                )
            )
    return chunks
