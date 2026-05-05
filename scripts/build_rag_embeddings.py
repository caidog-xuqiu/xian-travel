from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.rag_vector_index import DEFAULT_INDEX, DEFAULT_SOURCE, build_local_vector_index, query_local_vector_index


def main() -> int:
    parser = argparse.ArgumentParser(description="Build local RAG chunk embeddings from route-case JSONL.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Input JSONL source path")
    parser.add_argument("--output", default=str(DEFAULT_INDEX), help="Output vector JSONL path")
    parser.add_argument("--provider", default="hash", help="Embedding provider: hash or sentence_transformer")
    parser.add_argument("--chunk-size", type=int, default=420)
    parser.add_argument("--chunk-overlap", type=int, default=60)
    parser.add_argument("--hash-dim", type=int, default=384)
    parser.add_argument("--query", default="", help="Optional local retrieval query after building")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    summary = build_local_vector_index(
        source_path=args.source,
        output_path=args.output,
        provider_name=args.provider,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        hash_dimension=args.hash_dim,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.query:
        hits = query_local_vector_index(
            args.query,
            index_path=args.output,
            provider_name=args.provider,
            top_k=args.top_k,
            hash_dimension=args.hash_dim,
        )
        preview = [
            {
                "id": item.get("id"),
                "document_id": item.get("document_id"),
                "score": item.get("score"),
                "route_tags": (item.get("metadata") or {}).get("route_tags"),
                "text_preview": str(item.get("text") or "")[:160],
            }
            for item in hits
        ]
        print(json.dumps(preview, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
