from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.services.agent_graph import run_agent_v3, run_agent_v4_current
from app.services.area_registry import map_place_to_area
from app.services.plan_selector import select_best_plan
from app.services.request_parser import parse_free_text_to_plan_request
from app.services.skills_registry import get_skill_for_node

DEFAULT_EVAL_CASES_PATH = Path(__file__).resolve().parents[2] / "data" / "eval_cases.json"
SUPPORTED_ENDPOINTS = {"v2", "v3", "v4_current"}

ORIGIN_CLUSTER_HINTS = {
    "钟楼": "城墙钟鼓楼簇",
    "鼓楼": "城墙钟鼓楼簇",
    "回民街": "城墙钟鼓楼簇",
    "小寨": "小寨文博簇",
    "大雁塔": "大雁塔簇",
    "曲江": "曲江夜游簇",
    "不夜城": "曲江夜游簇",
}


def _to_plain_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if isinstance(value, dict):
        return dict(value)
    return {}


def _to_plain_list(items: Iterable[Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in items:
        if hasattr(item, "model_dump"):
            result.append(dict(item.model_dump()))
        elif isinstance(item, dict):
            result.append(dict(item))
    return result


def _record_evaluation_skill_event(
    skill_trace: List[Dict[str, Any]],
    debug_logs: List[Dict[str, Any]],
    *,
    status: str,
    summary: str,
) -> None:
    skill_name = get_skill_for_node("evaluation") or "evaluation_skill"
    skill_trace.append(
        {
            "chain": "evaluation",
            "node": "evaluation",
            "skill_name": skill_name,
            "status": status,
            "summary": summary,
        }
    )
    debug_logs.append(
        {
            "level": "warn" if status in {"fallback", "error"} else "info",
            "message": f"skill invoked: {skill_name}, status={status}, summary={summary}",
        }
    )


def load_eval_cases(path: str | Path | None = None) -> List[Dict[str, Any]]:
    target = Path(path) if path else DEFAULT_EVAL_CASES_PATH
    if not target.exists():
        raise FileNotFoundError(f"eval cases file not found: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("eval cases file must be a list")

    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id") or f"case_{idx:02d}")
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        expected_focus = item.get("expected_focus") or []
        if isinstance(expected_focus, str):
            expected_focus = [expected_focus]
        expected_focus = [str(f).strip().lower() for f in expected_focus if str(f).strip()]
        normalized.append({"case_id": case_id, "text": text, "expected_focus": expected_focus})
    return normalized


def _count_invalid_action_fallback(debug_logs: List[Dict[str, Any]]) -> int:
    count = 0
    for log in debug_logs:
        message = str(log.get("message", "")).lower()
        if "invalid action fallback" in message:
            count += 1
    return count


def _extract_route_features(selected_plan: Dict[str, Any]) -> Dict[str, Any]:
    route = selected_plan.get("route") or []
    clusters: List[str] = []
    areas: List[str] = []
    names: List[str] = []
    meal_count = 0
    indoor_reason_hits = 0
    estimated_leg_count = 0

    for item in route:
        cluster = str(item.get("district_cluster") or "")
        if cluster and cluster not in clusters:
            clusters.append(cluster)
        name = str(item.get("name") or "")
        if name:
            names.append(name)
        area = map_place_to_area({"name": name, "district_cluster": cluster}) or "unknown"
        if area and area not in areas:
            areas.append(area)
        if str(item.get("type")) == "restaurant":
            meal_count += 1
        reason = str(item.get("reason") or "")
        if "室内" in reason:
            indoor_reason_hits += 1
        if "估算" in str(item.get("transport_from_prev") or ""):
            estimated_leg_count += 1

    joined_names = " ".join(names)
    is_night_route = any("夜游" in cluster for cluster in clusters) or any(
        keyword in joined_names for keyword in ["不夜城", "曲江", "芙蓉园", "夜景"]
    )
    return {
        "stop_count": len(route),
        "clusters": clusters,
        "areas": areas,
        "cross_cluster_count": max(0, len(clusters) - 1),
        "cross_area_count": max(0, len(areas) - 1),
        "area_transition_summary": " -> ".join(areas) if areas else "",
        "has_meal": meal_count > 0,
        "meal_count": meal_count,
        "first_cluster": clusters[0] if clusters else None,
        "first_area": areas[0] if areas else None,
        "is_night_route": is_night_route,
        "indoor_reason_hits": indoor_reason_hits,
        "estimated_leg_count": estimated_leg_count,
        "route_source": "fallback_local" if estimated_leg_count > 0 else ("amap" if route else "unknown"),
    }


def _infer_origin_cluster(origin: str) -> str | None:
    text = str(origin or "")
    for anchor, cluster in ORIGIN_CLUSTER_HINTS.items():
        if anchor in text:
            return cluster
    return None


def _infer_origin_area(origin: str) -> str | None:
    cluster = _infer_origin_cluster(origin)
    area = map_place_to_area({"name": origin, "district_cluster": cluster or ""})
    if isinstance(area, str) and area and area != "unknown":
        return area
    return None


def _intent_hits(parsed_request: Dict[str, Any], route_features: Dict[str, Any]) -> Dict[str, bool]:
    need_meal = bool(parsed_request.get("need_meal", False))
    meal_hit = (not need_meal) or bool(route_features.get("has_meal", False))

    preferred_period = str(parsed_request.get("preferred_period") or "")
    purpose = str(parsed_request.get("purpose") or "")
    night_intent = preferred_period == "evening" or purpose == "dating"
    night_hit = (not night_intent) or bool(route_features.get("is_night_route", False))

    nearby_intent = str(parsed_request.get("origin_preference_mode") or "") == "nearby"
    origin_cluster_hint = _infer_origin_cluster(str(parsed_request.get("origin") or ""))
    origin_area_hint = _infer_origin_area(str(parsed_request.get("origin") or ""))
    nearby_hit = (not nearby_intent) or (
        origin_cluster_hint is not None and route_features.get("first_cluster") == origin_cluster_hint
    )
    if nearby_intent and origin_area_hint is not None:
        nearby_hit = nearby_hit or (route_features.get("first_area") == origin_area_hint)

    companion = str(parsed_request.get("companion_type") or "")
    walking = str(parsed_request.get("walking_tolerance") or "")
    relax_intent = purpose == "relax" or walking == "low" or companion == "parents"
    relax_hit = (not relax_intent) or int(route_features.get("cross_cluster_count", 0)) <= 1
    if relax_intent:
        relax_hit = relax_hit and int(route_features.get("cross_area_count", 0)) <= 1

    weather = str(parsed_request.get("weather") or "")
    indoor_intent = weather in {"rainy", "hot"}
    indoor_hit = (not indoor_intent) or int(route_features.get("indoor_reason_hits", 0)) > 0

    checks: List[bool] = []
    for enabled, hit in [
        (need_meal, meal_hit),
        (night_intent, night_hit),
        (nearby_intent, nearby_hit),
        (relax_intent, relax_hit),
        (indoor_intent, indoor_hit),
    ]:
        if enabled:
            checks.append(hit)

    route_quality_hit = True if not checks else (sum(1 for x in checks if x) / len(checks) >= 0.6)
    available_hours = float(parsed_request.get("available_hours", 0) or 0)
    area_fit_hit = True
    if relax_intent:
        area_fit_hit = int(route_features.get("cross_area_count", 0)) <= 1
    elif purpose == "tourism" and available_hours >= 6:
        area_fit_hit = int(route_features.get("cross_area_count", 0)) >= 1
    elif purpose == "food":
        area_fit_hit = bool(route_features.get("has_meal", False)) and int(route_features.get("cross_area_count", 0)) <= 2
    elif nearby_intent and origin_area_hint:
        area_fit_hit = route_features.get("first_area") == origin_area_hint

    return {
        "meal_hit": meal_hit,
        "night_hit": night_hit,
        "nearby_hit": nearby_hit,
        "relax_hit": relax_hit,
        "indoor_hit": indoor_hit,
        "route_quality_hit": route_quality_hit,
        "area_fit_hit": bool(area_fit_hit),
    }


def _candidate_diversity_score(candidate_summaries: List[Dict[str, Any]]) -> float:
    if not candidate_summaries:
        return 0.0
    signatures = set()
    for item in candidate_summaries:
        signatures.add(
            (
                item.get("variant_label"),
                tuple(item.get("clusters") or []),
                item.get("cross_cluster_count"),
                item.get("has_meal"),
                item.get("rhythm"),
                tuple(item.get("knowledge_tags") or []),
            )
        )
    return round(len(signatures) / max(1, len(candidate_summaries)), 4)


def _candidate_quality_signal(data_quality_report: Dict[str, Any]) -> float:
    if not data_quality_report:
        return 1.0
    total_after_dedup = int(data_quality_report.get("total_after_dedup", 0) or 0)
    quarantined = int(data_quality_report.get("quarantined_count", 0) or 0)
    denominator = max(1, total_after_dedup)
    ratio = min(1.0, max(0.0, quarantined / denominator))
    signal = max(0.0, 1.0 - ratio)
    if ratio >= 0.5:
        signal = min(signal, 0.4)
    return round(signal, 4)


def _extract_network_diagnostics(details: List[Dict[str, Any]]) -> Dict[str, Any]:
    for record in reversed(details):
        events = record.get("amap_events") or []
        if not isinstance(events, list):
            continue
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            if any(event.get(key) for key in ("exception_type", "amap_infocode", "request_url")):
                return {
                    "proxy_mode": event.get("proxy_mode"),
                    "exception_type": event.get("exception_type"),
                    "exception_message": event.get("exception_message"),
                    "request_url": event.get("request_url"),
                    "amap_infocode": event.get("amap_infocode"),
                }
    return {}


def _compute_discovery_source_coverage(
    discovery_sources: List[str] | None,
    discovered_source_counts: Dict[str, Any] | None,
) -> float:
    counts = discovered_source_counts or {}
    positive_sources = [source for source, count in counts.items() if int(count or 0) > 0]
    source_count = len(positive_sources)
    if source_count == 0:
        source_count = len([x for x in (discovery_sources or []) if str(x)])
    # 0 -> none, 0.5 -> single source, 1.0 -> multi-source (>=2)
    return round(min(1.0, source_count / 2.0), 4)


def _compute_area_coverage_signal(
    area_scope_used: List[str] | None,
    discovered_area_counts: Dict[str, Any] | None,
) -> float:
    scope = [str(x) for x in (area_scope_used or []) if str(x)]
    counts = discovered_area_counts or {}
    hit_areas = [area for area, count in counts.items() if str(area) and int(count or 0) > 0 and area != "unknown"]
    if not scope:
        # no configured scope info means weak observability
        return round(min(1.0, len(hit_areas) / 3.0), 4)
    return round(min(1.0, len(hit_areas) / max(1, len(scope))), 4)


def _cross_area_signal(route_features: Dict[str, Any]) -> float:
    cross_area_count = int(route_features.get("cross_area_count", 0) or 0)
    stop_count = int(route_features.get("stop_count", 0) or 0)
    if stop_count <= 1:
        return 0.0
    return round(min(1.0, cross_area_count / max(1, stop_count - 1)), 4)


def _compute_cross_area_signal(cross_area_count: int, stop_count: int) -> float:
    if stop_count <= 1:
        return 0.0
    return round(min(1.0, max(0.0, float(cross_area_count) / float(max(1, stop_count - 1)))), 4)


def _short_result_label(clarification_needed: bool, route_quality_hit: bool, invalid_action_fallback: int) -> str:
    if clarification_needed:
        return "needs_clarification"
    if route_quality_hit and invalid_action_fallback == 0:
        return "better"
    if route_quality_hit:
        return "equal"
    return "weaker"


def _build_record_from_v2(case: Dict[str, Any]) -> Dict[str, Any]:
    request = parse_free_text_to_plan_request(case["text"])
    plan_result = select_best_plan(request)

    parsed_request = _to_plain_dict(request)
    candidate_summaries = _to_plain_list(plan_result.get("alternative_plans_summary") or [])
    selected_plan = _to_plain_dict(plan_result.get("selected_plan"))
    route_features = _extract_route_features(selected_plan)
    intent = _intent_hits(parsed_request, route_features)

    return {
        "case_id": case["case_id"],
        "text": case["text"],
        "expected_focus": case.get("expected_focus", []),
        "endpoint": "v2",
        "parsed_by": parsed_request.get("parsed_by", "rule"),
        "selected_by": str(plan_result.get("selected_by") or "fallback_rule"),
        "clarification_needed": False,
        "invalid_action_fallback": 0,
        "candidate_count": len(candidate_summaries),
        "candidate_diversity_score": _candidate_diversity_score(candidate_summaries),
        "candidate_quality_signal": 1.0,
        "amap_called": False,
        "amap_sources_used": [],
        "amap_search_hit": False,
        "amap_route_hit": route_features.get("route_source") == "amap",
        "amap_weather_hit": False,
        "route_source": str(route_features.get("route_source") or "unknown"),
        "weather_source": "fallback_request",
        "amap_fallback_reason": "v2_no_amap_context",
        "candidate_plans_summary": candidate_summaries,
        "data_quality_report": {},
        "discovery_sources": [],
        "discovered_source_counts": {},
        "discovery_source_coverage": 0.0,
        "area_scope_used": [],
        "discovered_area_counts": {},
        "area_coverage_signal": 0.0,
        "cross_area_signal": _cross_area_signal(route_features),
        "cross_area_count": int(route_features.get("cross_area_count", 0) or 0),
        "area_transition_summary": str(route_features.get("area_transition_summary", "") or ""),
        "area_fit_hit": bool(intent.get("area_fit_hit", False)),
        "parsed_request": parsed_request,
        "selected_plan": selected_plan,
        "route_quality_hit": bool(intent["route_quality_hit"]),
        "meal_intent_hit": bool(intent["meal_hit"]),
        "night_intent_hit": bool(intent["night_hit"]),
        "nearby_intent_hit": bool(intent["nearby_hit"]),
        "relax_intent_hit": bool(intent["relax_hit"]),
        "short_result_label": _short_result_label(False, bool(intent["route_quality_hit"]), 0),
    }


def _run_agent_for_endpoint(endpoint: str, *, text: str, thread_id: str, user_key: str | None) -> Any:
    if endpoint == "v3":
        return run_agent_v3(text=text, thread_id=thread_id, user_key=user_key)
    if endpoint == "v4_current":
        return run_agent_v4_current(text=text, thread_id=thread_id, user_key=user_key)
    raise ValueError(f"unsupported agent endpoint for eval: {endpoint}")


def _build_record_from_v3(case: Dict[str, Any], endpoint: str, user_key: str | None = None) -> Dict[str, Any]:
    thread_id = f"eval-{endpoint}-{uuid.uuid4().hex[:8]}"
    response = _run_agent_for_endpoint(endpoint, text=case["text"], thread_id=thread_id, user_key=user_key)
    payload = _to_plain_dict(response)

    parsed_request = _to_plain_dict(payload.get("parsed_request"))
    candidate_summaries = _to_plain_list(payload.get("candidate_plans_summary") or [])
    selected_plan = _to_plain_dict(payload.get("selected_plan"))
    debug_logs = _to_plain_list(payload.get("debug_logs") or [])
    invalid_action_fallback = _count_invalid_action_fallback(debug_logs)
    data_quality_report = _to_plain_dict(payload.get("data_quality_report"))
    discovery_sources = [str(x) for x in (payload.get("discovery_sources") or []) if str(x)]
    discovered_source_counts = _to_plain_dict(payload.get("discovered_source_counts"))
    area_scope_used = [str(x) for x in (payload.get("area_scope_used") or []) if str(x)]
    discovered_area_counts = _to_plain_dict(payload.get("discovered_area_counts"))

    route_features = _extract_route_features(selected_plan)
    intent = (
        _intent_hits(parsed_request, route_features)
        if parsed_request
        else {
            "route_quality_hit": False,
            "meal_hit": False,
            "night_hit": False,
            "nearby_hit": False,
            "relax_hit": False,
            "area_fit_hit": False,
        }
    )

    clarification_needed = bool(payload.get("clarification_needed", False))
    amap_sources_used = [str(x) for x in (payload.get("amap_sources_used") or []) if str(x)]
    route_source = str(payload.get("route_source") or route_features.get("route_source") or "unknown")
    weather_source = str(payload.get("weather_source") or "fallback_request")
    amap_called = bool(payload.get("amap_called", False) or amap_sources_used)
    amap_events = payload.get("amap_events") or []
    discovery_failed_reason = ""
    whether_network_failed = False
    whether_api_returned_error = False
    if (route_source == "amap" or weather_source == "amap_weather") and not amap_sources_used:
        for event in amap_events:
            if not isinstance(event, dict):
                continue
            tool = str(event.get("amap_tool") or "")
            if tool not in {"text_search", "nearby"}:
                continue
            if event.get("amap_hit"):
                continue
            discovery_failed_reason = str(event.get("amap_fallback_reason") or event.get("exception_type") or "")
            exc_type = str(event.get("exception_type") or "")
            if exc_type in {"ConnectionError", "ProxyError", "ConnectTimeout", "ReadTimeout", "SSLError"}:
                whether_network_failed = True
            amap_infocode = str(event.get("amap_infocode") or "")
            if amap_infocode and amap_infocode != "10000":
                whether_api_returned_error = True
            break
    return {
        "case_id": case["case_id"],
        "text": case["text"],
        "expected_focus": case.get("expected_focus", []),
        "endpoint": endpoint,
        "parsed_by": parsed_request.get("parsed_by", payload.get("parsed_by", "unknown")) if parsed_request else "unknown",
        "selected_by": str(payload.get("selected_by") or "unknown"),
        "clarification_needed": clarification_needed,
        "invalid_action_fallback": invalid_action_fallback,
        "candidate_count": len(candidate_summaries),
        "candidate_diversity_score": _candidate_diversity_score(candidate_summaries),
        "candidate_quality_signal": _candidate_quality_signal(data_quality_report),
        "amap_called": amap_called,
        "amap_sources_used": amap_sources_used,
        "amap_search_hit": bool(amap_sources_used),
        "amap_route_hit": route_source == "amap",
        "amap_weather_hit": weather_source == "amap_weather",
        "route_source": route_source,
        "weather_source": weather_source,
        "amap_fallback_reason": str(payload.get("amap_fallback_reason") or ""),
        "discovery_failed_reason": discovery_failed_reason,
        "whether_network_failed": bool(whether_network_failed),
        "whether_api_returned_error": bool(whether_api_returned_error),
        "amap_events": payload.get("amap_events") or [],
        "candidate_plans_summary": candidate_summaries,
        "data_quality_report": data_quality_report,
        "discovery_sources": discovery_sources,
        "discovered_source_counts": discovered_source_counts,
        "discovery_source_coverage": _compute_discovery_source_coverage(
            discovery_sources=discovery_sources,
            discovered_source_counts=discovered_source_counts,
        ),
        "area_scope_used": area_scope_used,
        "discovered_area_counts": discovered_area_counts,
        "area_coverage_signal": _compute_area_coverage_signal(
            area_scope_used=area_scope_used,
            discovered_area_counts=discovered_area_counts,
        ),
        "cross_area_signal": _cross_area_signal(route_features),
        "cross_area_count": int(route_features.get("cross_area_count", 0) or 0),
        "area_transition_summary": str(route_features.get("area_transition_summary", "") or ""),
        "area_fit_hit": bool(intent.get("area_fit_hit", False)),
        "parsed_request": parsed_request,
        "selected_plan": selected_plan,
        "route_quality_hit": bool(intent["route_quality_hit"]),
        "meal_intent_hit": bool(intent["meal_hit"]),
        "night_intent_hit": bool(intent["night_hit"]),
        "nearby_intent_hit": bool(intent["nearby_hit"]),
        "relax_intent_hit": bool(intent["relax_hit"]),
        "short_result_label": _short_result_label(
            clarification_needed=clarification_needed,
            route_quality_hit=bool(intent["route_quality_hit"]),
            invalid_action_fallback=invalid_action_fallback,
        ),
    }


def run_eval_for_endpoint(
    endpoint_name: str,
    cases: List[Dict[str, Any]],
    options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if endpoint_name not in SUPPORTED_ENDPOINTS:
        raise ValueError(f"unsupported endpoint_name: {endpoint_name}")

    options = options or {}
    user_key = options.get("user_key")
    details: List[Dict[str, Any]] = []
    skill_trace: List[Dict[str, Any]] = []
    debug_logs: List[Dict[str, Any]] = []
    _record_evaluation_skill_event(
        skill_trace,
        debug_logs,
        status="success",
        summary=f"evaluation_started endpoint={endpoint_name} cases={len(cases)}",
    )
    for case in cases:
        if endpoint_name == "v2":
            details.append(_build_record_from_v2(case))
        else:
            details.append(_build_record_from_v3(case, endpoint_name, user_key=user_key))
    _record_evaluation_skill_event(
        skill_trace,
        debug_logs,
        status="success",
        summary=f"summary_generated endpoint={endpoint_name} total_cases={len(details)}",
    )

    return {
        "endpoint": endpoint_name,
        "summary": summarize_eval_results(details),
        "details": details,
        "skill_trace": skill_trace,
        "debug_logs": debug_logs,
    }


def summarize_eval_results(results: Dict[str, Any] | List[Dict[str, Any]]) -> Dict[str, Any]:
    details = results if isinstance(results, list) else list(results.get("details") or [])
    total = len(details)
    if total == 0:
        return {
            "total_cases": 0,
            "parsed_by_llm_rate": 0.0,
            "selected_by_llm_rate": 0.0,
            "clarification_rate": 0.0,
            "invalid_fallback_total": 0,
            "candidate_avg_count": 0.0,
            "candidate_diversity_score": 0.0,
            "candidate_quality_signal": 0.0,
            "amap_usage_rate": 0.0,
            "amap_search_hit_rate": 0.0,
            "amap_route_hit_rate": 0.0,
            "amap_weather_hit_rate": 0.0,
            "discovery_source_coverage": 0.0,
            "area_coverage_signal": 0.0,
            "cross_area_signal": 0.0,
            "route_quality_hit_rate": 0.0,
            "area_fit_hit_rate": 0.0,
            "meal_intent_hit_rate": 0.0,
            "night_intent_hit_rate": 0.0,
            "nearby_intent_hit_rate": 0.0,
            "relax_intent_hit_rate": 0.0,
        }

    parsed_by_llm = sum(1 for d in details if d.get("parsed_by") == "llm")
    selected_by_llm = sum(1 for d in details if d.get("selected_by") == "llm")
    clarifications = sum(1 for d in details if d.get("clarification_needed"))
    invalid_fallback_total = sum(int(d.get("invalid_action_fallback", 0) or 0) for d in details)
    candidate_count_sum = sum(int(d.get("candidate_count", 0) or 0) for d in details)
    diversity_avg = sum(float(d.get("candidate_diversity_score", 0.0) or 0.0) for d in details) / total
    quality_signal_avg = sum(float(d.get("candidate_quality_signal", 0.0) or 0.0) for d in details) / total
    amap_usage_hits = sum(1 for d in details if d.get("amap_called"))
    amap_search_hits = sum(1 for d in details if d.get("amap_search_hit"))
    amap_route_hits = sum(1 for d in details if d.get("amap_route_hit"))
    amap_weather_hits = sum(1 for d in details if d.get("amap_weather_hit"))
    source_coverage_avg = sum(float(d.get("discovery_source_coverage", 0.0) or 0.0) for d in details) / total
    area_coverage_avg = sum(float(d.get("area_coverage_signal", 0.0) or 0.0) for d in details) / total
    cross_area_avg = sum(float(d.get("cross_area_signal", 0.0) or 0.0) for d in details) / total
    route_quality_hits = sum(1 for d in details if d.get("route_quality_hit"))
    area_fit_hits = sum(1 for d in details if d.get("area_fit_hit"))
    meal_hits = sum(1 for d in details if d.get("meal_intent_hit"))
    night_hits = sum(1 for d in details if d.get("night_intent_hit"))
    nearby_hits = sum(1 for d in details if d.get("nearby_intent_hit"))
    relax_hits = sum(1 for d in details if d.get("relax_intent_hit"))

    return {
        "total_cases": total,
        "parsed_by_llm_rate": round(parsed_by_llm / total, 4),
        "selected_by_llm_rate": round(selected_by_llm / total, 4),
        "clarification_rate": round(clarifications / total, 4),
        "invalid_fallback_total": int(invalid_fallback_total),
        "candidate_avg_count": round(candidate_count_sum / total, 2),
        "candidate_diversity_score": round(diversity_avg, 4),
        "candidate_quality_signal": round(quality_signal_avg, 4),
        "amap_usage_rate": round(amap_usage_hits / total, 4),
        "amap_search_hit_rate": round(amap_search_hits / total, 4),
        "amap_route_hit_rate": round(amap_route_hits / total, 4),
        "amap_weather_hit_rate": round(amap_weather_hits / total, 4),
        "discovery_source_coverage": round(source_coverage_avg, 4),
        "area_coverage_signal": round(area_coverage_avg, 4),
        "cross_area_signal": round(cross_area_avg, 4),
        "route_quality_hit_rate": round(route_quality_hits / total, 4),
        "area_fit_hit_rate": round(area_fit_hits / total, 4),
        "meal_intent_hit_rate": round(meal_hits / total, 4),
        "night_intent_hit_rate": round(night_hits / total, 4),
        "nearby_intent_hit_rate": round(nearby_hits / total, 4),
        "relax_intent_hit_rate": round(relax_hits / total, 4),
        "network_diagnostics": _extract_network_diagnostics(details),
    }


def _build_delta(new_summary: Dict[str, Any], base_summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "parsed_by_llm_rate_delta": round(float(new_summary.get("parsed_by_llm_rate", 0.0)) - float(base_summary.get("parsed_by_llm_rate", 0.0)), 4),
        "selected_by_llm_rate_delta": round(float(new_summary.get("selected_by_llm_rate", 0.0)) - float(base_summary.get("selected_by_llm_rate", 0.0)), 4),
        "candidate_diversity_delta": round(float(new_summary.get("candidate_diversity_score", 0.0)) - float(base_summary.get("candidate_diversity_score", 0.0)), 4),
        "candidate_quality_signal_delta": round(float(new_summary.get("candidate_quality_signal", 0.0)) - float(base_summary.get("candidate_quality_signal", 0.0)), 4),
        "amap_usage_rate_delta": round(float(new_summary.get("amap_usage_rate", 0.0)) - float(base_summary.get("amap_usage_rate", 0.0)), 4),
        "amap_search_hit_rate_delta": round(float(new_summary.get("amap_search_hit_rate", 0.0)) - float(base_summary.get("amap_search_hit_rate", 0.0)), 4),
        "amap_route_hit_rate_delta": round(float(new_summary.get("amap_route_hit_rate", 0.0)) - float(base_summary.get("amap_route_hit_rate", 0.0)), 4),
        "amap_weather_hit_rate_delta": round(float(new_summary.get("amap_weather_hit_rate", 0.0)) - float(base_summary.get("amap_weather_hit_rate", 0.0)), 4),
        "discovery_source_coverage_delta": round(float(new_summary.get("discovery_source_coverage", 0.0)) - float(base_summary.get("discovery_source_coverage", 0.0)), 4),
        "area_coverage_signal_delta": round(float(new_summary.get("area_coverage_signal", 0.0)) - float(base_summary.get("area_coverage_signal", 0.0)), 4),
        "cross_area_signal_delta": round(float(new_summary.get("cross_area_signal", 0.0)) - float(base_summary.get("cross_area_signal", 0.0)), 4),
        "route_quality_hit_rate_delta": round(float(new_summary.get("route_quality_hit_rate", 0.0)) - float(base_summary.get("route_quality_hit_rate", 0.0)), 4),
        "area_fit_hit_rate_delta": round(float(new_summary.get("area_fit_hit_rate", 0.0)) - float(base_summary.get("area_fit_hit_rate", 0.0)), 4),
        "invalid_fallback_total_delta": int(new_summary.get("invalid_fallback_total", 0)) - int(base_summary.get("invalid_fallback_total", 0)),
    }


def compare_eval_results(
    v2_results: Dict[str, Any],
    v3_results: Dict[str, Any],
    v4_results: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    v2_summary = summarize_eval_results(v2_results)
    v3_summary = summarize_eval_results(v3_results)
    output: Dict[str, Any] = {
        "v2_summary": v2_summary,
        "v3_summary": v3_summary,
        "v2_vs_v3": _build_delta(v3_summary, v2_summary),
    }
    if v4_results is not None:
        v4_summary = summarize_eval_results(v4_results)
        output["v4_summary"] = v4_summary
        output["v3_vs_v4"] = _build_delta(v4_summary, v3_summary)
    return output


# Backward-compatible wrappers
def evaluate_run_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = summarize_eval_results(records)
    return {"total_cases": summary["total_cases"], "summary": summary, "details": records}


def compare_v3_v2_records(v3_records: List[Dict[str, Any]], v2_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    return compare_eval_results(
        v2_results={"details": v2_records},
        v3_results={"details": v3_records},
        v4_results=None,
    )
