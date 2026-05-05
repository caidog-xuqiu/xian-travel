from __future__ import annotations

from typing import Any, Dict, List

from app.services import sqlite_store
from app.services.route_scoring import STORE_SCORE_THRESHOLD, should_store_case


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


def _route_items(itinerary: Any) -> List[Dict[str, Any]]:
    payload = _as_dict(itinerary)
    route = payload.get("route")
    if isinstance(route, list):
        return [_as_dict(item) for item in route]
    return []


def _value(payload: Dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if hasattr(value, "value"):
        return value.value
    return value


def _case_summary(case: Dict[str, Any]) -> Dict[str, Any]:
    query = str(case.get("user_query") or "")
    return {
        "case_id": case.get("id"),
        "query": query[:48],
        "score": case.get("total_score"),
        "selected_plan": case.get("selected_plan"),
        "created_at": case.get("created_at"),
    }


def _match_score(request_context: Dict[str, Any], case: Dict[str, Any]) -> float:
    parsed = case.get("parsed_request") or {}
    score = 0.0
    for key in ["purpose", "companion_type", "walking_tolerance", "budget_level", "weather"]:
        if str(_value(request_context, key) or "") == str(_value(parsed, key) or ""):
            score += 1.4
    if bool(_value(request_context, "need_meal")) == bool(_value(parsed, "need_meal")):
        score += 1.2
    try:
        current_hours = float(_value(request_context, "available_hours") or 0)
        case_hours = float(_value(parsed, "available_hours") or 0)
        if current_hours and case_hours and abs(current_hours - case_hours) <= 1.5:
            score += 1.0
    except (TypeError, ValueError):
        pass
    origin = str(_value(request_context, "origin") or "")
    case_origin = str(_value(parsed, "origin") or "")
    if origin and case_origin and (origin in case_origin or case_origin in origin):
        score += 1.0
    score += min(2.0, max(0.0, float(case.get("total_score") or 0.0) - STORE_SCORE_THRESHOLD))
    return score


def retrieve_high_score_cases(
    request_context: Dict[str, Any],
    *,
    top_k: int = 3,
    user_key: str | None = None,
    min_score: float = STORE_SCORE_THRESHOLD,
) -> List[Dict[str, Any]]:
    if top_k <= 0:
        return []
    candidates = sqlite_store.list_recent_high_score_cases(
        user_key=user_key,
        limit=max(20, top_k * 4),
        min_score=min_score,
    )
    if not candidates and user_key:
        candidates = sqlite_store.list_recent_high_score_cases(limit=max(20, top_k * 4), min_score=min_score)
    scored: List[Dict[str, Any]] = []
    context = _as_dict(request_context)
    for case in candidates:
        item = dict(case)
        item["match_score"] = _match_score(context, item)
        if item["match_score"] <= 0:
            continue
        scored.append(item)
    scored.sort(key=lambda item: (float(item.get("match_score") or 0.0), float(item.get("total_score") or 0.0)), reverse=True)
    return scored[:top_k]


def build_case_bias(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    weights = {
        "prefer_single_cluster": 0,
        "prefer_low_walk": 0,
        "prefer_meal_experience": 0,
        "prefer_night_view": 0,
        "avoid_too_many_stops": 0,
        "prefer_lively_places": 0,
    }
    summaries: List[Dict[str, Any]] = []
    case_ids: List[int] = []
    for case in cases or []:
        case_id = case.get("id")
        if case_id is not None:
            case_ids.append(int(case_id))
        summaries.append(_case_summary(case))
        parsed = case.get("parsed_request") or {}
        route = _route_items(case.get("itinerary") or {})
        route_summary = case.get("route_summary") or {}
        stop_count = len(route)
        cross_area = int(route_summary.get("cross_area_count") or 0)
        has_meal = any(str(item.get("type") or "") == "restaurant" for item in route)
        if cross_area <= 1:
            weights["prefer_single_cluster"] += 1
        if str(parsed.get("walking_tolerance") or "") == "low" or stop_count <= 3:
            weights["prefer_low_walk"] += 1
            weights["avoid_too_many_stops"] += 1
        if has_meal or bool(parsed.get("need_meal")):
            weights["prefer_meal_experience"] += 1
        if str(parsed.get("purpose") or "") == "dating" or str(parsed.get("preferred_period") or "") == "evening":
            weights["prefer_night_view"] += 1
        if str(parsed.get("companion_type") or "") == "friends":
            weights["prefer_lively_places"] += 1
    return {
        "case_ids": list(dict.fromkeys(case_ids)),
        "case_summaries": summaries[:5],
        "weights": weights,
        "prefer_single_cluster": weights["prefer_single_cluster"] > 0,
        "prefer_low_walk": weights["prefer_low_walk"] > 0,
        "prefer_meal_experience": weights["prefer_meal_experience"] > 0,
        "prefer_night_view": weights["prefer_night_view"] > 0,
        "avoid_too_many_stops": weights["avoid_too_many_stops"] > 0,
        "prefer_lively_places": weights["prefer_lively_places"] > 0,
    }


def merge_knowledge_and_cases(knowledge_bias: Dict[str, Any], case_bias: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(knowledge_bias or {})
    merged_weights = dict(merged.get("weights") or {})
    for key, value in (case_bias or {}).items():
        if key in {"case_ids", "case_summaries", "weights"}:
            continue
        if isinstance(value, bool):
            merged[key] = bool(merged.get(key) or value)
    for key, value in (case_bias.get("weights") or {}).items():
        merged_weights[key] = int(merged_weights.get(key) or 0) + int(value or 0)
    if merged_weights:
        merged["weights"] = merged_weights
    merged["case_memory_used"] = bool(case_bias.get("case_ids"))
    return merged


def save_high_quality_case(
    *,
    user_key: str | None,
    user_query: str,
    parsed_request: Any,
    selected_plan: str | None,
    itinerary: Any,
    route_summary: Dict[str, Any] | None,
    knowledge_ids: List[str] | None,
    knowledge_bias: Dict[str, Any] | None,
    score_result: Dict[str, Any],
    user_feedback_text: str | None = None,
) -> Dict[str, Any]:
    decision = should_store_case(score_result, itinerary)
    if not decision.get("should_store"):
        return {"stored_to_case_memory": False, "case_memory_id": None, "stored_reason": decision.get("stored_reason")}
    payload = {
        "user_key": user_key,
        "user_query": user_query,
        "parsed_request": _as_dict(parsed_request),
        "selected_plan": selected_plan,
        "itinerary": _as_dict(itinerary),
        "route_summary": route_summary or {},
        "knowledge_ids": knowledge_ids or [],
        "knowledge_bias": knowledge_bias or {},
        "total_score": score_result.get("total_score"),
        "constraint_score": score_result.get("constraint_score"),
        "plan_quality_score": score_result.get("plan_quality_score"),
        "user_feedback_score": score_result.get("user_feedback_score"),
        "user_feedback_text": user_feedback_text,
        "stored_reason": decision.get("stored_reason"),
    }
    case_id = sqlite_store.save_route_case_memory(payload)
    return {"stored_to_case_memory": True, "case_memory_id": case_id, "stored_reason": decision.get("stored_reason")}
