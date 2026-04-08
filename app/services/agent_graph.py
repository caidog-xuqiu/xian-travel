from __future__ import annotations

import os
import re
import uuid
from typing import Any, Dict, List
from datetime import datetime

from app.models.schemas import ReadableOutput
from app.services.amap_client import input_tips, load_valid_amap_api_key
from app.services.agent_state import AgentPlanResponse, AgentState, DebugLog
from app.services.area_registry import resolve_area_scope_from_request
from app.services.candidate_discovery import discover_candidates
from app.services.data_quality import govern_candidate_pool
from app.services.data_loader import load_pois
from app.services.discovery_sources import SOURCE_AMAP_WEB
from app.services.itinerary_renderer import render_itinerary_text
from app.services.llm_planner import (
    enrich_selection_reason_with_knowledge,
    get_last_selector_debug,
    infer_reason_tags,
    post_check_selected_plan,
    rank_plans_with_constraints,
    select_plan_with_llm,
)
from app.services.knowledge_layer import bundle_to_notes, bundle_to_tags, retrieve_place_knowledge
from app.services.memory_store import recall_user_memory, save_user_memory
from app.services.plan_selector import generate_candidate_plans
from app.services.request_parser import parse_free_text_to_plan_request, parse_free_text_to_plan_request_with_debug
from app.services.strategy_matrix import resolve_strategy_matrix
from app.services.planning_loop import run_planning_loop
from app.services.thread_store import get_latest_state, save_checkpoint
from app.services.skills_registry import get_active_skills_for_agent, get_skill_for_node
from app.services import sqlite_store
from app.services.weather_service import get_weather_context
from app.services.knowledge_base import retrieve_knowledge
from app.services.knowledge_adapter import build_knowledge_bias

MORNING_KEYWORDS = {"上午", "早上", "一早", "早晨"}
MIDDAY_KEYWORDS = {"中午", "午饭前后", "中午前后", "午间"}
AFTERNOON_KEYWORDS = {"下午", "午后"}
EVENING_KEYWORDS = {"晚上", "夜里", "夜间", "晚饭后", "吃完晚饭"}

ORIGIN_HINT_WORDS = {"附近", "这边", "周边", "出发"}
KNOWN_ORIGINS = {"钟楼", "小寨", "大雁塔", "曲江", "回民街"}

CLUSTER_BY_ORIGIN = {
    "钟楼": "城墙钟鼓楼簇",
    "小寨": "小寨文博簇",
    "大雁塔": "大雁塔簇",
    "曲江": "曲江夜游簇",
    "回民街": "城墙钟鼓楼簇",
}

SEARCH_MIN_RESULTS = 12
SEARCH_MAX_RESULTS = 30
SEARCH_MIN_SIGHTS = 4
SEARCH_MIN_RESTAURANTS = 4
DISCOVERY_MIN_RESULTS = 6
QUALITY_MIN_USABLE_RESULTS = 6
DISCOVERY_ENABLED_ENV = "CANDIDATE_DISCOVERY_ENABLED"
_BASE_PARSE_FN = parse_free_text_to_plan_request


def _is_candidate_discovery_enabled() -> bool:
    value = str(os.getenv(DISCOVERY_ENABLED_ENV, "")).strip().lower()
    if value == "":
        return True
    return value in {"1", "true", "yes", "on"}


def _has_time_signal(text: str) -> bool:
    if re.search(r"\d+(?:\.\d+)?\s*(?:小时|h|hour)", text, flags=re.IGNORECASE):
        return True
    if "半天" in text or "全天" in text or "一天" in text:
        return True
    if _has_period_signal(text):
        return True
    return False


def _has_period_signal(text: str) -> bool:
    return any(key in text for key in (MORNING_KEYWORDS | MIDDAY_KEYWORDS | AFTERNOON_KEYWORDS | EVENING_KEYWORDS))


def _period_conflict(text: str) -> bool:
    has_midday = any(key in text for key in MIDDAY_KEYWORDS)
    has_evening = any(key in text for key in EVENING_KEYWORDS)
    return has_midday and has_evening


def _origin_unclear(text: str) -> bool:
    if not any(word in text for word in ORIGIN_HINT_WORDS):
        return False
    return not any(anchor in text for anchor in KNOWN_ORIGINS)


def _build_origin_tip_keyword(text: str) -> str:
    match = re.search(r"(?:在|从|由)([^，。；,\s]{1,12})(?:附近|这边|周边|出发|开始)?", text)
    if match:
        return str(match.group(1) or "").strip()
    return "西安"


def _suggest_origin_tips(text: str, limit: int = 3) -> tuple[List[str], dict]:
    key, _ = load_valid_amap_api_key("AMAP_API_KEY")
    if not key:
        return [], {
            "amap_attempted": False,
            "amap_tool": "tips",
            "amap_hit": False,
            "amap_infocode": None,
            "amap_fallback_reason": "missing_api_key",
        }
    keyword = _build_origin_tip_keyword(text)
    try:
        debug_payload = input_tips(keyword=keyword, limit=max(1, min(limit, 5)), api_key=key, debug=True)
        tips = debug_payload.get("result") or []
        event = {
            "amap_attempted": True,
            "amap_tool": "tips",
            "amap_hit": bool(debug_payload.get("ok")) and bool(tips),
            "amap_infocode": debug_payload.get("amap_infocode"),
            "amap_fallback_reason": None if debug_payload.get("ok") else "tips_failed",
            "exception_type": debug_payload.get("exception_type"),
            "exception_message": debug_payload.get("exception_message"),
            "request_url": debug_payload.get("request_url"),
            "timeout_seconds": debug_payload.get("timeout_seconds"),
            "proxy_mode": debug_payload.get("proxy_mode"),
            "env_proxy_snapshot": debug_payload.get("env_proxy_snapshot"),
        }
    except Exception:
        return [], {
            "amap_attempted": True,
            "amap_tool": "tips",
            "amap_hit": False,
            "amap_infocode": None,
            "amap_fallback_reason": "tips_exception",
        }
    names: List[str] = []
    for tip in tips:
        name = str(tip.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names[:limit], event


def _origin_cluster_hint(origin_text: str) -> str | None:
    for anchor, cluster in CLUSTER_BY_ORIGIN.items():
        if anchor in origin_text:
            return cluster
    return None


def _dedupe_by_id(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result: List[Dict[str, Any]] = []
    for item in items:
        item_id = item.get("id")
        if item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result


def _update_state_knowledge_from_summaries(state: AgentState) -> None:
    notes: List[str] = []
    hit_count = 0
    for summary in state.alternative_plans_summary:
        if getattr(summary, "knowledge_tags", None):
            hit_count += 1
        if getattr(summary, "knowledge_notes", None):
            notes.extend(list(summary.knowledge_notes))

    def _dedupe(items: List[str]) -> List[str]:
        ordered: List[str] = []
        seen = set()
        for item in items:
            if not item or item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    state.retrieved_knowledge_count = max(state.retrieved_knowledge_count, hit_count)
    source_tags = list(state.knowledge_source_tags)
    if hit_count > 0:
        source_tags.append("summary_knowledge")
    merged_tags = _dedupe(source_tags)
    merged_notes = _dedupe(state.knowledge_usage_notes + notes)
    state.knowledge_source_tags = merged_tags[:8]
    state.knowledge_usage_notes = merged_notes[:8]
    if hit_count > 0 or merged_notes:
        _record_skill_event(
            state,
            node_name="knowledge_enrichment",
            status="success",
            summary=f"summary_hits={hit_count}",
        )
    else:
        _record_skill_event(
            state,
            node_name="knowledge_enrichment",
            status="fallback",
            summary="summary_knowledge_miss",
        )


def _build_knowledge_request_context(state: AgentState) -> Dict[str, Any]:
    request = state.parsed_request
    if request is None:
        return {}
    weather_value = (
        str((state.weather_context or {}).get("weather_condition") or "").strip().lower()
        or request.weather.value
    )
    return {
        "weather": weather_value,
        "companion_type": request.companion_type.value,
        "purpose": request.purpose.value,
        "need_meal": request.need_meal,
        "available_hours": request.available_hours,
        "walking_tolerance": request.walking_tolerance.value,
        "budget_level": request.budget_level.value,
        "preferred_period": request.preferred_period,
        "preferred_trip_style": request.preferred_trip_style.value,
    }


def _knowledge_bias_to_candidate_biases(knowledge_bias: Dict[str, Any]) -> List[str]:
    mapping = [
        ("prefer_indoor", "prioritize_indoor"),
        ("prefer_single_cluster", "fewer_cross_cluster"),
        ("prefer_low_walk", "prioritize_relaxed_pacing"),
        ("prefer_meal_experience", "include_meal_stop"),
        ("prefer_night_view", "prioritize_night_view"),
        ("prefer_budget_friendly", "prefer_budget_friendly"),
        ("avoid_too_many_stops", "avoid_too_many_stops"),
        ("prefer_lively_places", "prefer_lively_places"),
    ]
    resolved: List[str] = []
    for bias_key, strategy_bias in mapping:
        if knowledge_bias.get(bias_key):
            resolved.append(strategy_bias)
    return resolved


def _apply_local_knowledge_enrichment(state: AgentState) -> None:
    context = _build_knowledge_request_context(state)
    snippets = retrieve_knowledge(context, top_k=4)
    knowledge_bias = build_knowledge_bias(snippets)
    knowledge_ids = list(knowledge_bias.get("knowledge_ids") or [])
    explanation_basis = list(knowledge_bias.get("explanation_basis") or [])

    state.knowledge_used_count = len(snippets)
    state.knowledge_ids = knowledge_ids
    state.knowledge_bias = {k: v for k, v in knowledge_bias.items() if k not in {"knowledge_ids", "explanation_basis"}}
    state.explanation_basis = explanation_basis[:4]

    if snippets:
        merged_notes = list(dict.fromkeys(state.knowledge_usage_notes + [s.get("title", "") for s in snippets if s.get("title")]))
        state.knowledge_usage_notes = merged_notes[:10]
        state.knowledge_source_tags = list(dict.fromkeys(state.knowledge_source_tags + ["local_rag_rules"]))[:10]
        _log(
            state,
            f"local knowledge retrieved count={len(snippets)}, ids={knowledge_ids}",
        )
        _record_skill_event(
            state,
            node_name="knowledge_enrichment",
            status="success",
            summary=f"local_rag_hits={len(snippets)}",
        )
    else:
        _log(state, "local knowledge miss for current request", "warn")
        _record_skill_event(
            state,
            node_name="knowledge_enrichment",
            status="fallback",
            summary="local_rag_miss",
        )

    extra_biases = _knowledge_bias_to_candidate_biases(knowledge_bias)
    if extra_biases:
        state.candidate_biases = list(dict.fromkeys(state.candidate_biases + extra_biases))
    if explanation_basis:
        state.strategy_notes = list(dict.fromkeys(state.strategy_notes + explanation_basis[:2]))[:8]


def _retrieve_knowledge_for_selected_plan(state: AgentState, selected_candidate: Dict[str, Any] | None) -> None:
    if not state.parsed_request or not selected_candidate:
        return

    itinerary = selected_candidate.get("itinerary")
    if itinerary is None:
        return
    route_names = [item.name for item in itinerary.route]
    summary = selected_candidate.get("summary")
    clusters = list(getattr(summary, "clusters", [])) if summary is not None else []
    query = " ".join(route_names + clusters + [state.user_input or ""])
    purpose_value = getattr(state.parsed_request.purpose, "value", state.parsed_request.purpose)
    context = {
        "preferred_period": state.parsed_request.preferred_period,
        "purpose": str(purpose_value),
        "cluster": clusters[0] if clusters else "",
        "tags": list(getattr(summary, "bias_tags", [])) if summary is not None else [],
    }
    bundle = retrieve_place_knowledge(query=query, context=context)
    tags = bundle_to_tags(bundle)
    notes = bundle_to_notes(bundle, limit=2)

    if tags or notes:
        state.retrieved_knowledge_count += len(tags) or len(notes)
        state.knowledge_source_tags = list(dict.fromkeys(state.knowledge_source_tags + list(bundle.source_tags) + tags))[:10]
        state.knowledge_usage_notes = list(dict.fromkeys(state.knowledge_usage_notes + notes))[:10]
        _log(
            state,
            f"knowledge_layer hit for selection, tags={tags[:2]}, sources={list(bundle.source_tags)}",
        )
        _record_skill_event(
            state,
            node_name="knowledge_enrichment",
            status="success",
            summary=f"selection_hits={len(tags) or len(notes)}",
        )
    else:
        _log(state, "knowledge_layer miss for selection", "warn")
        _record_skill_event(
            state,
            node_name="knowledge_enrichment",
            status="fallback",
            summary="selection_knowledge_miss",
        )


def _log(state: AgentState, message: str, level: str = "info") -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    entry = DebugLog(ts=ts, node=state.current_node, level=level, message=message)
    state.debug_logs.append(entry)
    if state.thread_id:
        try:
            sqlite_store.save_log(
                thread_id=state.thread_id,
                current_node=state.current_node,
                level=level,
                message=message,
            )
        except Exception:
            pass


def _record_skill_event(
    state: AgentState,
    *,
    node_name: str,
    status: str = "success",
    summary: str = "",
    chain: str = "agent_main",
) -> None:
    skill_name = get_skill_for_node(node_name)
    if not skill_name:
        return
    state.active_skill = skill_name
    state.last_skill_result_summary = summary or None
    state.skill_trace.append(
        {
            "chain": chain,
            "node": node_name,
            "skill_name": skill_name,
            "status": status,
            "summary": summary,
        }
    )
    level = "warn" if status in {"fallback", "error"} else "info"
    _log(state, f"skill invoked: {skill_name}, status={status}, summary={summary}", level)


def _record_amap_event(
    state: AgentState,
    *,
    tool: str,
    attempted: bool,
    hit: bool,
    infocode: str | None = None,
    fallback_reason: str | None = None,
    exception_type: str | None = None,
    exception_message: str | None = None,
    request_url: str | None = None,
    timeout_seconds: List[int] | None = None,
    proxy_mode: str | None = None,
    env_proxy_snapshot: Dict[str, Any] | None = None,
) -> None:
    event = {
        "amap_attempted": bool(attempted),
        "amap_tool": tool,
        "amap_hit": bool(hit),
        "amap_infocode": infocode,
        "amap_fallback_reason": fallback_reason,
        "exception_type": exception_type,
        "exception_message": exception_message,
        "request_url": request_url,
        "timeout_seconds": timeout_seconds,
        "proxy_mode": proxy_mode,
        "env_proxy_snapshot": env_proxy_snapshot,
    }
    if attempted:
        state.amap_called = True
    if hit and tool in {"text_search", "nearby"}:
        if tool not in state.amap_sources_used:
            state.amap_sources_used.append(tool)
    state.amap_events.append(event)
    _log(
        state,
        f"amap_event tool={tool} attempted={attempted} hit={hit} infocode={infocode} "
        f"fallback={fallback_reason}",
        "warn" if attempted and not hit else "info",
    )


def analyze_query(state: AgentState) -> AgentState:
    state.current_step = "analyze_query"
    state.current_node = "analyze_query"
    text = state.user_input or ""
    state.normalized_input = text.strip()
    if not _has_time_signal(text):
        state.clarification_needed = True
        state.clarification_question = "这次大概想安排多久？半天还是一天？"
        _log(state, "缺少时间信息，触发澄清：时长。")
    if _period_conflict(text):
        state.clarification_needed = True
        state.clarification_question = "你更倾向白天还是晚间出行？我会按你选择的时段规划。"
        _log(state, "出现时段冲突，触发澄清：白天/晚间。")
    if _origin_unclear(text):
        state.clarification_needed = True
        suggestions, amap_event = _suggest_origin_tips(text)
        _record_amap_event(
            state,
            tool=str(amap_event.get("amap_tool") or "tips"),
            attempted=bool(amap_event.get("amap_attempted")),
            hit=bool(amap_event.get("amap_hit")),
            infocode=amap_event.get("amap_infocode"),
            fallback_reason=amap_event.get("amap_fallback_reason"),
            exception_type=amap_event.get("exception_type"),
            exception_message=amap_event.get("exception_message"),
            request_url=amap_event.get("request_url"),
            timeout_seconds=amap_event.get("timeout_seconds"),
            proxy_mode=amap_event.get("proxy_mode"),
            env_proxy_snapshot=amap_event.get("env_proxy_snapshot"),
        )
        if suggestions:
            suggestion_text = "、".join(suggestions)
            state.clarification_question = f"你的起点更接近哪里？可参考：{suggestion_text}。"
            _log(state, "amap input tips called for clarification")
        else:
            state.clarification_question = "你的起点更接近哪里？比如钟楼、小寨或大雁塔？"
        _log(state, "起点附近意图强但锚点不明确，触发澄清：起点。")
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def recall_memory(state: AgentState) -> AgentState:
    state.current_step = "recall_memory"
    state.current_node = "recall_memory"
    memory_key = state.user_key or state.thread_id or ""
    state.recalled_memory = recall_user_memory(memory_key)
    if state.recalled_memory:
        _log(state, "已召回历史偏好。")
        _record_skill_event(state, node_name="recall_memory", status="success", summary="memory_hit")
    else:
        _log(state, "未找到历史偏好。")
        _record_skill_event(state, node_name="recall_memory", status="success", summary="memory_miss")
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def parse_request(state: AgentState) -> AgentState:
    state.current_step = "parse_request"
    state.current_node = "parse_request"
    try:
        if parse_free_text_to_plan_request is _BASE_PARSE_FN:
            parsed_request, parse_debug = parse_free_text_to_plan_request_with_debug(state.user_input or "")
        else:
            parsed_request = parse_free_text_to_plan_request(state.user_input or "")
            parse_debug = {
                "llm_called": None,
                "llm_raw_response_exists": None,
                "llm_json_parse_ok": None,
                "llm_schema_ok": None,
                "fallback_reason": "parser_monkeypatched_debug_unavailable",
            }
        state.parsed_request = parsed_request
        state.parsed_by = parsed_request.parsed_by
        state.amap_geo_used = bool(
            getattr(parsed_request, "origin_latitude", None) is not None
            and getattr(parsed_request, "origin_longitude", None) is not None
        )
        amap_geo_debug = parse_debug.get("amap_geo_debug")
        if isinstance(amap_geo_debug, dict):
            _record_amap_event(
                state,
                tool=str(amap_geo_debug.get("amap_tool") or "geocode"),
                attempted=bool(amap_geo_debug.get("amap_attempted")),
                hit=bool(amap_geo_debug.get("amap_hit")),
                infocode=amap_geo_debug.get("amap_infocode"),
                fallback_reason=None if amap_geo_debug.get("amap_hit") else "geocode_failed",
                exception_type=amap_geo_debug.get("exception_type"),
                exception_message=amap_geo_debug.get("exception_message"),
                request_url=amap_geo_debug.get("request_url"),
                timeout_seconds=amap_geo_debug.get("timeout_seconds"),
                proxy_mode=amap_geo_debug.get("proxy_mode"),
                env_proxy_snapshot=amap_geo_debug.get("env_proxy_snapshot"),
            )
        if state.amap_geo_used:
            _log(state, "amap geocode called: origin normalized with coordinates")
            _record_skill_event(
                state,
                node_name="amap_geocode",
                status="success",
                summary="origin_geocode_hit",
            )
        _log(state, f"解析完成，来源={state.parsed_by}。")
        _record_skill_event(
            state,
            node_name="parse_request",
            status="success",
            summary=f"parsed_by={state.parsed_by}",
        )
        _log(
            state,
            "LLM parse diagnostics: "
            f"llm_called={parse_debug.get('llm_called', False)}, "
            f"llm_raw_response_exists={parse_debug.get('llm_raw_response_exists', False)}, "
            f"llm_json_parse_ok={parse_debug.get('llm_json_parse_ok', False)}, "
            f"llm_schema_ok={parse_debug.get('llm_schema_ok', False)}, "
            f"fallback_reason={parse_debug.get('fallback_reason')}",
            "warn" if parse_debug.get("fallback_reason") else "info",
        )
    except Exception as exc:
        state.errors.append(str(exc))
        state.clarification_needed = True
        state.clarification_question = "我还需要更明确的行程信息，方便你补充一下吗？"
        _log(state, "解析失败，触发澄清。")
        _record_skill_event(
            state,
            node_name="parse_request",
            status="fallback",
            summary="parse_exception_to_clarification",
        )
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def clarify_if_needed(state: AgentState) -> AgentState:
    state.current_step = "clarify_if_needed"
    state.current_node = "clarify_if_needed"
    if state.clarification_needed:
        state.final_response = {
            "clarification_needed": True,
            "clarification_question": state.clarification_question,
        }
        _log(state, "进入澄清分支。")
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def gather_context(state: AgentState) -> AgentState:
    state.current_step = "gather_context"
    state.current_node = "gather_context"
    if state.parsed_request:
        state.weather_context = get_weather_context(request=state.parsed_request)
        state.weather_source = str((state.weather_context or {}).get("source") or "fallback_request")
        state.amap_weather_used = state.weather_source == "amap_weather"
        key, _ = load_valid_amap_api_key("AMAP_API_KEY")
        _record_amap_event(
            state,
            tool="weather",
            attempted=bool(key),
            hit=state.amap_weather_used,
            infocode=None,
            fallback_reason=None if state.amap_weather_used else "weather_fallback_request",
        )
        _log(state, f"amap weather called: source={state.weather_source}")
        if not state.amap_weather_used:
            state.amap_fallback_reason = state.amap_fallback_reason or "weather_fallback_request"
            _record_skill_event(
                state,
                node_name="amap_weather",
                status="fallback",
                summary=state.weather_source,
            )
        else:
            _record_skill_event(
                state,
                node_name="amap_weather",
                status="success",
                summary="amap_weather",
            )
        _log(state, f"已获取天气上下文（source={state.weather_source}）。")
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def analyze_search_intent(state: AgentState) -> AgentState:
    state.current_step = "analyze_search_intent"
    state.current_node = "analyze_search_intent"
    request = state.parsed_request
    if not request:
        return state

    intent = {
        "companion_type": request.companion_type.value,
        "purpose": request.purpose.value,
        "preferred_period": request.preferred_period,
        "weather": request.weather.value,
        "walking_tolerance": request.walking_tolerance.value,
        "need_meal": request.need_meal,
        "origin_preference_mode": request.origin_preference_mode,
    }
    matrix = resolve_strategy_matrix(request)
    primary = list(matrix.get("primary_strategies", []))
    secondary = list(matrix.get("secondary_strategies", []))
    strategy = primary + [item for item in secondary if item not in primary]

    state.search_intent = intent
    state.primary_strategies = primary
    state.secondary_strategies = secondary
    state.search_strategy = strategy
    state.candidate_biases = list(matrix.get("candidate_biases", []))
    state.strategy_notes = list(matrix.get("notes", []))
    _apply_local_knowledge_enrichment(state)
    state.search_round = 0
    area_scope_info = resolve_area_scope_from_request(request, state.user_input or "")
    state.area_scope_used = list(area_scope_info.get("areas") or [])
    state.area_priority_order = list(area_scope_info.get("priority_areas") or state.area_scope_used)
    state.discovered_area_counts = {}
    state.area_coverage_summary = {}
    if state.search_intent is None:
        state.search_intent = {}
    state.search_intent["area_scope_info"] = area_scope_info
    state.search_intent["area_scope_used"] = list(state.area_scope_used)
    state.search_intent["area_origin"] = area_scope_info.get("origin_area")
    state.search_intent["knowledge_ids"] = list(state.knowledge_ids)
    state.search_intent["knowledge_bias"] = dict(state.knowledge_bias)

    _log(state, f"策略矩阵 primary={state.primary_strategies} secondary={state.secondary_strategies}。")
    if state.candidate_biases:
        _log(state, f"候选偏好={state.candidate_biases}。")
    if state.strategy_notes:
        _log(state, f"策略说明={' | '.join(state.strategy_notes)}。")
    if state.area_scope_used:
        _log(state, f"area scope resolved={state.area_scope_used}")
    if state.area_priority_order:
        _log(state, f"area priority order (intent)={state.area_priority_order}")
    area_priority_from_scope = area_scope_info.get("priority_areas") or []
    if area_priority_from_scope:
        _log(state, f"area priority seed={area_priority_from_scope}")
    if state.knowledge_used_count > 0:
        _log(state, f"knowledge_bias={state.knowledge_bias}")
        _log(state, f"explanation_basis={state.explanation_basis[:2]}")
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def _discovery_quality_is_weak(state: AgentState) -> bool:
    if state.discovered_pois_count < DISCOVERY_MIN_RESULTS:
        return True

    coverage = state.discovery_coverage_summary or {}
    if coverage.get("coverage_ok") is False:
        return True

    kind_counts = coverage.get("kind_counts") or {}
    sight_count = int(kind_counts.get("sight", 0) or 0)
    restaurant_count = int(kind_counts.get("restaurant", 0) or 0)
    if sight_count < 2 or restaurant_count < 1:
        return True

    return False


def candidate_discovery(state: AgentState) -> AgentState:
    state.current_step = "candidate_discovery"
    state.current_node = "candidate_discovery"

    request = state.parsed_request
    if not request:
        return state

    _log(state, "candidate discovery started")
    _log(state, f"candidate discovery strategies={state.search_strategy}")

    if not _is_candidate_discovery_enabled():
        state.discovered_pois = []
        state.discovered_pois_count = 0
        state.discovery_sources = []
        state.discovery_notes = ["candidate discovery disabled"]
        state.discovery_coverage_summary = {}
        state.area_scope_used = []
        state.area_priority_order = []
        state.discovered_area_counts = {}
        state.area_coverage_summary = {}
        _log(state, "candidate discovery disabled, skip this node", "warn")
        _record_skill_event(
            state,
            node_name="candidate_discovery",
            status="skip",
            summary="discovery_disabled",
        )
        save_checkpoint(state.thread_id or "", state.model_dump())
        return state

    try:
        quality_fallback = bool((state.data_quality_report or {}).get("quality_fallback"))
        if state.governed_pois and not quality_fallback:
            all_pois = list(state.governed_pois)
            _log(state, f"candidate discovery consumes governed pool, count={len(all_pois)}")
        else:
            all_pois = load_pois(request_context=request)
            fallback_reason = (state.data_quality_report or {}).get("fallback_reason") or "governed_pool_unavailable"
            _log(
                state,
                f"candidate discovery quality fallback happened, reason={fallback_reason}, raw_count={len(all_pois)}",
                "warn",
            )

        discovery_result = discover_candidates(
            query=state.user_input or request.origin,
            context={
                "request_context": request,
                "primary_strategies": state.primary_strategies,
                "secondary_strategies": state.secondary_strategies,
                "base_pois": all_pois,
                "area_scope_info": (state.search_intent or {}).get("area_scope_info"),
            },
            limits={"max_candidates": SEARCH_MAX_RESULTS},
            filters={},
        )

        state.discovered_pois = list(discovery_result.discovered_pois)
        state.discovered_pois_count = len(state.discovered_pois)
        state.discovery_sources = list(discovery_result.discovery_sources)
        state.discovered_source_counts = dict(getattr(discovery_result, "discovered_source_counts", {}) or {})
        state.area_scope_used = list(getattr(discovery_result, "area_scope_used", []) or state.area_scope_used)
        state.area_priority_order = list(getattr(discovery_result, "area_priority_order", []) or state.area_priority_order)
        state.discovered_area_counts = dict(getattr(discovery_result, "discovered_area_counts", {}) or {})
        state.area_coverage_summary = dict(getattr(discovery_result, "area_coverage_summary", {}) or {})
        state.discovery_notes = list(discovery_result.discovery_notes)
        state.discovery_coverage_summary = dict(discovery_result.coverage_summary)
        state.amap_called = SOURCE_AMAP_WEB in state.discovered_source_counts
        state.amap_sources_used = [
            source
            for source, count in state.discovered_source_counts.items()
            if "amap" in source and int(count or 0) > 0
        ]
        state.area_priority_order = list(
            state.area_priority_order
            or (state.discovery_coverage_summary or {}).get("area_priority_order")
            or state.area_scope_used
        )

        _log(state, f"candidate discovery discovered_count={state.discovered_pois_count}")
        if state.discovered_source_counts:
            _log(
                state,
                "candidate discovery source counts="
                + ", ".join([f"{k}:{v}" for k, v in state.discovered_source_counts.items()]),
            )
        if state.area_scope_used:
            _log(state, "candidate discovery area scope used=" + ", ".join(state.area_scope_used))
        if state.area_priority_order:
            _log(state, "candidate discovery area priority used=" + ", ".join(state.area_priority_order))
        if state.discovered_area_counts:
            _log(
                state,
                "candidate discovery area counts="
                + ", ".join([f"{k}:{v}" for k, v in state.discovered_area_counts.items()]),
            )
        coverage = state.discovery_coverage_summary or {}
        source_meta = coverage.get("source_meta") or {}
        amap_meta = source_meta.get(SOURCE_AMAP_WEB) if isinstance(source_meta, dict) else None
        if isinstance(amap_meta, dict):
            _log(
                state,
                "amap search called: "
                f"mode={amap_meta.get('search_mode')}, "
                f"queries={amap_meta.get('query_count', 0)}, "
                f"mapped={amap_meta.get('mapped_result_count', 0)}",
            )
            search_mode = str(amap_meta.get("search_mode") or "")
            tool_name = "nearby" if search_mode == "nearby" else "text_search"
            mapped_count = int(amap_meta.get("mapped_result_count", 0) or 0)
            fallback_reason = str(amap_meta.get("fallback_reason") or "").strip()
            attempted = fallback_reason != "missing_api_key"
            _record_amap_event(
                state,
                tool=tool_name,
                attempted=attempted,
                hit=mapped_count > 0,
                infocode=amap_meta.get("amap_infocode"),
                fallback_reason=fallback_reason or ("amap_empty" if mapped_count == 0 else None),
                exception_type=amap_meta.get("exception_type"),
                exception_message=amap_meta.get("exception_message"),
                request_url=amap_meta.get("request_url"),
                timeout_seconds=amap_meta.get("timeout_seconds"),
                proxy_mode=amap_meta.get("proxy_mode"),
                env_proxy_snapshot=amap_meta.get("env_proxy_snapshot"),
            )
            fallback_reason = str(amap_meta.get("fallback_reason") or "").strip()
            if fallback_reason:
                state.amap_fallback_reason = fallback_reason
                _log(state, f"amap search fallback: {fallback_reason}", "warn")
                _record_skill_event(
                    state,
                    node_name="amap_search",
                    status="fallback",
                    summary=fallback_reason,
                )
            elif state.amap_sources_used:
                _log(state, "amap search success")
                _record_skill_event(
                    state,
                    node_name="amap_search",
                    status="success",
                    summary=f"source_hits={state.discovered_source_counts.get(SOURCE_AMAP_WEB, 0)}",
                )
        elif state.amap_called and state.discovered_source_counts.get(SOURCE_AMAP_WEB, 0) == 0:
            state.amap_fallback_reason = state.amap_fallback_reason or "amap_search_empty"
            _log(state, "amap search called but returned empty; fallback to local sources", "warn")
            _record_amap_event(
                state,
                tool="text_search",
                attempted=True,
                hit=False,
                infocode=None,
                fallback_reason="amap_search_empty",
            )
            _record_skill_event(
                state,
                node_name="amap_search",
                status="fallback",
                summary="amap_search_empty",
            )

        total_before_merge = int(coverage.get("total_before_merge", 0) or 0)
        total_after_merge = int(coverage.get("total_after_merge", 0) or 0)
        duplicates_removed = int(coverage.get("duplicates_removed", 0) or 0)
        if total_before_merge or total_after_merge:
            _log(
                state,
                "candidate discovery merge summary: "
                f"before={total_before_merge}, after={total_after_merge}, dedup={duplicates_removed}",
            )
        area_priority_order = coverage.get("area_priority_order") or []
        if area_priority_order:
            _log(state, "candidate discovery area priority order=" + ", ".join(area_priority_order))
        if state.discovery_notes:
            _log(state, "discovery notes: " + " | ".join(state.discovery_notes))

        if _discovery_quality_is_weak(state):
            _log(state, "candidate discovery quality is weak; dynamic_search will fallback", "warn")
        _record_skill_event(
            state,
            node_name="candidate_discovery",
            status="success",
            summary=f"discovered_count={state.discovered_pois_count}",
        )
    except Exception as exc:
        state.discovered_pois = []
        state.discovered_pois_count = 0
        state.discovery_sources = ["discovery_exception"]
        state.amap_called = True
        state.amap_sources_used = []
        state.amap_fallback_reason = f"discovery_exception:{exc.__class__.__name__}"
        state.discovered_source_counts = {}
        state.area_scope_used = []
        state.area_priority_order = []
        state.discovered_area_counts = {}
        state.area_coverage_summary = {}
        state.discovery_notes = [f"candidate discovery exception: {exc}"]
        state.discovery_coverage_summary = {"coverage_ok": False, "error": str(exc)}
        _log(state, f"candidate discovery failed, fallback to old path: {exc}", "warn")
        _record_skill_event(
            state,
            node_name="amap_search",
            status="fallback",
            summary=f"discovery_exception:{exc.__class__.__name__}",
        )
        _record_skill_event(
            state,
            node_name="candidate_discovery",
            status="fallback",
            summary="discovery_exception_fallback",
        )

    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def _apply_strategy_filter(
    pois: List[Dict[str, Any]],
    request: Any,
    strategy: str,
) -> List[Dict[str, Any]]:
    strategy = (strategy or "").strip().lower()
    if strategy == "nearby":
        hint = _origin_cluster_hint(request.origin)
        if not hint:
            return []
        return [poi for poi in pois if poi.get("district_cluster") == hint]
    if strategy == "night":
        return [poi for poi in pois if poi.get("district_cluster") == "曲江夜游簇"]
    if strategy == "classic":
        keywords = {"博物馆", "城墙", "塔", "寺", "钟楼", "鼓楼"}
        result = []
        for poi in pois:
            text = f"{poi.get('name','')}{poi.get('category','')}"
            if any(word in text for word in keywords):
                result.append(poi)
        return result
    if strategy == "museum":
        keywords = {"博物馆", "文博", "展馆", "历史"}
        return [poi for poi in pois if any(word in f"{poi.get('name','')}{poi.get('category','')}" for word in keywords)]
    if strategy == "landmark":
        keywords = {"钟楼", "鼓楼", "城墙", "大雁塔", "大唐不夜城", "芙蓉园"}
        return [poi for poi in pois if any(word in f"{poi.get('name','')}{poi.get('category','')}" for word in keywords)]
    if strategy == "indoor":
        return [poi for poi in pois if poi.get("indoor_or_outdoor") == "indoor"]
    if strategy == "food":
        return [poi for poi in pois if poi.get("kind") == "restaurant"]
    if strategy == "relaxed":
        return [poi for poi in pois if poi.get("walking_level") != "high"]
    return []


def dynamic_search(state: AgentState) -> AgentState:
    state.current_step = "dynamic_search"
    state.current_node = "dynamic_search"
    request = state.parsed_request
    if not request:
        return state

    all_pois = list(state.governed_pois) if state.governed_pois else load_pois(request_context=request)
    strategy = state.search_strategy or []
    aggregated: List[Dict[str, Any]] = []

    state.search_round = 1

    coverage_strategies = set(state.discovery_coverage_summary.get("strategies_applied") or [])
    current_strategies = set(strategy)
    strategy_shifted = bool(coverage_strategies) and coverage_strategies != current_strategies

    if strategy_shifted:
        _log(state, "candidate discovery strategy changed, refresh discovery")
        candidate_discovery(state)
        state.current_step = "dynamic_search"
        state.current_node = "dynamic_search"

    use_discovery = bool(state.discovered_pois) and not _discovery_quality_is_weak(state)
    fallback_used = False

    if use_discovery:
        aggregated.extend(state.discovered_pois)
        _log(state, f"dynamic_search uses discovery result first, count={len(state.discovered_pois)}")
    else:
        fallback_used = True
        fallback_reason = "empty" if not state.discovered_pois else "weak_quality"
        _log(state, f"dynamic_search fallback to old path, reason={fallback_reason}", "warn")

    if len(aggregated) < SEARCH_MIN_RESULTS:
        for strat in strategy:
            aggregated.extend(_apply_strategy_filter(all_pois, request, strat))

    aggregated = _dedupe_by_id(aggregated)
    if len(aggregated) < SEARCH_MIN_RESULTS:
        state.search_round = 2
        aggregated.extend(all_pois)
        aggregated = _dedupe_by_id(aggregated)
        _log(state, "search results still insufficient, append fallback pool")

    state.search_results = aggregated[:SEARCH_MAX_RESULTS]
    state.search_results_count = len(state.search_results)
    _log(state, f"dynamic_search finished, count={state.search_results_count}, round={state.search_round}")
    _record_skill_event(
        state,
        node_name="dynamic_search",
        status="fallback" if fallback_used else "success",
        summary=f"search_results_count={state.search_results_count}",
    )
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def refine_search_results(state: AgentState) -> AgentState:
    state.current_step = "refine_search_results"
    state.current_node = "refine_search_results"
    if not state.search_results:
        return state

    results = list(state.search_results)
    sight_count = sum(1 for poi in results if poi.get("kind") == "sight")
    restaurant_count = sum(1 for poi in results if poi.get("kind") == "restaurant")

    if sight_count < SEARCH_MIN_SIGHTS or restaurant_count < SEARCH_MIN_RESTAURANTS:
        all_pois = list(state.governed_pois) if state.governed_pois else load_pois(request_context=state.parsed_request)
        for poi in all_pois:
            results.append(poi)
        results = _dedupe_by_id(results)
        _log(state, "补足景点/餐饮最小数量。")

    state.search_results = results[:SEARCH_MAX_RESULTS]
    state.search_results_count = len(state.search_results)
    _log(state, f"搜索结果精炼完成，候选数={state.search_results_count}。")
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def _apply_candidate_biases(
    candidates: List[Dict[str, Any]],
    biases: List[str],
    origin_cluster_hint: str | None,
) -> List[Dict[str, Any]]:
    if not candidates or not biases:
        return candidates

    def _score(item: Dict[str, Any]) -> tuple[int, int, int, int]:
        summary = item.get("summary")
        itinerary = item.get("itinerary")
        route = itinerary.route if itinerary else []
        first_cluster = route[0].district_cluster if route else None

        bias_score = 0
        if "fewer_cross_cluster" in biases and summary:
            bias_score -= int(getattr(summary, "cross_cluster_count", 0))
        if "include_meal_stop" in biases and summary and getattr(summary, "has_meal", False):
            bias_score += 2
        if "prioritize_relaxed_pacing" in biases and summary and getattr(summary, "rhythm", "") == "轻松":
            bias_score += 2
        if "prefer_origin_cluster_first" in biases and origin_cluster_hint and first_cluster == origin_cluster_hint:
            bias_score += 2
        if "prioritize_night_view" in biases and summary:
            clusters = list(getattr(summary, "clusters", []))
            if "曲江夜游簇" in clusters:
                bias_score += 2
        if "prioritize_indoor" in biases:
            indoor_stops = 0
            for stop in route:
                reason_text = getattr(stop, "reason", "")
                if "室内" in reason_text:
                    indoor_stops += 1
            bias_score += indoor_stops
        if "prioritize_landmarks" in biases and summary:
            clusters = list(getattr(summary, "clusters", []))
            if clusters:
                bias_score += 1
        if "prefer_budget_friendly" in biases and summary:
            if getattr(summary, "budget_level", "") == "low":
                bias_score += 2
            elif getattr(summary, "budget_level", "") == "medium":
                bias_score += 1
        if "avoid_too_many_stops" in biases and summary:
            bias_score -= max(0, int(getattr(summary, "stop_count", 0)) - 2)
        if "prefer_lively_places" in biases and summary:
            clusters = list(getattr(summary, "clusters", []))
            if any(cluster in {"城墙钟鼓楼簇", "曲江夜游簇"} for cluster in clusters):
                bias_score += 2

        stop_count = int(getattr(summary, "stop_count", 0)) if summary else 0
        distance = int(getattr(summary, "total_distance_meters", 0)) if summary else 0
        return (bias_score, stop_count, -distance, 0)

    return sorted(candidates, key=_score, reverse=True)


def generate_candidates(state: AgentState) -> AgentState:
    state.current_step = "generate_candidates"
    state.current_node = "generate_candidates"
    if state.parsed_request:
        _log(state, "area-aware candidate generation started")
        if state.area_priority_order:
            _log(state, "area priority order used=" + ", ".join(state.area_priority_order))
        candidate_pois = state.search_results if state.search_results else None
        quality_info: Dict[str, Any] = {}
        area_context = {
            "area_scope_used": state.area_scope_used,
            "area_priority_order": state.area_priority_order,
            "discovered_area_counts": state.discovered_area_counts,
            "area_coverage_summary": state.area_coverage_summary,
            "origin_area": (state.search_intent or {}).get("area_origin"),
        }
        try:
            state.candidate_plans = generate_candidate_plans(
                state.parsed_request,
                candidate_pois=candidate_pois,
                area_context=area_context,
                quality_feedback=quality_info,
                knowledge_bias=state.knowledge_bias,
            )
        except TypeError as exc:
            # Backward-compatible fallback for monkeypatched/legacy signatures
            # that do not yet accept area_context / knowledge_bias.
            if "area_context" not in str(exc) and "knowledge_bias" not in str(exc):
                raise
            _log(state, "area_context_not_supported_fallback", "warn")
            try:
                state.candidate_plans = generate_candidate_plans(
                    state.parsed_request,
                    candidate_pois=candidate_pois,
                    area_context=area_context,
                    quality_feedback=quality_info,
                )
            except TypeError:
                state.candidate_plans = generate_candidate_plans(
                    state.parsed_request,
                    candidate_pois=candidate_pois,
                    quality_feedback=quality_info,
                )
        origin_hint = _origin_cluster_hint(state.parsed_request.origin)
        state.candidate_plans = _apply_candidate_biases(
            state.candidate_plans,
            state.candidate_biases,
            origin_hint,
        )
        if state.revision_biases:
            state.candidate_plans = _apply_candidate_biases(
                state.candidate_plans,
                state.revision_biases,
                origin_hint,
            )
        state.alternative_plans_summary = [item["summary"] for item in state.candidate_plans]
        state.candidate_plans_count = len(state.candidate_plans)
        _log(state, f"候选方案生成数={state.candidate_plans_count}。")
        _update_state_knowledge_from_summaries(state)
        if state.retrieved_knowledge_count > 0:
            _log(
                state,
                "knowledge_layer called for summary, "
                f"hits={state.retrieved_knowledge_count}, notes={state.knowledge_usage_notes[:2]}",
            )
        else:
            _log(state, "knowledge_layer miss for summary", "warn")
        if quality_info.get("diversity_retry_count", 0) > 0:
            _log(state, f"候选差异增强重试次数={quality_info.get('diversity_retry_count')}。")
        if quality_info.get("candidate_count", 0) < 2:
            _log(state, "候选质量告警：可选方案数量不足 2。", "warn")
        if quality_info.get("diversity_insufficient"):
            _log(
                state,
                f"候选质量告警：方案差异度不足（pairs={quality_info.get('too_similar_pairs', [])}）。",
                "warn",
            )
        cross_area_stats = [
            f"{item['summary'].plan_id}:cross_area={item['summary'].cross_area_count}"
            for item in state.candidate_plans
        ]
        if cross_area_stats:
            _log(state, "candidate cross-area stats=" + " | ".join(cross_area_stats))
        if state.candidate_biases:
            _log(state, f"已按候选偏好排序={state.candidate_biases}。")
        if state.revision_biases:
            _log(state, f"已应用修正偏置={state.revision_biases}。")
        _record_skill_event(
            state,
            node_name="generate_candidates",
            status="success",
            summary=f"candidate_plans_count={state.candidate_plans_count}",
        )
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def select_plan(state: AgentState) -> AgentState:
    state.current_step = "select_plan"
    state.current_node = "select_plan"
    if not state.candidate_plans:
        _log(state, "无候选方案，跳过选优。")
        _record_skill_event(
            state,
            node_name="select_plan",
            status="skip",
            summary="no_candidate_plans",
        )
        return state

    summaries = state.alternative_plans_summary
    llm_choice = None
    llm_select_debug = {
        "llm_selector_called": False,
        "llm_selector_raw_response_exists": False,
        "llm_selector_json_parse_ok": False,
        "llm_selector_schema_ok": False,
        "llm_selector_retry_count": 0,
        "llm_selected_plan_valid": False,
        "fallback_reason": "selector_not_called",
    }
    if state.parsed_request:
        llm_choice = select_plan_with_llm(
            request=state.parsed_request,
            plan_summaries=summaries,
        )
        llm_select_debug = get_last_selector_debug() or llm_select_debug

    ranked_summaries = rank_plans_with_constraints(state.parsed_request, summaries) if state.parsed_request else summaries
    ranked_top_plan_id = ranked_summaries[0].plan_id if ranked_summaries else state.candidate_plans[0]["plan_id"]

    selected_by = "fallback_rule"
    selected_plan_id = ranked_top_plan_id
    selection_reason = "LLM 选优不可用，已按本地约束排序选择最匹配方案。"
    reason_tags: List[str] = ["本地约束回退"]

    if llm_choice:
        selected_plan_id = llm_choice.get("selected_plan_id", selected_plan_id)
        selection_reason = llm_choice.get("selection_reason") or selection_reason
        reason_tags = llm_choice.get("reason_tags") or reason_tags
        selected_by = "llm"
        _log(state, "LLM 选优成功。")
        if selected_plan_id not in {summary.plan_id for summary in summaries}:
            selected_by = "fallback_rule"
            selected_plan_id = ranked_top_plan_id
            selection_reason = "LLM 选择无效编号，已按本地约束排序回退。"
            reason_tags = ["编号无效", "本地约束回退"]
            _log(state, "selector_local_rank_fallback: invalid_plan_id", "warn")
            _log(state, "LLM 返回无效编号，已提前回退到本地约束最优方案。", "warn")
    else:
        fallback_reason = str(llm_select_debug.get("fallback_reason") or "")
        if fallback_reason in {"network_exception", "llm_selector_call_exception"}:
            _log(state, "llm_selector_call_exception", "warn")
        _log(state, f"selector_local_rank_fallback: {fallback_reason or 'unknown'}", "warn")
        _log(
            state,
            "LLM select diagnostics: "
            f"llm_selector_called={llm_select_debug.get('llm_selector_called', llm_select_debug.get('llm_called', False))}, "
            f"llm_selector_raw_response_exists={llm_select_debug.get('llm_selector_raw_response_exists', llm_select_debug.get('llm_raw_response_exists', False))}, "
            f"llm_selector_json_parse_ok={llm_select_debug.get('llm_selector_json_parse_ok', llm_select_debug.get('llm_json_parse_ok', False))}, "
            f"llm_selector_schema_ok={llm_select_debug.get('llm_selector_schema_ok', llm_select_debug.get('llm_schema_ok', False))}, "
            f"llm_selector_retry_count={llm_select_debug.get('llm_selector_retry_count', 0)}, "
            f"llm_selected_plan_valid={llm_select_debug.get('llm_selected_plan_valid', False)}, "
            f"selector_error_type={llm_select_debug.get('selector_error_type')}, "
            f"fallback_reason={llm_select_debug.get('fallback_reason')}",
            "warn",
        )

    # 选优后复核：保证核心约束（用餐、站点数、轻松少跨簇、夜游氛围）不被明显违背。
    if state.parsed_request and summaries:
        post_check = post_check_selected_plan(
            request=state.parsed_request,
            plan_summaries=summaries,
            proposed_plan_id=selected_plan_id,
        )
        final_plan_id = post_check.get("final_plan_id") or selected_plan_id
        if final_plan_id != selected_plan_id:
            _log(
                state,
                f"选优后复核改选：{selected_plan_id} -> {final_plan_id}（{post_check.get('note', 'post_check')}）。",
                "warn",
            )
            selected_plan_id = final_plan_id
            if selected_by == "llm":
                selection_reason = (
                    f"{selection_reason}；系统复核后改选为 {final_plan_id}，以满足关键约束。"
                )
                reason_tags = ["后置复核改选"] + reason_tags
        if not reason_tags:
            selected_summary = next((s for s in summaries if s.plan_id == selected_plan_id), None)
            if selected_summary:
                reason_tags = infer_reason_tags(state.parsed_request, selected_summary)

    selected_candidate = next((item for item in state.candidate_plans if item["plan_id"] == selected_plan_id), None)
    if selected_candidate is None:
        selected_candidate = next((item for item in state.candidate_plans if item["plan_id"] == ranked_top_plan_id), None)
        if selected_candidate is None:
            selected_candidate = state.candidate_plans[0]
        selected_by = "fallback_rule"
        selection_reason = "LLM 选择无效编号，已按本地约束排序回退。"
        reason_tags = ["编号无效", "本地约束回退"]
        _log(state, "selector_local_rank_fallback: selected_candidate_missing", "warn")
        _log(state, "LLM 返回无效编号，回退到本地约束最优方案。")

    selected_summary = selected_candidate.get("summary")
    if state.parsed_request and not reason_tags and selected_summary is not None:
        reason_tags = infer_reason_tags(state.parsed_request, selected_summary)

    selection_reason = enrich_selection_reason_with_knowledge(
        selection_reason=selection_reason,
        summary=selected_summary,
    )
    if state.explanation_basis:
        selection_reason = f"{selection_reason}；依据：{state.explanation_basis[0]}"
    if selected_summary is not None and getattr(selected_summary, "knowledge_tags", None):
        reason_tags = list(dict.fromkeys(reason_tags + list(selected_summary.knowledge_tags[:2])))
    _retrieve_knowledge_for_selected_plan(state, selected_candidate)

    state.selected_plan = selected_candidate["itinerary"]
    if state.selected_plan and state.selected_plan.route:
        has_estimated_leg = any("估算" in item.transport_from_prev for item in state.selected_plan.route)
        state.route_source = "fallback_local" if has_estimated_leg else "amap"
        state.amap_route_used = state.route_source == "amap"
        key, _ = load_valid_amap_api_key("AMAP_API_KEY")
        _record_amap_event(
            state,
            tool="route",
            attempted=bool(key),
            hit=state.amap_route_used,
            infocode=None,
            fallback_reason=None if state.amap_route_used else "route_fallback_local",
        )
        _log(state, f"amap route called: route_source={state.route_source}")
        if not state.amap_route_used:
            state.amap_fallback_reason = state.amap_fallback_reason or "route_fallback_local"
            _record_skill_event(
                state,
                node_name="amap_route",
                status="fallback",
                summary=state.route_source,
            )
        else:
            _record_skill_event(
                state,
                node_name="amap_route",
                status="success",
                summary=state.route_source,
            )
    if selected_summary is not None:
        state.selected_plan_area_summary = {
            "is_cross_area": bool(getattr(selected_summary, "is_cross_area", False)),
            "cross_area_count": int(getattr(selected_summary, "cross_area_count", 0) or 0),
            "area_transition_summary": str(getattr(selected_summary, "area_transition_summary", "")),
            "area_bias_note": getattr(selected_summary, "area_bias_note", None),
        }
        _log(
            state,
            "selected plan area summary="
            + f"cross_area={state.selected_plan_area_summary.get('cross_area_count', 0)}, "
            + f"transition={state.selected_plan_area_summary.get('area_transition_summary', '')}",
        )
    else:
        state.selected_plan_area_summary = {}
    state.selection_reason = selection_reason
    state.reason_tags = list(dict.fromkeys(reason_tags))
    state.selected_by = selected_by
    _log(state, f"最终方案={selected_candidate['plan_id']}，selected_by={selected_by}。")
    _record_skill_event(
        state,
        node_name="select_plan",
        status="success" if selected_by == "llm" else "fallback",
        summary=f"selected_by={selected_by}",
    )
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def render_output(state: AgentState) -> AgentState:
    state.current_step = "render_output"
    state.current_node = "render_output"
    if state.selected_plan and state.parsed_request:
        readable = render_itinerary_text(
            itinerary=state.selected_plan,
            request=state.parsed_request,
            parsed_request=state.parsed_request,
        )
        state.readable_output = ReadableOutput(**readable)
        state.final_response = {
            "selection_reason": state.selection_reason,
            "selected_by": state.selected_by,
            "readable_output": readable,
            "knowledge_used_count": state.knowledge_used_count,
            "knowledge_ids": state.knowledge_ids,
            "knowledge_bias": state.knowledge_bias,
            "explanation_basis": state.explanation_basis,
        }
        _log(state, "文案渲染完成。")
        if state.knowledge_usage_notes:
            _log(
                state,
                f"knowledge_layer used for readable_output, notes={state.knowledge_usage_notes[:2]}",
            )
            _record_skill_event(
                state,
                node_name="knowledge_enrichment",
                status="success",
                summary="readable_knowledge_hit",
            )
        else:
            _log(state, "knowledge_layer miss for readable_output", "warn")
            _record_skill_event(
                state,
                node_name="knowledge_enrichment",
                status="fallback",
                summary="readable_knowledge_miss",
            )
        _record_skill_event(
            state,
            node_name="render_output",
            status="success",
            summary="readable_output_ready",
        )
    else:
        _record_skill_event(
            state,
            node_name="render_output",
            status="skip",
            summary="selected_plan_missing",
        )
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def data_quality(state: AgentState) -> AgentState:
    state.current_step = "data_quality"
    state.current_node = "data_quality"
    request = state.parsed_request
    if not request:
        return state

    _log(state, "data_quality started")
    raw_pois = load_pois(request_context=request)

    try:
        outcome = govern_candidate_pool(raw_pois)
        report = outcome.report.model_dump() if hasattr(outcome.report, "model_dump") else (
            {} if outcome.report is None else dict(outcome.report.__dict__)
        )
        state.governed_pois = list(outcome.usable_pois)
        state.data_quality_report = report
        state.quarantined_count = int(report.get("quarantined_count", len(outcome.quarantined_pois)))
        state.quality_issue_summary = dict(report.get("issue_counts") or {})

        _log(
            state,
            "data_quality totals: "
            f"input={report.get('total_input', len(raw_pois))}, "
            f"after_dedup={report.get('total_after_dedup', len(raw_pois))}, "
            f"quarantined={state.quarantined_count}",
        )
        if state.quality_issue_summary:
            _log(state, f"data_quality issue summary={state.quality_issue_summary}")

        if len(state.governed_pois) < QUALITY_MIN_USABLE_RESULTS:
            state.data_quality_report["quality_fallback"] = True
            state.data_quality_report["fallback_reason"] = "usable_pool_too_small"
            _log(
                state,
                f"data_quality fallback flagged: usable_count={len(state.governed_pois)}",
                "warn",
            )
        else:
            state.data_quality_report["quality_fallback"] = False
            state.data_quality_report["fallback_reason"] = None
    except Exception as exc:
        state.governed_pois = []
        state.data_quality_report = {
            "total_input": len(raw_pois),
            "total_after_dedup": len(raw_pois),
            "quarantined_count": 0,
            "issue_counts": {},
            "quality_notes": [f"data_quality exception: {exc}"],
            "quality_fallback": True,
            "fallback_reason": "data_quality_exception",
        }
        state.quarantined_count = 0
        state.quality_issue_summary = {}
        _log(state, f"data_quality failed: {exc}", "warn")

    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def finalize_memory(state: AgentState) -> AgentState:
    state.current_step = "finalize_memory"
    state.current_node = "finalize_memory"
    if state.parsed_request:
        payload = {
            "companion_type": state.parsed_request.companion_type.value,
            "origin": state.parsed_request.origin,
            "walking_tolerance": state.parsed_request.walking_tolerance.value,
            "budget_level": state.parsed_request.budget_level.value,
            "purpose": state.parsed_request.purpose.value,
            "preferred_period": state.parsed_request.preferred_period,
            "need_meal": state.parsed_request.need_meal,
        }
        state.memory_write_payload = payload
        memory_key = state.user_key or state.thread_id or ""
        save_user_memory(memory_key, payload)
        _log(state, "已写回偏好记忆。")
    save_checkpoint(state.thread_id or "", state.model_dump())
    return state


def _response_from_state(
    state: AgentState,
    *,
    clarification_needed: bool | None = None,
    selected_by: str | None = None,
) -> AgentPlanResponse:
    return AgentPlanResponse(
        thread_id=state.thread_id or "",
        current_step=state.current_step,
        current_node=state.current_node,
        clarification_needed=state.clarification_needed if clarification_needed is None else clarification_needed,
        clarification_question=state.clarification_question,
        parsed_request=state.parsed_request,
        search_intent=state.search_intent,
        search_strategy=state.search_strategy,
        candidate_biases=state.candidate_biases,
        strategy_notes=state.strategy_notes,
        search_round=state.search_round,
        data_quality_report=state.data_quality_report,
        quarantined_count=state.quarantined_count,
        quality_issue_summary=state.quality_issue_summary,
        discovered_pois_count=state.discovered_pois_count,
        discovery_sources=state.discovery_sources,
        amap_called=state.amap_called,
        amap_sources_used=state.amap_sources_used,
        amap_route_used=state.amap_route_used,
        amap_geo_used=state.amap_geo_used,
        amap_weather_used=state.amap_weather_used,
        amap_fallback_reason=state.amap_fallback_reason,
        amap_events=state.amap_events,
        discovered_source_counts=state.discovered_source_counts,
        area_scope_used=state.area_scope_used,
        area_priority_order=state.area_priority_order,
        discovered_area_counts=state.discovered_area_counts,
        area_coverage_summary=state.area_coverage_summary,
        discovery_notes=state.discovery_notes,
        discovery_coverage_summary=state.discovery_coverage_summary,
        route_source=state.route_source,
        weather_source=state.weather_source,
        planning_history=state.planning_history,
        search_results_count=state.search_results_count,
        candidate_plans_count=state.candidate_plans_count,
        finish_ready=state.finish_ready,
        candidate_plans_summary=state.alternative_plans_summary,
        selected_plan=state.selected_plan,
        selected_plan_area_summary=state.selected_plan_area_summary,
        selection_reason=state.selection_reason,
        reason_tags=state.reason_tags,
        selected_by=state.selected_by if selected_by is None else selected_by,
        knowledge_used_count=state.knowledge_used_count,
        knowledge_ids=state.knowledge_ids,
        knowledge_bias=state.knowledge_bias,
        explanation_basis=state.explanation_basis,
        retrieved_knowledge_count=state.retrieved_knowledge_count,
        knowledge_source_tags=state.knowledge_source_tags,
        knowledge_usage_notes=state.knowledge_usage_notes,
        active_skill=state.active_skill,
        skill_trace=state.skill_trace,
        last_skill_result_summary=state.last_skill_result_summary,
        readable_output=state.readable_output,
        final_response=state.final_response,
        errors=state.errors,
        debug_logs=state.debug_logs,
    )


def run_agent_v2(text: str, thread_id: str | None = None, user_key: str | None = None) -> AgentPlanResponse:
    state = AgentState(user_input=text, thread_id=thread_id or str(uuid.uuid4()), user_key=user_key)
    _log(state, f"active skills={get_active_skills_for_agent()}")

    analyze_query(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    recall_memory(state)
    parse_request(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    clarify_if_needed(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    analyze_search_intent(state)
    data_quality(state)
    candidate_discovery(state)
    dynamic_search(state)
    refine_search_results(state)
    gather_context(state)
    generate_candidates(state)
    select_plan(state)
    render_output(state)
    finalize_memory(state)

    return _response_from_state(state)


def run_agent_v3(text: str, thread_id: str | None = None, user_key: str | None = None) -> AgentPlanResponse:
    state = AgentState(
        user_input=text,
        thread_id=thread_id or str(uuid.uuid4()),
        user_key=user_key,
        planning_loop_enabled=True,
        planning_max_steps=3,
    )
    _log(state, f"active skills={get_active_skills_for_agent()}")

    analyze_query(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    recall_memory(state)
    parse_request(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    clarify_if_needed(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    analyze_search_intent(state)
    data_quality(state)
    candidate_discovery(state)

    run_planning_loop(
        state,
        dynamic_search_fn=dynamic_search,
        refine_search_results_fn=refine_search_results,
        generate_candidates_fn=generate_candidates,
        logger=_log,
    )

    # Safety net: planning loop should never break the downstream chain.
    if not state.search_results:
        data_quality(state)
        candidate_discovery(state)
        dynamic_search(state)
        refine_search_results(state)
    if not state.candidate_plans:
        generate_candidates(state)

    gather_context(state)
    if not state.candidate_plans:
        generate_candidates(state)
    select_plan(state)
    render_output(state)
    finalize_memory(state)

    return _response_from_state(state)


def run_agent_v4_current(text: str, thread_id: str | None = None, user_key: str | None = None) -> AgentPlanResponse:
    """Current v4 execution chain.

    Notes:
    - This chain is intentionally independent from run_agent_v3 for A/B evaluation.
    - It still reuses the same hard-planning substrate and safety fallbacks.
    """
    state = AgentState(
        user_input=text,
        thread_id=thread_id or str(uuid.uuid4()),
        user_key=user_key,
        planning_loop_enabled=True,
        planning_max_steps=3,
    )
    _log(state, f"active skills={get_active_skills_for_agent()}")
    _log(state, "agent_chain=v4_current")

    analyze_query(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    recall_memory(state)
    parse_request(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    clarify_if_needed(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    analyze_search_intent(state)
    data_quality(state)
    candidate_discovery(state)

    run_planning_loop(
        state,
        dynamic_search_fn=dynamic_search,
        refine_search_results_fn=refine_search_results,
        generate_candidates_fn=generate_candidates,
        logger=_log,
    )

    # v4 current stabilization: keep fallback explicit and deterministic.
    if not state.search_results:
        _log(state, "v4_current fallback: refresh search context", "warn")
        data_quality(state)
        candidate_discovery(state)
        dynamic_search(state)
        refine_search_results(state)
    if not state.candidate_plans:
        _log(state, "v4_current fallback: regenerate candidates", "warn")
        generate_candidates(state)

    gather_context(state)
    if not state.candidate_plans:
        generate_candidates(state)
    select_plan(state)
    render_output(state)
    finalize_memory(state)

    return _response_from_state(state)


def run_agent(text: str, thread_id: str | None = None) -> AgentPlanResponse:
    return run_agent_v2(text=text, thread_id=thread_id, user_key=None)


def _merge_clarification_input(original_text: str | None, clarification_answer: str) -> str:
    base = (original_text or "").strip()
    answer = clarification_answer.strip()
    if not base:
        return answer
    return f"{base}\n补充：{answer}"


def continue_agent(thread_id: str, clarification_answer: str) -> AgentPlanResponse:
    snapshot = get_latest_state(thread_id)
    if not snapshot:
        raise ValueError("thread_id 不存在或未找到历史状态。")

    state = AgentState(**snapshot)
    if not state.clarification_needed:
        raise ValueError("该线程当前不处于澄清状态，无法继续。")

    state.user_input = _merge_clarification_input(state.user_input, clarification_answer)
    state.clarification_answer = clarification_answer
    state.clarification_needed = False
    state.clarification_question = None
    state.errors = []
    save_checkpoint(state.thread_id or "", state.model_dump())

    analyze_query(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    parse_request(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    clarify_if_needed(state)
    if state.clarification_needed:
        return _response_from_state(state, clarification_needed=True, selected_by="unknown")

    analyze_search_intent(state)
    data_quality(state)
    candidate_discovery(state)

    if state.planning_loop_enabled:
        run_planning_loop(
            state,
            dynamic_search_fn=dynamic_search,
            refine_search_results_fn=refine_search_results,
            generate_candidates_fn=generate_candidates,
            logger=_log,
        )
    else:
        dynamic_search(state)
        refine_search_results(state)

    if not state.search_results:
        data_quality(state)
        candidate_discovery(state)
        dynamic_search(state)
        refine_search_results(state)

    gather_context(state)
    if not state.candidate_plans:
        generate_candidates(state)
    select_plan(state)
    render_output(state)
    finalize_memory(state)

    return _response_from_state(state)


def continue_agent_v2(thread_id: str, clarification_answer: str) -> AgentPlanResponse:
    return continue_agent(thread_id=thread_id, clarification_answer=clarification_answer)


def get_latest_thread_state(thread_id: str) -> Dict[str, Any] | None:
    return get_latest_state(thread_id)


