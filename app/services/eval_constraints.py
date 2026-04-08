from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Tuple

from app.services.area_registry import map_place_to_area
from app.services.request_parser import parse_free_text_to_plan_request

CONSTRAINT_FIELDS = [
    "need_meal",
    "available_hours",
    "walking_tolerance",
    "weather",
    "companion_type",
    "purpose",
]


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return {}


def _extract_debug_message_payload(debug_logs: List[Dict[str, Any]], prefix: str) -> Any:
    for log in reversed(debug_logs):
        message = str(log.get("message") or "")
        if not message.startswith(prefix):
            continue
        payload_text = message[len(prefix) :].strip()
        try:
            return ast.literal_eval(payload_text)
        except Exception:
            return None
    return None


def _extract_knowledge_from_debug_logs(debug_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    knowledge_count = 0
    knowledge_ids: List[str] = []

    for log in reversed(debug_logs):
        message = str(log.get("message") or "")
        if "local knowledge retrieved count=" not in message:
            continue
        match = re.search(r"count=(\d+)", message)
        if match:
            knowledge_count = int(match.group(1))
        ids_match = re.search(r"ids=(\[.*\])", message)
        if ids_match:
            try:
                parsed_ids = ast.literal_eval(ids_match.group(1))
                if isinstance(parsed_ids, list):
                    knowledge_ids = [str(x) for x in parsed_ids]
            except Exception:
                pass
        break

    knowledge_bias = _extract_debug_message_payload(debug_logs, "knowledge_bias=") or {}
    explanation_basis = _extract_debug_message_payload(debug_logs, "explanation_basis=") or []

    if not isinstance(knowledge_bias, dict):
        knowledge_bias = {}
    if not isinstance(explanation_basis, list):
        explanation_basis = []

    return {
        "knowledge_used_count": knowledge_count,
        "knowledge_ids": knowledge_ids,
        "knowledge_bias": knowledge_bias,
        "explanation_basis": [str(x) for x in explanation_basis],
    }


def resolve_request_context(case: Dict[str, Any]) -> Dict[str, Any]:
    parsed_request = _as_dict(case.get("parsed_request"))
    if not parsed_request:
        text = str(case.get("text") or "").strip()
        if text:
            try:
                parsed_request = _as_dict(parse_free_text_to_plan_request(text))
            except Exception:
                parsed_request = {}

    resolved = {
        "companion_type": str(_enum_value(parsed_request.get("companion_type") or "") or "").strip().lower(),
        "available_hours": _safe_float(_enum_value(parsed_request.get("available_hours"))),
        "budget_level": str(_enum_value(parsed_request.get("budget_level") or "") or "").strip().lower(),
        "purpose": str(_enum_value(parsed_request.get("purpose") or "") or "").strip().lower(),
        "need_meal": bool(parsed_request.get("need_meal")) if parsed_request.get("need_meal") is not None else None,
        "walking_tolerance": str(_enum_value(parsed_request.get("walking_tolerance") or "") or "").strip().lower(),
        "weather": str(_enum_value(parsed_request.get("weather") or "") or "").strip().lower(),
        "preferred_period": str(_enum_value(parsed_request.get("preferred_period") or "") or "").strip().lower(),
    }

    return {
        "parsed_request": parsed_request,
        "resolved": resolved,
    }


def extract_selected_plan_id(case: Dict[str, Any]) -> str | None:
    if case.get("selected_plan_id"):
        return str(case.get("selected_plan_id"))

    debug_logs = case.get("debug_logs") or []
    for log in reversed(debug_logs):
        message = str(log.get("message") or "")
        match = re.search(r"最终方案=([^，,\s]+)", message)
        if match:
            return str(match.group(1)).strip()
    return None


def _parse_time_slot_minutes(time_slot: str) -> Tuple[int, int] | None:
    text = str(time_slot or "")
    match = re.search(r"(\d{1,2}):(\d{2})\s*[-~]\s*(\d{1,2}):(\d{2})", text)
    if not match:
        return None
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    if end < start:
        end += 24 * 60
    return start, end


def compute_route_stats(case: Dict[str, Any]) -> Dict[str, Any]:
    selected_plan = _as_dict(case.get("selected_plan"))
    route = selected_plan.get("route") if isinstance(selected_plan.get("route"), list) else []

    meal_count = 0
    sight_count = 0
    clusters: List[str] = []
    areas: List[str] = []
    leg_duration_sum = 0
    max_leg_duration = 0
    start_minutes: List[int] = []
    end_minutes: List[int] = []

    for item in route:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type == "restaurant":
            meal_count += 1
        if item_type == "sight":
            sight_count += 1

        cluster = str(item.get("district_cluster") or "")
        if cluster and cluster not in clusters:
            clusters.append(cluster)

        area = map_place_to_area(
            {
                "name": str(item.get("name") or ""),
                "district_cluster": cluster,
            }
        )
        if area and area != "unknown" and area not in areas:
            areas.append(area)

        leg_minutes = _safe_int(item.get("estimated_duration_minutes")) or 0
        leg_duration_sum += max(0, leg_minutes)
        max_leg_duration = max(max_leg_duration, max(0, leg_minutes))

        slot = _parse_time_slot_minutes(str(item.get("time_slot") or ""))
        if slot is not None:
            start_minutes.append(slot[0])
            end_minutes.append(slot[1])

    scheduled_duration_minutes = None
    if start_minutes and end_minutes:
        scheduled_duration_minutes = max(end_minutes) - min(start_minutes)

    total_duration_minutes = scheduled_duration_minutes if scheduled_duration_minutes is not None else leg_duration_sum
    stop_count = len(route)
    meal_ratio = (meal_count / stop_count) if stop_count > 0 else 0.0

    night_cluster_hit = any("夜游" in c for c in clusters)
    night_name_hit = any(
        keyword in " ".join(str(item.get("name") or "") for item in route)
        for keyword in ["夜", "不夜城", "曲江", "芙蓉园"]
    )

    return {
        "stop_count": stop_count,
        "meal_count": meal_count,
        "sight_count": sight_count,
        "meal_ratio": round(meal_ratio, 4),
        "clusters": clusters,
        "areas": areas,
        "cross_cluster_count": max(0, len(clusters) - 1),
        "cross_area_count": max(0, len(areas) - 1),
        "leg_duration_sum": leg_duration_sum,
        "max_leg_duration": max_leg_duration,
        "scheduled_duration_minutes": scheduled_duration_minutes,
        "total_duration_minutes": total_duration_minutes,
        "is_night_route": bool(night_cluster_hit or night_name_hit),
        "has_valid_route": stop_count > 0,
    }


def extract_knowledge_context(case: Dict[str, Any]) -> Dict[str, Any]:
    explicit_count = _safe_int(case.get("knowledge_used_count"))
    explicit_ids = case.get("knowledge_ids") or []
    explicit_bias = case.get("knowledge_bias") or {}
    explicit_basis = case.get("explanation_basis") or []

    if explicit_count is not None or explicit_ids or explicit_bias or explicit_basis:
        return {
            "knowledge_used_count": int(explicit_count or 0),
            "knowledge_ids": [str(x) for x in explicit_ids],
            "knowledge_bias": dict(explicit_bias) if isinstance(explicit_bias, dict) else {},
            "explanation_basis": [str(x) for x in explicit_basis],
        }

    debug_logs = case.get("debug_logs") or []
    return _extract_knowledge_from_debug_logs(debug_logs if isinstance(debug_logs, list) else [])


def evaluate_constraint_satisfaction(
    request_context: Dict[str, Any],
    route_stats: Dict[str, Any],
    explanation_basis: List[str] | None = None,
    knowledge_bias: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    resolved = request_context.get("resolved") if isinstance(request_context.get("resolved"), dict) else {}
    basis_text = " ".join(str(x) for x in (explanation_basis or []))
    knowledge_bias = knowledge_bias or {}

    checks: Dict[str, Dict[str, Any]] = {}

    def _add_check(name: str, applicable: bool, satisfied: bool | None, note: str = "") -> None:
        checks[name] = {
            "applicable": bool(applicable),
            "satisfied": None if not applicable else bool(satisfied),
            "note": note,
        }

    need_meal = resolved.get("need_meal") is True
    _add_check(
        "need_meal",
        need_meal,
        route_stats.get("meal_count", 0) > 0,
        "need_meal=true 时应包含餐饮节点",
    )

    available_hours = _safe_float(resolved.get("available_hours"))
    total_minutes = _safe_float(route_stats.get("total_duration_minutes"))
    budget_applicable = available_hours is not None and available_hours > 0
    _add_check(
        "available_hours",
        budget_applicable,
        (total_minutes is not None) and (total_minutes <= available_hours * 60),
        "总时长不超过 available_hours",
    )

    low_walk_intent = str(resolved.get("walking_tolerance") or "") == "low"
    low_walk_hit = (
        int(route_stats.get("cross_area_count", 0)) <= 1
        and int(route_stats.get("stop_count", 0)) <= 3
        and int(route_stats.get("max_leg_duration", 0)) <= 25
    )
    _add_check(
        "walking_tolerance",
        low_walk_intent,
        low_walk_hit,
        "低步行偏好应控制跨区、站点和单段时长",
    )

    weather = str(resolved.get("weather") or "")
    weather_applicable = weather in {"rainy", "hot"}
    weather_hit = (
        bool(knowledge_bias.get("prefer_indoor"))
        or bool(knowledge_bias.get("prefer_low_walk"))
        or ("室内" in basis_text)
        or ("雨天" in basis_text)
        or low_walk_hit
    )
    _add_check(
        "weather",
        weather_applicable,
        weather_hit,
        "雨天/高温应偏室内或低步行策略",
    )

    companion = str(resolved.get("companion_type") or "")
    parents_applicable = companion == "parents"
    parents_hit = (
        int(route_stats.get("stop_count", 0)) <= 3
        and int(route_stats.get("cross_area_count", 0)) <= 1
    )
    _add_check(
        "companion_type",
        parents_applicable,
        parents_hit,
        "陪父母应偏低强度、少跨区",
    )

    purpose = str(resolved.get("purpose") or "")
    purpose_applicable = purpose in {"dating", "food", "relax", "tourism"}
    purpose_hit = True
    if purpose == "dating":
        purpose_hit = bool(route_stats.get("meal_count", 0) > 0 and route_stats.get("is_night_route", False))
    elif purpose == "food":
        purpose_hit = bool(route_stats.get("meal_count", 0) > 0 and float(route_stats.get("meal_ratio", 0.0)) >= 0.3)
    elif purpose == "relax":
        purpose_hit = bool(
            int(route_stats.get("stop_count", 0)) <= 3
            and int(route_stats.get("cross_area_count", 0)) <= 1
        )
    elif purpose == "tourism":
        purpose_hit = bool(route_stats.get("sight_count", 0) >= 1)

    _add_check(
        "purpose",
        purpose_applicable,
        purpose_hit,
        "目的约束应与路线结构一致",
    )

    applicable = [item for item in checks.values() if item["applicable"]]
    satisfied = [item for item in applicable if item["satisfied"] is True]
    score = round(len(satisfied) / len(applicable), 4) if applicable else 1.0

    return {
        "checks": checks,
        "applicable_count": len(applicable),
        "satisfied_count": len(satisfied),
        "constraint_satisfaction_rate": score,
    }


def evaluate_explanation_consistency(
    route_stats: Dict[str, Any],
    explanation_basis: List[str] | None,
) -> Dict[str, Any]:
    basis_text = " ".join(str(x) for x in (explanation_basis or []))

    rules: List[Tuple[bool, bool, str]] = []

    weather_basis = any(keyword in basis_text for keyword in ["雨天", "室内", "少步行", "低步行"])
    weather_consistent = (
        int(route_stats.get("cross_area_count", 0)) <= 1
        and int(route_stats.get("stop_count", 0)) <= 3
    )
    rules.append((weather_basis, weather_consistent, "天气/室内/低步行说明一致性"))

    parents_basis = any(keyword in basis_text for keyword in ["陪父母", "低强度", "轻松"])
    parents_consistent = (
        int(route_stats.get("stop_count", 0)) <= 3
        and int(route_stats.get("cross_area_count", 0)) <= 1
    )
    rules.append((parents_basis, parents_consistent, "陪父母/低强度说明一致性"))

    meal_basis = any(keyword in basis_text for keyword in ["吃饭优先", "用餐", "餐饮优先", "保留正餐"])
    meal_consistent = int(route_stats.get("meal_count", 0)) > 0
    rules.append((meal_basis, meal_consistent, "餐饮优先说明一致性"))

    applicable = [rule for rule in rules if rule[0]]
    satisfied = [rule for rule in applicable if rule[1]]

    if not applicable:
        rate = 1.0
        consistent = True
    else:
        rate = round(len(satisfied) / len(applicable), 4)
        consistent = len(satisfied) == len(applicable)

    return {
        "applicable_count": len(applicable),
        "satisfied_count": len(satisfied),
        "explanation_consistency_rate": rate,
        "explanation_consistent": consistent,
        "rules": [
            {
                "name": note,
                "applicable": app,
                "satisfied": sat if app else None,
            }
            for app, sat, note in rules
        ],
    }


def evaluate_case_constraints(case: Dict[str, Any]) -> Dict[str, Any]:
    request_context = resolve_request_context(case)
    route_stats = compute_route_stats(case)
    knowledge_ctx = extract_knowledge_context(case)

    constraint_eval = evaluate_constraint_satisfaction(
        request_context=request_context,
        route_stats=route_stats,
        explanation_basis=knowledge_ctx.get("explanation_basis") or [],
        knowledge_bias=knowledge_ctx.get("knowledge_bias") or {},
    )
    explanation_eval = evaluate_explanation_consistency(
        route_stats=route_stats,
        explanation_basis=knowledge_ctx.get("explanation_basis") or [],
    )

    return {
        "request_context": request_context,
        "route_stats": route_stats,
        "knowledge_context": knowledge_ctx,
        "constraint_eval": constraint_eval,
        "explanation_eval": explanation_eval,
    }


def is_task_success(case: Dict[str, Any]) -> bool:
    selected_plan = _as_dict(case.get("selected_plan"))
    route = selected_plan.get("route") if isinstance(selected_plan.get("route"), list) else []
    errors = case.get("errors") or []

    if not selected_plan:
        return False
    if not route:
        return False
    if isinstance(errors, list) and len(errors) > 0:
        return False
    return True
