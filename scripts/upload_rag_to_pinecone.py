from __future__ import annotations

import argparse
import json

from app.services.rag_pinecone_store import PineconeConfig, RestPineconeVectorStore
from app.services.rag_vector_index import load_vector_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload local RAG vector records to Pinecone.")
    parser.add_argument("--index-file", default="app/data/rag_index/xian_route_case_vectors.jsonl")
    parser.add_argument("--namespace", default="", help="Override Pinecone namespace")
    parser.add_argument("--dry-run", action="store_true", help="Only print what would be uploaded")
    args = parser.parse_args()

    records = load_vector_records(args.index_file)
    namespace = args.namespace or None
    if args.dry_run:
        first = records[0] if records else {}
        print(
            json.dumps(
                {
                    "index_file": args.index_file,
                    "record_count": len(records),
                    "namespace": namespace or "<env/default>",
                    "first_record": {
                        "id": first.get("id"),
                        "document_id": first.get("document_id"),
                        "embedding_dim": first.get("embedding_dim"),
                        "metadata_keys": sorted((first.get("metadata") or {}).keys()),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    store = RestPineconeVectorStore(PineconeConfig.from_env())
    result = store.upsert(records, namespace=namespace)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

