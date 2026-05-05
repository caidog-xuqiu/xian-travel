from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from app.services.llm_parser import _call_llm_provider, _load_json_lenient

LLM_REACT_PLANNER_ENABLED_ENV = "LLM_REACT_PLANNER_ENABLED"

ALLOWED_DECISIONS = {
    "retrieve_cases",
    "search_poi",
    "search_nearby",
    "get_weather",
    "plan_route",
    "clarify_user",
    "finish",
    "fallback",
}

ALLOWED_TOOLS = {
    "pinecone_cases",
    "amap_search",
    "amap_nearby",
    "amap_weather",
    "amap_route",
    "none",
}


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


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


def _enabled() -> bool:
    value = str(os.getenv(LLM_REACT_PLANNER_ENABLED_ENV, "1")).strip().lower()
    return value not in {"0", "false", "off", "no"}


def _build_prompt(
    user_query: str,
    request_context: Dict[str, Any],
    observation: Dict[str, Any],
    react_history: List[Dict[str, Any]],
) -> str:
    return (
        "你是 ReAct 搜索规划器。你只输出下一步 action 的 JSON，不输出解释。\n"
        "decision 只能是: retrieve_cases, search_poi, search_nearby, get_weather, plan_route, clarify_user, finish, fallback。\n"
        "tool 只能是: pinecone_cases, amap_search, amap_nearby, amap_weather, amap_route, none。\n"
        "输出字段必须包含: decision, reason, tool, tool_input, constraints。\n"
        "若信息不足请用 clarify_user，并在 tool_input 中返回 clarification_question 和 clarification_options(2-4项)。\n"
        "不要覆盖用户硬约束。不要输出 Markdown。\n"
        f"user_query={user_query}\n"
        f"request_context={request_context}\n"
        f"observation={observation}\n"
        f"react_history={react_history}\n"
    )


def _normalize_constraints(raw: Dict[str, Any], request_context: Dict[str, Any]) -> Dict[str, Any]:
    constraints = dict(raw or {})
    walking_tolerance = str(_value(request_context.get("walking_tolerance")) or "")
    weather = str(_value(request_context.get("weather")) or "")
    need_meal = bool(_value(request_context.get("need_meal")))
    max_hours = _value(request_context.get("available_hours"))
    budget_level = str(_value(request_context.get("budget_level")) or "")
    constraints.setdefault("low_walk", walking_tolerance == "low")
    constraints.setdefault("need_meal", need_meal)
    constraints.setdefault("max_hours", float(max_hours) if max_hours is not None else 4.0)
    constraints.setdefault("rainy", weather == "rainy")
    constraints.setdefault("budget_low", budget_level == "low")
    return constraints


def _validate_action(payload: Dict[str, Any], request_context: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    decision = str(payload.get("decision") or "").strip()
    tool = str(payload.get("tool") or "none").strip()
    if decision not in ALLOWED_DECISIONS:
        return None
    if tool not in ALLOWED_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    reason = str(payload.get("reason") or "").strip() or "react_next_step"
    constraints = _normalize_constraints(payload.get("constraints") or {}, request_context)
    return {
        "decision": decision,
        "reason": reason,
        "tool": tool,
        "tool_input": dict(tool_input),
        "constraints": constraints,
    }


def _fallback_action(request_context: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {
        "decision": "fallback",
        "reason": reason,
        "tool": "none",
        "tool_input": {},
        "constraints": _normalize_constraints({}, request_context),
    }


def _parse_raw_action(raw: str | None) -> Dict[str, Any] | None:
    payload = _load_json_lenient(raw)
    if payload is None:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("choices"), list):
        choice = payload.get("choices", [None])[0]
        if isinstance(choice, dict):
            message = choice.get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return _load_json_lenient(content)
            if isinstance(content, list):
                merged = "\n".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                )
                return _load_json_lenient(merged) if merged else None
        return None
    return payload if isinstance(payload, dict) else None


def build_next_action(
    user_query: str,
    request_context: Dict[str, Any] | Any,
    observation: Dict[str, Any] | None,
    react_history: List[Dict[str, Any]] | None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    context = _as_dict(request_context)
    observation_payload = dict(observation or {})
    history_payload = list(react_history or [])
    debug = {
        "llm_search_planner_called": False,
        "llm_search_planner_success": False,
        "llm_search_planner_error_type": None,
        "llm_search_planner_error_message": None,
    }

    if not _enabled():
        debug["llm_search_planner_error_type"] = "planner_disabled"
        debug["llm_search_planner_error_message"] = "LLM_REACT_PLANNER_ENABLED is disabled"
        return _fallback_action(context, "planner_disabled"), debug

    debug["llm_search_planner_called"] = True
    prompt = _build_prompt(user_query, context, observation_payload, history_payload)
    try:
        raw = _call_llm_provider(prompt)
    except Exception as exc:  # pragma: no cover - provider variability
        debug["llm_search_planner_error_type"] = exc.__class__.__name__
        debug["llm_search_planner_error_message"] = str(exc)
        return _fallback_action(context, "llm_call_exception"), debug

    parsed = _parse_raw_action(raw)
    if parsed is None:
        debug["llm_search_planner_error_type"] = "invalid_json"
        debug["llm_search_planner_error_message"] = "LLM planner output is not valid JSON"
        return _fallback_action(context, "invalid_json"), debug

    action = _validate_action(parsed, context)
    if action is None:
        debug["llm_search_planner_error_type"] = "schema_validation_failed"
        debug["llm_search_planner_error_message"] = "LLM planner output does not match action schema"
        return _fallback_action(context, "schema_validation_failed"), debug

    debug["llm_search_planner_success"] = True
    return action, debug
