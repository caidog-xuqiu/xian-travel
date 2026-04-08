from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.services.eval_constraints import (
    evaluate_case_constraints,
    extract_selected_plan_id,
    is_task_success,
)

PARSING_FIELDS = [
    "companion_type",
    "available_hours",
    "budget_level",
    "purpose",
    "need_meal",
    "walking_tolerance",
    "weather",
]

TOOL_KEYS = ["geocode", "search", "nearby", "weather", "route"]
TOOL_EVENT_MAP = {
    "geocode": "geocode",
    "text_search": "search",
    "nearby": "nearby",
    "weather": "weather",
    "route": "route",
}


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _match_value(field: str, predicted: Any, expected: Any) -> bool:
    if field == "available_hours":
        p = _safe_float(predicted)
        e = _safe_float(expected)
        if p is None or e is None:
            return False
        return abs(p - e) <= 0.25
    if field == "need_meal":
        return bool(predicted) == bool(expected)
    return _normalize_scalar(predicted) == _normalize_scalar(expected)


def load_case_gold(path: str | Path | None) -> Dict[str, Dict[str, Any]]:
    if path is None:
        return {}
    target = Path(path)
    if not target.exists():
        return {}

    payload = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        return {}

    indexed: Dict[str, Dict[str, Any]] = {}
    for idx, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id") or f"case_{idx:02d}")
        indexed[case_id] = item
        text = str(item.get("text") or "").strip()
        if text:
            indexed[f"text::{text}"] = item
    return indexed


def _resolve_gold_case(case_name: str, text: str, gold_index: Dict[str, Dict[str, Any]]) -> Dict[str, Any] | None:
    if not gold_index:
        return None
    if case_name in gold_index:
        return gold_index[case_name]

    compact = case_name.lower().replace("-", "_")
    if compact in gold_index:
        return gold_index[compact]

    by_text_key = f"text::{text.strip()}"
    if by_text_key in gold_index:
        return gold_index[by_text_key]

    return None


def _build_case_name(case: Dict[str, Any], index: int) -> str:
    if case.get("case_name"):
        return str(case.get("case_name"))
    if case.get("case_id"):
        return str(case.get("case_id"))
    return f"case_{index:02d}"


def _extract_pairs_warning(case: Dict[str, Any]) -> bool:
    logs = case.get("debug_logs") or []
    if not isinstance(logs, list):
        return False

    for log in logs:
        message = str(log.get("message") or "")
        if "方案差异度不足" in message:
            return True
        if "too_similar" in message:
            return True
    return False


def _extract_debug_refs(case: Dict[str, Any]) -> Dict[str, Any]:
    logs = case.get("debug_logs") or []
    if not isinstance(logs, list):
        return {"pairs_warning_logs": [], "fallback_logs": []}

    pairs_logs: List[str] = []
    fallback_logs: List[str] = []
    for log in logs:
        message = str(log.get("message") or "")
        if "方案差异度不足" in message or "too_similar" in message:
            pairs_logs.append(message)
        if "fallback" in message.lower():
            fallback_logs.append(message)

    return {
        "pairs_warning_logs": pairs_logs,
        "fallback_logs": fallback_logs,
    }


def _extract_tool_status(case: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    status = {
        key: {
            "attempted": False,
            "success": False,
            "fallback": False,
            "fallback_reasons": [],
        }
        for key in TOOL_KEYS
    }

    events = case.get("amap_events") or []
    if not isinstance(events, list):
        return status

    for event in events:
        if not isinstance(event, dict):
            continue
        raw_tool = str(event.get("amap_tool") or "").strip()
        tool = TOOL_EVENT_MAP.get(raw_tool)
        if not tool:
            continue

        attempted = bool(event.get("amap_attempted"))
        hit = bool(event.get("amap_hit"))
        fallback_reason = str(event.get("amap_fallback_reason") or "").strip()

        status[tool]["attempted"] = status[tool]["attempted"] or attempted
        status[tool]["success"] = status[tool]["success"] or (attempted and hit)

        is_fallback = bool(fallback_reason) or (attempted and not hit)
        status[tool]["fallback"] = status[tool]["fallback"] or is_fallback
        if fallback_reason:
            status[tool]["fallback_reasons"].append(fallback_reason)

    for tool in TOOL_KEYS:
        deduped = list(dict.fromkeys(status[tool]["fallback_reasons"]))
        status[tool]["fallback_reasons"] = deduped

    return status


def _extract_overall_fallback(case: Dict[str, Any], tool_status: Dict[str, Dict[str, Any]]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    fallback_reason = str(case.get("amap_fallback_reason") or "").strip()
    if fallback_reason:
        reasons.append(fallback_reason)

    for tool in TOOL_KEYS:
        if tool_status[tool]["fallback"]:
            reasons.extend(tool_status[tool]["fallback_reasons"])
            if not tool_status[tool]["fallback_reasons"]:
                reasons.append(f"{tool}_fallback")

    reasons = list(dict.fromkeys(reasons))
    return (len(reasons) > 0), reasons


def _collect_variant_averages(cases: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    bucket: Dict[str, Dict[str, float]] = {}
    counts: Dict[str, int] = {}

    for case in cases:
        summaries = case.get("candidate_plans_summary") or []
        if not isinstance(summaries, list):
            continue
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            plan_id = str(summary.get("plan_id") or "").strip()
            if not plan_id:
                continue

            stop_count = _safe_float(summary.get("stop_count")) or 0.0
            has_meal = bool(summary.get("has_meal"))
            meal_ratio = (1.0 / stop_count) if (has_meal and stop_count > 0) else 0.0

            bucket.setdefault(
                plan_id,
                {
                    "total_duration": 0.0,
                    "cross_area_count": 0.0,
                    "meal_ratio": 0.0,
                    "stop_count": 0.0,
                },
            )
            counts[plan_id] = counts.get(plan_id, 0) + 1

            bucket[plan_id]["total_duration"] += _safe_float(summary.get("total_duration_minutes")) or 0.0
            bucket[plan_id]["cross_area_count"] += _safe_float(summary.get("cross_area_count")) or 0.0
            bucket[plan_id]["meal_ratio"] += meal_ratio
            bucket[plan_id]["stop_count"] += stop_count

    result: Dict[str, Dict[str, Any]] = {}
    for plan_id, sums in bucket.items():
        n = max(1, counts.get(plan_id, 0))
        result[plan_id] = {
            "sample_count": counts.get(plan_id, 0),
            "avg_total_duration": round(sums["total_duration"] / n, 4),
            "avg_cross_area_count": round(sums["cross_area_count"] / n, 4),
            "avg_meal_ratio": round(sums["meal_ratio"] / n, 4),
            "avg_stop_count": round(sums["stop_count"] / n, 4),
        }

    return result


def _build_parsing_accuracy(
    case_details: List[Dict[str, Any]],
    gold_index: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if not gold_index:
        return {
            "available": False,
            "overall_rate": None,
            "overall_correct": 0,
            "overall_total": 0,
            "per_field": {field: {"rate": None, "correct": 0, "total": 0} for field in PARSING_FIELDS},
        }

    overall_correct = 0
    overall_total = 0
    per_field = {field: {"correct": 0, "total": 0} for field in PARSING_FIELDS}

    for detail in case_details:
        gold_case = detail.get("gold_case")
        if not isinstance(gold_case, dict):
            continue

        expected = gold_case.get("expected_parse") if isinstance(gold_case.get("expected_parse"), dict) else gold_case
        predicted = detail.get("request_context", {}).get("resolved", {})
        if not isinstance(expected, dict) or not isinstance(predicted, dict):
            continue

        for field in PARSING_FIELDS:
            if field not in expected:
                continue
            per_field[field]["total"] += 1
            overall_total += 1
            if _match_value(field, predicted.get(field), expected.get(field)):
                per_field[field]["correct"] += 1
                overall_correct += 1

    per_field_rate = {
        field: {
            "correct": stat["correct"],
            "total": stat["total"],
            "rate": round(stat["correct"] / stat["total"], 4) if stat["total"] > 0 else None,
        }
        for field, stat in per_field.items()
    }

    return {
        "available": True,
        "overall_rate": round(overall_correct / overall_total, 4) if overall_total > 0 else None,
        "overall_correct": overall_correct,
        "overall_total": overall_total,
        "per_field": per_field_rate,
    }


def evaluate_agent_cases(
    raw_cases: List[Dict[str, Any]],
    gold_index: Dict[str, Dict[str, Any]] | None = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    gold_index = gold_index or {}

    details: List[Dict[str, Any]] = []
    for idx, case in enumerate(raw_cases, start=1):
        if not isinstance(case, dict):
            continue

        case_name = _build_case_name(case, idx)
        text = str(case.get("text") or "").strip()
        gold_case = _resolve_gold_case(case_name, text, gold_index)

        constraint_bundle = evaluate_case_constraints(case)
        request_context = constraint_bundle["request_context"]
        route_stats = constraint_bundle["route_stats"]
        knowledge_context = constraint_bundle["knowledge_context"]
        constraint_eval = constraint_bundle["constraint_eval"]
        explanation_eval = constraint_bundle["explanation_eval"]

        tool_status = _extract_tool_status(case)
        overall_fallback, fallback_reasons = _extract_overall_fallback(case, tool_status)
        pairs_warning = _extract_pairs_warning(case)
        task_success = is_task_success(case)

        available_hours_check = constraint_eval["checks"].get("available_hours", {})
        time_budget_fit = available_hours_check.get("satisfied") if available_hours_check.get("applicable") else None

        detail = {
            "case_name": case_name,
            "text": text,
            "selected_plan": extract_selected_plan_id(case),
            "selected_by": str(case.get("selected_by") or "unknown"),
            "task_success": task_success,
            "constraint_satisfaction": constraint_eval["constraint_satisfaction_rate"],
            "constraint_checks": constraint_eval["checks"],
            "request_context": request_context,
            "tool_usage": {
                "amap_called": bool(case.get("amap_called")),
                "amap_sources_used": list(case.get("amap_sources_used") or []),
            },
            "tool_success": {
                tool: bool(tool_status[tool]["success"])
                for tool in TOOL_KEYS
            },
            "tool_attempted": {
                tool: bool(tool_status[tool]["attempted"])
                for tool in TOOL_KEYS
            },
            "fallback": {
                "overall_fallback": overall_fallback,
                "overall_fallback_reasons": fallback_reasons,
                "by_tool": {
                    tool: {
                        "fallback": bool(tool_status[tool]["fallback"]),
                        "reasons": list(tool_status[tool]["fallback_reasons"]),
                    }
                    for tool in TOOL_KEYS
                },
            },
            "pairs_warning": pairs_warning,
            "knowledge_used_count": int(knowledge_context.get("knowledge_used_count") or 0),
            "knowledge_ids": list(knowledge_context.get("knowledge_ids") or []),
            "knowledge_bias": dict(knowledge_context.get("knowledge_bias") or {}),
            "explanation_basis": list(knowledge_context.get("explanation_basis") or []),
            "explanation_consistency": explanation_eval,
            "time_budget_fit": time_budget_fit,
            "route_stats": route_stats,
            "debug_refs": _extract_debug_refs(case),
            "gold_case": gold_case,
            "raw": case,
        }
        details.append(detail)

    total_cases = len(details)
    if total_cases == 0:
        return {
            "total_cases": 0,
            "task_success_rate": 0.0,
        }, []

    task_success_cases = sum(1 for item in details if item["task_success"])

    # Weighted by applicable constraints.
    weighted_applicable = 0
    weighted_satisfied = 0
    for item in details:
        checks = item.get("constraint_checks") or {}
        for check in checks.values():
            if check.get("applicable"):
                weighted_applicable += 1
                if check.get("satisfied") is True:
                    weighted_satisfied += 1

    constraint_rate = round(weighted_satisfied / weighted_applicable, 4) if weighted_applicable > 0 else 1.0

    amap_called_cases = sum(1 for item in details if item["tool_usage"]["amap_called"])

    tool_attempted_counts = {tool: 0 for tool in TOOL_KEYS}
    tool_success_counts = {tool: 0 for tool in TOOL_KEYS}
    tool_fallback_case_counts = {tool: 0 for tool in TOOL_KEYS}

    for item in details:
        for tool in TOOL_KEYS:
            if item["tool_attempted"][tool]:
                tool_attempted_counts[tool] += 1
            if item["tool_success"][tool]:
                tool_success_counts[tool] += 1
            if item["fallback"]["by_tool"][tool]["fallback"]:
                tool_fallback_case_counts[tool] += 1

    overall_fallback_cases = sum(1 for item in details if item["fallback"]["overall_fallback"])
    pairs_warning_cases = sum(1 for item in details if item["pairs_warning"])

    time_budget_applicable = [item for item in details if item["time_budget_fit"] is not None]
    time_budget_fit_cases = sum(1 for item in time_budget_applicable if item["time_budget_fit"] is True)

    knowledge_used_cases = sum(1 for item in details if int(item["knowledge_used_count"]) > 0)
    explanation_consistent_cases = sum(1 for item in details if item["explanation_consistency"]["explanation_consistent"])

    parsing_accuracy = _build_parsing_accuracy(details, gold_index)

    variant_averages = _collect_variant_averages([item["raw"] for item in details])

    summary = {
        "total_cases": total_cases,
        "task_success_rate": round(task_success_cases / total_cases, 4),
        "constraint_satisfaction_rate": constraint_rate,
        "request_parsing_accuracy": parsing_accuracy,
        "amap_called_rate": round(amap_called_cases / total_cases, 4),
        "tool_success_rate": {
            f"{tool}_success_rate": round(tool_success_counts[tool] / tool_attempted_counts[tool], 4)
            if tool_attempted_counts[tool] > 0
            else 0.0
            for tool in TOOL_KEYS
        },
        "tool_attempted_counts": tool_attempted_counts,
        "tool_success_counts": tool_success_counts,
        "fallback_rate": {
            f"{tool}_fallback_rate": round(tool_fallback_case_counts[tool] / total_cases, 4)
            for tool in TOOL_KEYS
        }
        | {"overall_fallback_rate": round(overall_fallback_cases / total_cases, 4)},
        "pairs_warning_rate": round(pairs_warning_cases / total_cases, 4),
        "candidate_variant_averages": variant_averages,
        "time_budget_fit_rate": round(time_budget_fit_cases / len(time_budget_applicable), 4)
        if time_budget_applicable
        else 0.0,
        "knowledge_used_rate": round(knowledge_used_cases / total_cases, 4),
        "explanation_consistency_rate": round(explanation_consistent_cases / total_cases, 4),
        "counts": {
            "task_success_cases": task_success_cases,
            "pairs_warning_cases": pairs_warning_cases,
            "overall_fallback_cases": overall_fallback_cases,
            "knowledge_used_cases": knowledge_used_cases,
            "time_budget_fit_cases": time_budget_fit_cases,
            "time_budget_applicable_cases": len(time_budget_applicable),
        },
    }

    # Strip raw payload and embedded gold from exported details.
    cleaned_details: List[Dict[str, Any]] = []
    for item in details:
        exported = dict(item)
        exported.pop("raw", None)
        exported.pop("gold_case", None)
        cleaned_details.append(exported)

    return summary, cleaned_details
