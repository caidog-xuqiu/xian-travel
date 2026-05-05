from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Protocol


class VectorStoreBackend(Protocol):
    name: str

    def upsert(self, records: Iterable[Dict[str, Any]], *, namespace: str | None = None) -> Dict[str, Any]: ...

    def query(
        self,
        vector: List[float],
        *,
        top_k: int = 5,
        namespace: str | None = None,
        metadata_filter: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]: ...


@dataclass(frozen=True)
class PineconeConfig:
    api_key: str
    index_name: str = "xian-travel-rag"
    namespace: str = "route_cases_v1"

    @classmethod
    def from_env(cls) -> "PineconeConfig":
        api_key = os.getenv("PINECONE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("PINECONE_API_KEY is required for Pinecone vector store")
        return cls(
            api_key=api_key,
            index_name=os.getenv("PINECONE_INDEX_NAME", "xian-travel-rag").strip() or "xian-travel-rag",
            namespace=os.getenv("PINECONE_NAMESPACE", "route_cases_v1").strip() or "route_cases_v1",
        )


class PineconeVectorStore:
    name = "pinecone"

    def __init__(self, config: PineconeConfig | None = None, *, client: Any | None = None, index: Any | None = None) -> None:
        self.config = config or PineconeConfig.from_env()
        if index is not None:
            self._index = index
            return
        if client is None:
            try:
                from pinecone import Pinecone  # type: ignore
            except ImportError as exc:
                raise RuntimeError("pinecone package is not installed. Install pinecone before using this backend.") from exc
            client = Pinecone(api_key=self.config.api_key)
        self._index = client.Index(self.config.index_name)

    def upsert(self, records: Iterable[Dict[str, Any]], *, namespace: str | None = None) -> Dict[str, Any]:
        vectors = [_record_to_pinecone_vector(record) for record in records]
        if not vectors:
            return {"backend": self.name, "upserted_count": 0, "namespace": namespace or self.config.namespace}
        result = self._index.upsert(vectors=vectors, namespace=namespace or self.config.namespace)
        return {
            "backend": self.name,
            "upserted_count": len(vectors),
            "namespace": namespace or self.config.namespace,
            "raw_result": _to_plain_dict(result),
        }

    def query(
        self,
        vector: List[float],
        *,
        top_k: int = 5,
        namespace: str | None = None,
        metadata_filter: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        result = self._index.query(
            vector=vector,
            top_k=top_k,
            namespace=namespace or self.config.namespace,
            filter=metadata_filter,
            include_metadata=True,
        )
        payload = _to_plain_dict(result)
        matches = payload.get("matches") or []
        return [
            {
                "id": match.get("id"),
                "score": match.get("score"),
                "metadata": match.get("metadata") or {},
            }
            for match in matches
        ]



class RestPineconeVectorStore:
    name = "pinecone_rest"

    def __init__(self, config: PineconeConfig | None = None, *, session: Any | None = None, host: str | None = None) -> None:
        self.config = config or PineconeConfig.from_env()
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._host = (host or self._describe_index().get("host") or "").strip()
        if not self._host:
            raise RuntimeError(f"Pinecone index host not found for {self.config.index_name}")

    def upsert(self, records: Iterable[Dict[str, Any]], *, namespace: str | None = None) -> Dict[str, Any]:
        vectors = [_record_to_pinecone_vector(record) for record in records]
        if not vectors:
            return {"backend": self.name, "upserted_count": 0, "namespace": namespace or self.config.namespace}
        payload = {"vectors": vectors, "namespace": namespace or self.config.namespace}
        response = self._session.post(
            f"https://{self._host}/vectors/upsert",
            headers=self._headers(),
            json=payload,
            timeout=60,
        )
        self._raise_for_status(response)
        body = response.json() if getattr(response, "content", b"") else {}
        return {
            "backend": self.name,
            "upserted_count": int(body.get("upsertedCount") or body.get("upserted_count") or len(vectors)),
            "namespace": namespace or self.config.namespace,
            "raw_result": body,
        }

    def query(
        self,
        vector: List[float],
        *,
        top_k: int = 5,
        namespace: str | None = None,
        metadata_filter: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "vector": [float(value) for value in vector],
            "topK": int(top_k),
            "namespace": namespace or self.config.namespace,
            "includeMetadata": True,
        }
        if metadata_filter:
            payload["filter"] = metadata_filter
        response = self._session.post(
            f"https://{self._host}/query",
            headers=self._headers(),
            json=payload,
            timeout=60,
        )
        self._raise_for_status(response)
        body = response.json() if getattr(response, "content", b"") else {}
        return [
            {
                "id": match.get("id"),
                "score": match.get("score"),
                "metadata": match.get("metadata") or {},
            }
            for match in (body.get("matches") or [])
        ]

    def _describe_index(self) -> Dict[str, Any]:
        response = self._session.get(
            f"https://api.pinecone.io/indexes/{self.config.index_name}",
            headers=self._headers(),
            timeout=30,
        )
        self._raise_for_status(response)
        return response.json()

    def _headers(self) -> Dict[str, str]:
        return {
            "Api-Key": self.config.api_key,
            "Content-Type": "application/json",
            "X-Pinecone-Api-Version": "2024-07",
        }

    @staticmethod
    def _raise_for_status(response: Any) -> None:
        status_code = int(getattr(response, "status_code", 200))
        if 200 <= status_code < 300:
            return
        text = getattr(response, "text", "")
        raise RuntimeError(f"Pinecone REST request failed: status={status_code}, body={text[:500]}")

def _record_to_pinecone_vector(record: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(record.get("metadata") or {})
    metadata.update(
        {
            "document_id": record.get("document_id"),
            "chunk_index": record.get("chunk_index"),
            "text": record.get("text") or "",
            "embedding_provider": record.get("embedding_provider"),
            "embedding_dim": record.get("embedding_dim"),
        }
    )
    return {
        "id": str(record.get("id") or ""),
        "values": [float(value) for value in (record.get("embedding") or [])],
        "metadata": _sanitize_metadata(metadata),
    }


def _sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        elif isinstance(value, list):
            clean[key] = [str(item) for item in value if item is not None]
        else:
            clean[key] = str(value)
    return clean


def _to_plain_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {"repr": repr(value)}

