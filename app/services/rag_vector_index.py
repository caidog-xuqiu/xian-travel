from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.services.rag_chunker import RagChunk, chunk_documents
from app.services.rag_document_loader import RagDocument, load_jsonl_documents
from app.services.rag_embedding import EmbeddingProvider, cosine_similarity, create_embedding_provider

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "data" / "rag_sources" / "xian_route_cases_seed.jsonl"
DEFAULT_INDEX = Path(__file__).resolve().parents[1] / "data" / "rag_index" / "xian_route_case_vectors.jsonl"


def build_vector_records(
    chunks: Iterable[RagChunk],
    provider: EmbeddingProvider,
) -> List[Dict[str, Any]]:
    chunk_list = list(chunks)
    embeddings = provider.embed_documents([chunk.text for chunk in chunk_list])
    records: List[Dict[str, Any]] = []
    for chunk, embedding in zip(chunk_list, embeddings):
        records.append(
            {
                "id": chunk.id,
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "metadata": chunk.metadata,
                "embedding": embedding,
                "embedding_provider": provider.name,
                "embedding_dim": len(embedding),
            }
        )
    return records


def write_vector_records(records: Iterable[Dict[str, Any]], output_path: str | Path) -> int:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fp:
        for record in records:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


def load_vector_records(path: str | Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as fp:
        for line in fp:
            text = line.strip()
            if text:
                records.append(json.loads(text))
    return records


def build_local_vector_index(
    *,
    source_path: str | Path = DEFAULT_SOURCE,
    output_path: str | Path = DEFAULT_INDEX,
    provider_name: str | None = None,
    chunk_size: int = 420,
    chunk_overlap: int = 60,
    hash_dimension: int = 384,
) -> Dict[str, Any]:
    documents = load_jsonl_documents(source_path)
    chunks = chunk_documents(documents, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    provider = create_embedding_provider(provider_name, dimension=hash_dimension)
    records = build_vector_records(chunks, provider)
    count = write_vector_records(records, output_path)
    return {
        "source_path": str(source_path),
        "output_path": str(output_path),
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "record_count": count,
        "embedding_provider": provider.name,
        "embedding_dim": provider.dimension,
    }


def query_local_vector_index(
    query: str,
    *,
    index_path: str | Path = DEFAULT_INDEX,
    provider_name: str | None = None,
    top_k: int = 5,
    hash_dimension: int = 384,
) -> List[Dict[str, Any]]:
    records = load_vector_records(index_path)
    if not records or top_k <= 0:
        return []
    provider = create_embedding_provider(provider_name or _provider_from_records(records), dimension=hash_dimension)
    query_vector = provider.embed_query(query)
    scored: List[Dict[str, Any]] = []
    for record in records:
        score = cosine_similarity(query_vector, record.get("embedding") or [])
        item = {key: value for key, value in record.items() if key != "embedding"}
        item["score"] = round(score, 6)
        scored.append(item)
    scored.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return scored[:top_k]


def _provider_from_records(records: List[Dict[str, Any]]) -> str:
    provider = str((records[0] if records else {}).get("embedding_provider") or "hash")
    if provider.startswith("hash_embedding"):
        return "hash"
    if provider.startswith("sentence_transformer:"):
        return "sentence_transformer"
    return "hash"


def load_and_chunk_seed_cases(source_path: str | Path = DEFAULT_SOURCE) -> tuple[List[RagDocument], List[RagChunk]]:
    documents = load_jsonl_documents(source_path)
    chunks = chunk_documents(documents)
    return documents, chunks
