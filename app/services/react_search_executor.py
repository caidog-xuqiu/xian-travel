from __future__ import annotations

import os
from typing import Any, Dict, List

from app.services.constraint_guard import evaluate_constraints
from app.services.llm_search_planner import build_next_action
from app.services.search_observation import build_observation
from app.services.search_tool_registry import execute_search_action

ENABLE_REACT_SEARCH_ENV = "ENABLE_REACT_SEARCH"
DEFAULT_MAX_REACT_ROUNDS = 4


def _enabled() -> bool:
    value = str(os.getenv(ENABLE_REACT_SEARCH_ENV, "0")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _dedupe_by_id(pois: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for poi in pois:
        key = str(poi.get("id") or poi.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(dict(poi))
    return merged


def _summary_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": bool(result.get("success", False)),
        "result_count": int(result.get("result_count") or 0),
        "fallback_reason": result.get("fallback_reason"),
        "clarification_needed": bool(result.get("clarification_needed", False)),
        "route_duration_minutes": result.get("route_duration_minutes"),
        "route_source": result.get("route_source"),
    }


def run_react_search(
    *,
    user_query: str,
    request_context: Any,
    initial_search_plan: Dict[str, Any] | None,
    runtime_context: Dict[str, Any] | None = None,
    max_rounds: int = DEFAULT_MAX_REACT_ROUNDS,
) -> Dict[str, Any]:
    runtime_context = dict(runtime_context or {})
    discovered_pois = _dedupe_by_id(list(runtime_context.get("discovered_pois") or []))
    react_steps: List[Dict[str, Any]] = []
    search_rounds_debug: List[Dict[str, Any]] = []
    final_queries: List[str] = []
    retrieved_cases: List[Dict[str, Any]] = []
    weather_context: Dict[str, Any] | None = None
    no_progress_rounds = 0
    revise_budget = 1
    fallback_reason: str | None = None

    llm_called = False
    llm_success_any = False
    llm_error_type = None
    llm_error_message = None

    if not _enabled():
        return {
            "enabled": False,
            "success": False,
            "react_fallback_reason": "react_disabled",
            "react_steps": [],
            "search_rounds_debug": [],
            "discovered_pois": discovered_pois,
            "final_search_queries": final_queries,
            "llm_search_planner_called": False,
            "llm_search_planner_success": False,
            "llm_search_planner_error_type": None,
            "llm_search_planner_error_message": None,
        }

    plan = dict(initial_search_plan or {})
    max_rounds = max(1, min(int(max_rounds or DEFAULT_MAX_REACT_ROUNDS), 5))
    observation = {
        "search_plan_summary": plan.get("search_plan_summary"),
        "seed_queries": [str(x) for x in plan.get("search_rounds") or []],
        "discovered_count": len(discovered_pois),
    }

    for round_index in range(1, max_rounds + 1):
        action, planner_debug = build_next_action(user_query, request_context, observation, react_steps)
        llm_called = llm_called or bool(planner_debug.get("llm_search_planner_called"))
        llm_success_any = llm_success_any or bool(planner_debug.get("llm_search_planner_success"))
        llm_error_type = planner_debug.get("llm_search_planner_error_type") or llm_error_type
        llm_error_message = planner_debug.get("llm_search_planner_error_message") or llm_error_message

        decision = str(action.get("decision") or "")
        tool_input = dict(action.get("tool_input") or {})
        for key in ["query", "keyword"]:
            value = str(tool_input.get(key) or "").strip()
            if value and value not in final_queries:
                final_queries.append(value)

        if decision == "fallback":
            fallback_reason = str(action.get("reason") or "react_fallback")
            break

        tool_result = execute_search_action(
            action,
            request_context,
            {
                **runtime_context,
                "round_index": round_index,
                "discovered_pois": discovered_pois,
                "anchor_candidates": runtime_context.get("anchor_candidates") or discovered_pois[:3],
            },
        )

        new_pois = [item for item in (tool_result.get("pois") or []) if isinstance(item, dict)]
        before_count = len(discovered_pois)
        discovered_pois = _dedupe_by_id(discovered_pois + new_pois)
        after_count = len(discovered_pois)

        if tool_result.get("cases"):
            retrieved_cases = list(tool_result.get("cases") or [])
        if tool_result.get("weather_context"):
            weather_context = dict(tool_result.get("weather_context") or {})

        react_step = {
            "round_index": round_index,
            "decision": decision,
            "reason": action.get("reason"),
            "tool": action.get("tool"),
            "tool_input": tool_input,
            "tool_result": tool_result,
            "delta_discovered": after_count - before_count,
        }
        react_steps.append(react_step)

        if decision in {"search_poi", "search_nearby"} and after_count == before_count:
            no_progress_rounds += 1
        else:
            no_progress_rounds = 0

        observation = build_observation(
            round_index=round_index,
            action=action,
            tool_result=tool_result,
            discovered_pois=discovered_pois,
            react_steps=react_steps,
        )
        search_rounds_debug.append(observation)

        if tool_result.get("clarification_needed"):
            return {
                "enabled": True,
                "success": False,
                "clarification_needed": True,
                "clarification_question": tool_result.get("clarification_question"),
                "clarification_options": tool_result.get("clarification_options") or [],
                "react_fallback_reason": None,
                "react_steps": react_steps,
                "search_rounds_debug": search_rounds_debug,
                "discovered_pois": discovered_pois,
                "final_search_queries": final_queries,
                "retrieved_cases": retrieved_cases,
                "weather_context": weather_context,
                "llm_search_planner_called": llm_called,
                "llm_search_planner_success": llm_success_any,
                "llm_search_planner_error_type": llm_error_type,
                "llm_search_planner_error_message": llm_error_message,
            }

        if decision == "finish" or tool_result.get("finished"):
            guard = evaluate_constraints(
                request_context,
                discovered_pois,
                constraints_hint=action.get("constraints") or {},
                react_steps=react_steps,
            )
            react_steps[-1]["constraint_guard"] = guard
            if guard.get("status") == "ok":
                return {
                    "enabled": True,
                    "success": True,
                    "clarification_needed": False,
                    "react_fallback_reason": None,
                    "react_steps": react_steps,
                    "search_rounds_debug": search_rounds_debug,
                    "discovered_pois": discovered_pois,
                    "final_search_queries": final_queries,
                    "retrieved_cases": retrieved_cases,
                    "weather_context": weather_context,
                    "llm_search_planner_called": llm_called,
                    "llm_search_planner_success": llm_success_any,
                    "llm_search_planner_error_type": llm_error_type,
                    "llm_search_planner_error_message": llm_error_message,
                }
            if guard.get("status") == "needs_revise" and revise_budget > 0:
                revise_budget -= 1
                observation["constraint_violations"] = guard.get("violations") or []
                continue
            fallback_reason = f"constraint_guard_{guard.get('status', 'failed')}"
            break

        if not tool_result.get("success", False) and decision in {
            "retrieve_cases",
            "search_poi",
            "search_nearby",
            "get_weather",
            "plan_route",
        }:
            if decision in {"search_poi", "search_nearby"}:
                no_progress_rounds += 1
            if no_progress_rounds >= 2:
                fallback_reason = str(tool_result.get("fallback_reason") or "react_no_progress")
                break

    if not fallback_reason:
        fallback_reason = "max_rounds_reached"

    return {
        "enabled": True,
        "success": False,
        "clarification_needed": False,
        "react_fallback_reason": fallback_reason,
        "react_steps": react_steps,
        "search_rounds_debug": search_rounds_debug,
        "discovered_pois": discovered_pois,
        "final_search_queries": final_queries,
        "retrieved_cases": retrieved_cases,
        "weather_context": weather_context,
        "llm_search_planner_called": llm_called,
        "llm_search_planner_success": llm_success_any,
        "llm_search_planner_error_type": llm_error_type,
        "llm_search_planner_error_message": llm_error_message,
    }
