from __future__ import annotations

from app.services.rag_pinecone_store import PineconeConfig, PineconeVectorStore, RestPineconeVectorStore, _record_to_pinecone_vector


class FakeIndex:
    def __init__(self) -> None:
        self.upsert_calls = []
        self.query_calls = []

    def upsert(self, **kwargs):
        self.upsert_calls.append(kwargs)
        return {"upserted_count": len(kwargs.get("vectors") or [])}

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return {
            "matches": [
                {"id": "case_001::chunk_000", "score": 0.9, "metadata": {"document_id": "case_001", "text": "示例"}}
            ]
        }


def _record():
    return {
        "id": "case_001::chunk_000",
        "document_id": "case_001",
        "chunk_index": 0,
        "text": "用户需求：想看夜景",
        "metadata": {"route_tags": ["night_view", "meal"], "stop_count": 3, "nested": {"x": 1}},
        "embedding": [0.1, 0.2, 0.3],
        "embedding_provider": "hash_embedding_v1",
        "embedding_dim": 3,
    }


def test_record_to_pinecone_vector_sanitizes_metadata() -> None:
    vector = _record_to_pinecone_vector(_record())
    assert vector["id"] == "case_001::chunk_000"
    assert vector["values"] == [0.1, 0.2, 0.3]
    assert vector["metadata"]["route_tags"] == ["night_view", "meal"]
    assert vector["metadata"]["nested"] == "{'x': 1}"
    assert vector["metadata"]["text"] == "用户需求：想看夜景"


def test_pinecone_store_upsert_and_query_with_fake_index() -> None:
    fake = FakeIndex()
    store = PineconeVectorStore(
        PineconeConfig(api_key="test-key", index_name="test-index", namespace="route_cases_v1"),
        index=fake,
    )

    result = store.upsert([_record()])
    hits = store.query([0.1, 0.2, 0.3], top_k=1)

    assert result["upserted_count"] == 1
    assert fake.upsert_calls[0]["namespace"] == "route_cases_v1"
    assert fake.upsert_calls[0]["vectors"][0]["id"] == "case_001::chunk_000"
    assert fake.query_calls[0]["include_metadata"] is True
    assert hits[0]["metadata"]["document_id"] == "case_001"


class FakeRestResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)
        self.content = b"x"

    def json(self):
        return self._payload


class FakeRestSession:
    def __init__(self):
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeRestResponse({"host": "fake-host.pinecone.io", "dimension": 512})

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if url.endswith("/query"):
            return FakeRestResponse({"matches": [{"id": "case_001::chunk_000", "score": 0.8, "metadata": {"document_id": "case_001"}}]})
        return FakeRestResponse({"upsertedCount": len(kwargs.get("json", {}).get("vectors", []))})


def test_rest_pinecone_store_uses_describe_host_and_data_endpoints() -> None:
    session = FakeRestSession()
    store = RestPineconeVectorStore(
        PineconeConfig(api_key="test-key", index_name="test-index", namespace="route_cases_v1"),
        session=session,
    )

    result = store.upsert([_record()])
    hits = store.query([0.1, 0.2, 0.3], top_k=1)

    assert session.get_calls[0][0] == "https://api.pinecone.io/indexes/test-index"
    assert session.post_calls[0][0] == "https://fake-host.pinecone.io/vectors/upsert"
    assert session.post_calls[1][0] == "https://fake-host.pinecone.io/query"
    assert result["backend"] == "pinecone_rest"
    assert result["upserted_count"] == 1
    assert hits[0]["metadata"]["document_id"] == "case_001"
