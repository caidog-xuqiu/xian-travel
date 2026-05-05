from __future__ import annotations

from pathlib import Path
import uuid

from app.services.rag_chunker import chunk_documents, chunk_text
from app.services.rag_document_loader import RagDocument, load_jsonl_documents
from app.services.rag_embedding import HashEmbeddingProvider, cosine_similarity
from app.services.rag_vector_index import build_local_vector_index, load_vector_records, query_local_vector_index


def test_chunk_text_keeps_short_case_as_single_chunk() -> None:
    text = "用户需求：想去公园散步\n推荐路线：西电 -> 雁南公园"
    assert chunk_text(text, chunk_size=200) == [text]


def test_chunk_documents_adds_chunk_metadata() -> None:
    docs = [RagDocument(id="case_001", text="用户需求：想去公园散步。推荐路线：雁南公园。", metadata={"route_tags": ["park"]})]
    chunks = chunk_documents(docs, chunk_size=20, chunk_overlap=4)
    assert chunks
    assert chunks[0].document_id == "case_001"
    assert chunks[0].metadata["route_tags"] == ["park"]
    assert chunks[0].metadata["chunk_count"] == len(chunks)


def test_hash_embedding_is_deterministic_and_comparable() -> None:
    provider = HashEmbeddingProvider(dimension=64)
    left = provider.embed_query("公园 散步")
    same = provider.embed_query("公园 散步")
    other = provider.embed_query("烧烤 夜宵")
    assert left == same
    assert cosine_similarity(left, same) > cosine_similarity(left, other)


def test_build_local_vector_index_and_query() -> None:
    base = Path(__file__).resolve().parent / "_tmp_artifacts"
    base.mkdir(parents=True, exist_ok=True)
    source = base / f"cases_{uuid.uuid4().hex}.jsonl"
    source.write_text(
        "\n".join(
            [
                '{"id":"case_park","source_type":"route_case_seed","city":"西安","user_query":"想去公园散步","route_tags":["park","walk"],"text_for_rag":"用户需求：想去公园散步\\n推荐路线：雁南公园"}',
                '{"id":"case_bbq","source_type":"route_case_seed","city":"西安","user_query":"想吃烧烤夜宵","route_tags":["bbq"],"text_for_rag":"用户需求：想吃烧烤夜宵\\n推荐路线：安老虎烧烤"}',
            ]
        ),
        encoding="utf-8",
    )
    output = base / f"vectors_{uuid.uuid4().hex}.jsonl"

    summary = build_local_vector_index(source_path=source, output_path=output, provider_name="hash", hash_dimension=64)
    records = load_vector_records(output)
    hits = query_local_vector_index("公园 散步", index_path=output, provider_name="hash", top_k=1, hash_dimension=64)

    assert summary["document_count"] == 2
    assert summary["record_count"] == len(records)
    assert records[0]["embedding_dim"] == 64
    assert hits[0]["document_id"] == "case_park"
