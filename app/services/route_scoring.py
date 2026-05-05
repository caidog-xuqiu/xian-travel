from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

STORE_SCORE_THRESHOLD = 8.0


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _as_route(itinerary: Any) -> List[Dict[str, Any]]:
    payload = _as_dict(itinerary)
    route = payload.get("route")
    if isinstance(route, list):
        return [_as_dict(item) for item in route]
    return []


def _field_value(payload: Dict[str, Any], key: str, default: Any = None) -> Any:
    value = payload.get(key, default)
    if hasattr(value, "value"):
        return value.value
    return value


def _slot_minutes(time_slot: str | None) -> int:
    if not time_slot:
        return 0
    match = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", str(time_slot))
    if not match:
        return 0
    start_h, start_m, end_h, end_m = [int(part) for part in match.groups()]
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    if end < start:
        end += 24 * 60
    return max(0, end - start)


def _scheduled_minutes(route: Iterable[Dict[str, Any]]) -> int:
    total = 0
    for item in route:
        minutes = _slot_minutes(item.get("time_slot"))
        if minutes <= 0:
            minutes = int(item.get("estimated_duration_minutes") or 0)
        total += max(0, minutes)
    return total


def _walk_minutes(route: Iterable[Dict[str, Any]]) -> int:
    total = 0
    for item in route:
        transport = str(item.get("transport_from_prev") or "")
        if "步行" in transport or "walk" in transport.lower():
            total += max(0, int(item.get("estimated_duration_minutes") or 0))
    return total


def _clusters(route: Iterable[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for item in route:
        cluster = str(item.get("district_cluster") or "").strip()
        if cluster and cluster not in seen:
            seen.append(cluster)
    return seen


def _has_meal(route: Iterable[Dict[str, Any]]) -> bool:
    return any(str(item.get("type") or "").lower() == "restaurant" for item in route)


def _has_night_signal(route: Iterable[Dict[str, Any]], explanation_basis: Iterable[str]) -> bool:
    text = " ".join(
        [str(item.get("time_slot") or "") + " " + str(item.get("reason") or "") for item in route]
        + [str(item) for item in explanation_basis]
    )
    return any(token in text for token in ["18:", "19:", "20:", "21:", "夜", "晚", "拍照"])


def _fallback_is_serious(amap_fallback_reason: str | None, amap_events: List[Dict[str, Any]]) -> bool:
    reasons = [str(amap_fallback_reason or "")]
    for event in amap_events or []:
        if not isinstance(event, dict):
            continue
        reasons.append(str(event.get("amap_fallback_reason") or ""))
    joined = " ".join(reason for reason in reasons if reason)
    if not joined:
        return False
    minor_tokens = ["route_fallback_local", "nearby_failed", "geocode_failed"]
    if all(token in joined for token in ["route_fallback_local"]) or any(token in joined for token in minor_tokens):
        return False
    return True


def score_route_case(
    *,
    request_context: Any,
    selected_plan: Any,
    selected_plan_area_summary: Any | None = None,
    route_source: str | None = None,
    amap_fallback_reason: str | None = None,
    amap_events: List[Dict[str, Any]] | None = None,
    explanation_basis: List[str] | None = None,
    selected_by: str | None = None,
    user_rating: int | float | None = None,
) -> Dict[str, Any]:
    request = _as_dict(request_context)
    area_summary = _as_dict(selected_plan_area_summary)
    route = _as_route(selected_plan)
    explanation = explanation_basis or []

    stop_count = len(route)
    cross_area_count = int(area_summary.get("cross_area_count") or max(0, len(_clusters(route)) - 1))
    scheduled_minutes = _scheduled_minutes(route)
    walk_minutes = _walk_minutes(route)
    has_meal = _has_meal(route)
    need_meal = bool(_field_value(request, "need_meal", False))
    available_hours = float(_field_value(request, "available_hours", 0) or 0)
    walking_tolerance = str(_field_value(request, "walking_tolerance", "") or "")
    weather = str(_field_value(request, "weather", "") or "")
    companion_type = str(_field_value(request, "companion_type", "") or "")
    purpose = str(_field_value(request, "purpose", "") or "")

    constraint_items: Dict[str, float] = {}
    constraint_items["need_meal"] = 0.8 if (not need_meal or has_meal) else 0.0
    if available_hours > 0:
        constraint_items["available_hours"] = 0.8 if scheduled_minutes <= available_hours * 60 + 20 else 0.0
    else:
        constraint_items["available_hours"] = 0.4
    if walking_tolerance == "low":
        constraint_items["walking_tolerance"] = 0.8 if walk_minutes <= 30 and stop_count <= 3 else 0.2
    else:
        constraint_items["walking_tolerance"] = 0.8 if walk_minutes <= 80 else 0.4
    if weather == "rainy":
        constraint_items["weather"] = 0.8 if walk_minutes <= 35 and cross_area_count <= 1 else 0.2
    else:
        constraint_items["weather"] = 0.8
    if companion_type == "parents":
        companion_ok = stop_count <= 3 and walk_minutes <= 35 and cross_area_count <= 1
    elif purpose == "dating":
        companion_ok = has_meal and _has_night_signal(route, explanation)
    elif companion_type == "friends":
        companion_ok = stop_count >= 2
    else:
        companion_ok = stop_count >= 1
    constraint_items["companion_or_purpose"] = 0.8 if companion_ok else 0.2
    raw_constraint_score = min(4.0, sum(constraint_items.values()))

    quality_items: Dict[str, float] = {}
    quality_items["stop_count"] = 0.75 if 1 <= stop_count <= 5 else 0.25
    quality_items["cross_area"] = 0.75 if cross_area_count <= (1 if walking_tolerance == "low" else 2) else 0.25
    quality_items["tool_result"] = 0.75 if route_source == "amap" else (0.45 if route_source == "fallback_local" else 0.35)
    explanation_text = " ".join([str(item) for item in explanation])
    if need_meal and "饭" in explanation_text and not has_meal:
        consistency = 0.2
    elif walking_tolerance == "low" and ("少步行" in explanation_text or "低强度" in explanation_text) and walk_minutes > 45:
        consistency = 0.2
    else:
        consistency = 0.75
    if selected_by == "fallback_rule":
        consistency = min(consistency, 0.65)
    quality_items["explanation_consistency"] = consistency
    serious_fallback = _fallback_is_serious(amap_fallback_reason, amap_events or [])
    if serious_fallback:
        quality_items["fallback_penalty"] = -0.5
    else:
        quality_items["fallback_penalty"] = 0.0
    raw_plan_quality_score = max(0.0, min(3.0, sum(quality_items.values())))
    non_user_scale = 5.0 / 7.0
    constraint_score = round(raw_constraint_score * non_user_scale, 2)
    plan_quality_score = round(raw_plan_quality_score * non_user_scale, 2)

    if user_rating is None:
        user_feedback_score = 0.0
    else:
        try:
            rating = max(1.0, min(10.0, float(user_rating)))
        except (TypeError, ValueError):
            rating = 0.0
        user_feedback_score = round((rating / 10.0) * 5.0, 2) if rating else 0.0

    total_score = round(min(10.0, constraint_score + plan_quality_score + user_feedback_score), 2)
    constraints_met = raw_constraint_score >= 3.0

    return {
        "total_score": total_score,
        "constraint_score": constraint_score,
        "plan_quality_score": plan_quality_score,
        "user_feedback_score": user_feedback_score,
        "constraints_met": constraints_met,
        "serious_fallback": serious_fallback,
        "route_source": route_source,
        "route_metrics": {
            "stop_count": stop_count,
            "cross_area_count": cross_area_count,
            "scheduled_minutes": scheduled_minutes,
            "walk_minutes": walk_minutes,
            "has_meal": has_meal,
        },
        "score_breakdown": {
            "total_score": total_score,
            "constraint_score": constraint_score,
            "plan_quality_score": plan_quality_score,
            "user_feedback_score": user_feedback_score,
            "constraint_items": constraint_items,
            "plan_quality_items": quality_items,
            "raw_constraint_score": round(raw_constraint_score, 2),
            "raw_plan_quality_score": round(raw_plan_quality_score, 2),
            "non_user_score_max": 5.0,
            "user_feedback_score_max": 5.0,
            "constraints_met": constraints_met,
            "serious_fallback": serious_fallback,
        },
    }


def score_with_user_feedback(system_score_breakdown: Dict[str, Any], user_rating: int | float | None) -> Dict[str, Any]:
    base = dict(system_score_breakdown or {})
    constraint_score = float(base.get("constraint_score") or 0.0)
    plan_quality_score = float(base.get("plan_quality_score") or 0.0)
    try:
        rating = max(1.0, min(10.0, float(user_rating)))
    except (TypeError, ValueError):
        rating = 0.0
    user_feedback_score = round((rating / 10.0) * 5.0, 2) if rating else 0.0
    total_score = round(min(10.0, constraint_score + plan_quality_score + user_feedback_score), 2)
    base.update(
        {
            "total_score": total_score,
            "constraint_score": constraint_score,
            "plan_quality_score": plan_quality_score,
            "user_feedback_score": user_feedback_score,
        }
    )
    return {
        "total_score": total_score,
        "constraint_score": constraint_score,
        "plan_quality_score": plan_quality_score,
        "user_feedback_score": user_feedback_score,
        "score_breakdown": base,
    }


def should_store_case(score_result: Dict[str, Any], itinerary: Any) -> Dict[str, Any]:
    route = _as_route(itinerary)
    total_score = float(score_result.get("total_score") or 0.0)
    constraints_met = bool(score_result.get("constraints_met", total_score >= STORE_SCORE_THRESHOLD))
    serious_fallback = bool(score_result.get("serious_fallback", False))
    if not route:
        return {"should_store": False, "stored_reason": "rejected_due_to_empty_itinerary"}
    if total_score < STORE_SCORE_THRESHOLD:
        return {"should_store": False, "stored_reason": "rejected_due_to_low_score"}
    if serious_fallback:
        return {"should_store": False, "stored_reason": "rejected_due_to_serious_fallback"}
    if not constraints_met:
        return {"should_store": False, "stored_reason": "rejected_due_to_constraints_not_met"}
    route_source = str(score_result.get("route_source") or "")
    if route_source == "fallback_local":
        return {"should_store": True, "stored_reason": "high_score_but_with_minor_fallback"}
    return {"should_store": True, "stored_reason": "high_score_and_constraints_met"}
