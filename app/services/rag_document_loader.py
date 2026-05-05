from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class RagDocument:
    id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load_jsonl_documents(path: str | Path, *, text_field: str = "text_for_rag") -> List[RagDocument]:
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    documents: List[RagDocument] = []
    with source_path.open("r", encoding="utf-8") as fp:
        for line_no, line in enumerate(fp, start=1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            doc_id = _as_text(payload.get("id")) or f"{source_path.stem}_{line_no}"
            doc_text = _as_text(payload.get(text_field))
            if not doc_text:
                doc_text = _fallback_text(payload)
            if not doc_text:
                continue
            metadata = dict(payload.get("metadata") or {})
            metadata.update(
                {
                    "document_id": doc_id,
                    "source_path": source_path.as_posix(),
                    "source_type": payload.get("source_type") or metadata.get("source_type") or "unknown",
                    "city": payload.get("city") or metadata.get("city") or "",
                    "user_query": payload.get("user_query") or "",
                    "scene_context": payload.get("scene_context") or metadata.get("scene_context") or "",
                    "route_tags": payload.get("route_tags") or metadata.get("route_tags") or [],
                }
            )
            documents.append(RagDocument(id=doc_id, text=doc_text, metadata=metadata))
    return documents


def _fallback_text(payload: Dict[str, Any]) -> str:
    parts = []
    for label, key in [
        ("用户需求", "user_query"),
        ("适合场景", "scene_context"),
        ("可用时长", "available_hours_text"),
        ("同行人", "companion_text"),
        ("推荐路线", "route_text"),
        ("推荐理由", "recommendation_reason"),
    ]:
        value = _as_text(payload.get(key))
        if value:
            parts.append(f"{label}：{value}")
    tags = payload.get("route_tags") or []
    if tags:
        parts.append("路线标签：" + "、".join(str(tag) for tag in tags))
    return "\n".join(parts)


def iter_jsonl_documents(paths: Iterable[str | Path], *, text_field: str = "text_for_rag") -> List[RagDocument]:
    documents: List[RagDocument] = []
    for path in paths:
        documents.extend(load_jsonl_documents(path, text_field=text_field))
    return documents
