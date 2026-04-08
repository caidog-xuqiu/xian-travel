from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.services.evaluation_harness import (
    compare_eval_results,
    load_eval_cases,
    run_eval_for_endpoint,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fixed evaluation regression for v2/v3/v4_current.")
    parser.add_argument("--cases", default="data/eval_cases.json", help="Path to eval cases json file.")
    parser.add_argument("--output-dir", default="eval_results", help="Directory to save eval outputs.")
    parser.add_argument("--user-key", default="eval_runner", help="Optional user key for agent v3.")
    args = parser.parse_args()

    cases = load_eval_cases(args.cases)
    if not cases:
        raise SystemExit("No eval cases loaded.")

    v2_results = run_eval_for_endpoint("v2", cases, options={"user_key": args.user_key})
    v3_results = run_eval_for_endpoint("v3", cases, options={"user_key": args.user_key})
    v4_results = run_eval_for_endpoint("v4_current", cases, options={"user_key": args.user_key})
    compare = compare_eval_results(v2_results=v2_results, v3_results=v3_results, v4_results=v4_results)

    output_dir = Path(args.output_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    summary_payload = {
        "v2": v2_results.get("summary", {}),
        "v3": v3_results.get("summary", {}),
        "v4_current": v4_results.get("summary", {}),
    }
    details_payload = {
        "v2": v2_results.get("details", []),
        "v3": v3_results.get("details", []),
        "v4_current": v4_results.get("details", []),
    }

    _write_json(run_dir / "summary.json", summary_payload)
    _write_json(run_dir / "details.json", details_payload)
    _write_json(run_dir / "compare.json", compare)

    # Keep a stable latest snapshot for quick inspection.
    _write_json(output_dir / "summary.json", summary_payload)
    _write_json(output_dir / "details.json", details_payload)
    _write_json(output_dir / "compare.json", compare)

    print("Evaluation finished.")
    print(f"cases={len(cases)}")
    print(f"output={run_dir}")
    print("v2 summary:", json.dumps(summary_payload["v2"], ensure_ascii=False))
    print("v3 summary:", json.dumps(summary_payload["v3"], ensure_ascii=False))
    print("v4_current summary:", json.dumps(summary_payload["v4_current"], ensure_ascii=False))


if __name__ == "__main__":
    main()
