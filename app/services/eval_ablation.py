from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterable, List

import app.services.agent_graph as agent_graph
from app.services.eval_constraints import evaluate_case_constraints
from app.services.knowledge_adapter import build_knowledge_bias as _build_knowledge_bias


METRIC_KEYS = [
    "stop_count",
    "cross_area_count",
    "total_duration_minutes",
    "constraint_satisfaction_rate",
]


def _to_plain_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return {}


@contextmanager
def disable_local_knowledge_bias() -> Any:
    original_retrieve = agent_graph.retrieve_knowledge
    original_build = agent_graph.build_knowledge_bias

    def _empty_retrieve(_request_context: Dict[str, Any], top_k: int = 3) -> List[Dict[str, Any]]:
        return []

    def _empty_bias(_snippets: List[Dict[str, Any]]) -> Dict[str, Any]:
        return _build_knowledge_bias([])

    agent_graph.retrieve_knowledge = _empty_retrieve
    agent_graph.build_knowledge_bias = _empty_bias
    try:
        yield
    finally:
        agent_graph.retrieve_knowledge = original_retrieve
        agent_graph.build_knowledge_bias = original_build


def _extract_case_metrics(case_payload: Dict[str, Any]) -> Dict[str, Any]:
    bundle = evaluate_case_constraints(case_payload)
    route_stats = bundle["route_stats"]
    constraint_eval = bundle["constraint_eval"]

    return {
        "stop_count": int(route_stats.get("stop_count", 0) or 0),
        "cross_area_count": int(route_stats.get("cross_area_count", 0) or 0),
        "total_duration_minutes": float(route_stats.get("total_duration_minutes", 0.0) or 0.0),
        "constraint_satisfaction_rate": float(constraint_eval.get("constraint_satisfaction_rate", 0.0) or 0.0),
    }


def compute_knowledge_gain(
    with_knowledge: Iterable[Dict[str, Any]],
    without_knowledge: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    without_map = {str(item.get("case_name") or item.get("text") or ""): item for item in without_knowledge}

    rows: List[Dict[str, Any]] = []
    deltas = {key: 0.0 for key in METRIC_KEYS}
    matched = 0

    for with_case in with_knowledge:
        case_key = str(with_case.get("case_name") or with_case.get("text") or "")
        without_case = without_map.get(case_key)
        if without_case is None:
            continue

        matched += 1
        delta_row = {
            "case_name": case_key,
            "with_knowledge": {},
            "without_knowledge": {},
            "delta": {},
        }

        for key in METRIC_KEYS:
            with_value = float(with_case.get(key, 0.0) or 0.0)
            without_value = float(without_case.get(key, 0.0) or 0.0)
            delta_value = with_value - without_value

            deltas[key] += delta_value
            delta_row["with_knowledge"][key] = with_value
            delta_row["without_knowledge"][key] = without_value
            delta_row["delta"][key] = round(delta_value, 4)

        rows.append(delta_row)

    averages = {
        f"avg_{key}_delta": round(deltas[key] / matched, 4) if matched > 0 else 0.0
        for key in METRIC_KEYS
    }

    return {
        "case_count": matched,
        "averages": averages,
        "cases": rows,
    }


def run_knowledge_ablation(
    case_texts: Iterable[str],
    *,
    max_cases: int = 6,
    user_key: str = "eval_ablation",
) -> Dict[str, Any]:
    unique_texts: List[str] = []
    for text in case_texts:
        normalized = str(text or "").strip()
        if not normalized:
            continue
        if normalized in unique_texts:
            continue
        unique_texts.append(normalized)
        if len(unique_texts) >= max_cases:
            break

    with_cases: List[Dict[str, Any]] = []
    without_cases: List[Dict[str, Any]] = []

    for idx, text in enumerate(unique_texts, start=1):
        case_name = f"ablation_{idx:02d}"

        with_resp = agent_graph.run_agent_v4_current(text=text, user_key=user_key)
        with_payload = _to_plain_dict(with_resp)
        with_payload["text"] = text
        with_metrics = _extract_case_metrics(with_payload)
        with_cases.append({"case_name": case_name, "text": text, **with_metrics})

        with disable_local_knowledge_bias():
            without_resp = agent_graph.run_agent_v4_current(text=text, user_key=user_key)
        without_payload = _to_plain_dict(without_resp)
        without_payload["text"] = text
        without_metrics = _extract_case_metrics(without_payload)
        without_cases.append({"case_name": case_name, "text": text, **without_metrics})

    result = compute_knowledge_gain(with_knowledge=with_cases, without_knowledge=without_cases)
    result["with_knowledge"] = with_cases
    result["without_knowledge"] = without_cases
    return result
