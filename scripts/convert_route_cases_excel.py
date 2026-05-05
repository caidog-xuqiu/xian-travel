from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.rag_case_importer import load_route_cases_from_xlsx, write_jsonl

DEFAULT_INPUT = Path.home() / "Desktop" / "西安路线案例采集模板第一样板.xlsx"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "app" / "data" / "rag_sources" / "xian_route_cases_seed.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert route-case Excel workbook to RAG-ready JSONL.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Path to route-case .xlsx file")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path to output .jsonl file")
    parser.add_argument("--preview", action="store_true", help="Print a compact preview after conversion")
    args = parser.parse_args()

    cases = load_route_cases_from_xlsx(args.input)
    count = write_jsonl(cases, args.output)
    print(json.dumps({"input": args.input, "output": args.output, "case_count": count}, ensure_ascii=False, indent=2))
    if args.preview:
        preview = [
            {
                "id": item.get("id"),
                "user_query": item.get("user_query"),
                "scene_context": item.get("scene_context"),
                "route_tags": item.get("route_tags"),
                "route_text": item.get("route_text"),
            }
            for item in cases[:5]
        ]
        print(json.dumps(preview, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
