from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from app.services.eval_ablation import run_knowledge_ablation
from app.services.eval_metrics import evaluate_agent_cases, load_case_gold


DEFAULT_GOLD = Path("app/data/case_gold.json")
DEFAULT_SUMMARY = Path("eval_results/agent_eval_summary.json")
DEFAULT_DETAILS = Path("eval_results/agent_eval_details.json")


def _pick_latest(pattern: str) -> Path | None:
    files = [Path(p) for p in glob.glob(pattern)]
    files = [p for p in files if p.exists()]
    if not files:
        return None
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return files[0]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_cases(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("v4_current"), list):
            return [dict(item) for item in payload["v4_current"] if isinstance(item, dict)]
        if isinstance(payload.get("details"), list):
            return [dict(item) for item in payload["details"] if isinstance(item, dict)]
    return []


def _attach_case_ids(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(cases, start=1):
        copied = dict(item)
        copied.setdefault("case_id", f"case_{idx:02d}")
        normalized.append(copied)
    return normalized


def _write_json(path: Path, payload: Dict[str, Any] | List[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run formal agent evaluation metrics and export summary/details")
    parser.add_argument(
        "--details",
        type=str,
        default=None,
        help="Path to regression details json. Default: latest eval_results/amap_real_regression_details_*.json",
    )
    parser.add_argument(
        "--smoke",
        type=str,
        default=None,
        help="Path to smoke summary json. Default: latest eval_results/amap_smoke_summary_*.json",
    )
    parser.add_argument("--gold", type=str, default=str(DEFAULT_GOLD), help="Path to case gold labels json")
    parser.add_argument("--summary-out", type=str, default=str(DEFAULT_SUMMARY), help="Summary output path")
    parser.add_argument("--details-out", type=str, default=str(DEFAULT_DETAILS), help="Details output path")
    parser.add_argument("--skip-ablation", action="store_true", help="Skip knowledge A/B ablation")
    parser.add_argument("--ablation-max-cases", type=int, default=6, help="Max case count for knowledge ablation")
    args = parser.parse_args()

    details_path = Path(args.details) if args.details else _pick_latest("eval_results/amap_real_regression_details_*.json")
    if details_path is None or not details_path.exists():
        raise SystemExit("No regression details file found.")

    smoke_path = Path(args.smoke) if args.smoke else _pick_latest("eval_results/amap_smoke_summary_*.json")

    raw_payload = _load_json(details_path)
    raw_cases = _normalize_cases(raw_payload)
    raw_cases = _attach_case_ids(raw_cases)

    gold_index = load_case_gold(args.gold)
    summary, details = evaluate_agent_cases(raw_cases=raw_cases, gold_index=gold_index)

    if smoke_path and smoke_path.exists():
        summary["smoke_file"] = str(smoke_path)
        summary["smoke_snapshot"] = _load_json(smoke_path)

    if not args.skip_ablation:
        texts = [str(case.get("text") or "").strip() for case in raw_cases]
        texts = [text for text in texts if text]
        summary["knowledge_gain"] = run_knowledge_ablation(
            case_texts=texts,
            max_cases=max(1, int(args.ablation_max_cases)),
            user_key="agent_eval_ablation",
        )

    summary_payload: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "details_file": str(details_path),
        "gold_file": str(Path(args.gold)),
        "metrics": summary,
    }

    summary_out = Path(args.summary_out)
    details_out = Path(args.details_out)
    _write_json(summary_out, summary_payload)
    _write_json(details_out, details)

    print(f"[ok] summary={summary_out}")
    print(f"[ok] details={details_out}")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
