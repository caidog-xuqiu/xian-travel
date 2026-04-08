from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from app.services.agent_state import AgentState
from app.services.skills_registry import get_skill_for_planning_action

ALLOWED_ACTIONS = {"SEARCH", "GENERATE_CANDIDATES", "REVISE", "FINISH"}
ACTION_ALIASES = {
    "GENERATE": "GENERATE_CANDIDATES",
    "GENERATE_CANDIDATE": "GENERATE_CANDIDATES",
    "GENERATE_CANDIDATES": "GENERATE_CANDIDATES",
    "CREATE_CANDIDATES": "GENERATE_CANDIDATES",
    "MAKE_CANDIDATES": "GENERATE_CANDIDATES",
    "REFINE": "REVISE",
    "ADJUST": "REVISE",
    "DONE": "FINISH",
    "END": "FINISH",
    "STOP": "FINISH",
}
ALLOWED_REVISION_BIASES = {
    "fewer_cross_cluster",
    "include_meal_stop",
    "prioritize_indoor",
    "prioritize_relaxed_pacing",
    "prioritize_night_view",
}
ALLOWED_SEARCH_STRATEGIES = {
    "nearby",
    "classic",
    "indoor",
    "food",
    "night",
    "relaxed",
    "landmark",
    "museum",
}
ALLOWED_PERIOD_HINTS = {"morning", "midday", "afternoon", "evening"}

LLM_ENABLED_ENV = "LLM_PARSER_ENABLED"
LLM_PROVIDER_ENV = "LLM_PROVIDER"
LLM_API_KEY_ENV = "LLM_API_KEY"
LLM_BASE_URL_ENV = "LLM_BASE_URL"
LLM_MODEL_ENV = "LLM_MODEL"

_DOTENV_LOADED = False


def _load_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=False)
    _DOTENV_LOADED = True


def _llm_enabled() -> bool:
    _load_dotenv_once()
    return str(os.getenv(LLM_ENABLED_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def is_planning_llm_available() -> bool:
    _load_dotenv_once()
    if not _llm_enabled():
        return False
    provider = str(os.getenv(LLM_PROVIDER_ENV, "")).strip().lower()
    api_key = str(os.getenv(LLM_API_KEY_ENV, "")).strip()
    base_url = str(os.getenv(LLM_BASE_URL_ENV, "")).strip()
    if not provider or not api_key:
        return False
    if provider == "custom" and not base_url:
        return False
    return True


def _build_prompt(state: AgentState) -> str:
    request = state.parsed_request
    if request is None:
        return ""

    return (
        "You are a planning loop controller. "
        "Do NOT generate final itinerary. "
        "Only output one JSON action.\n"
        "Allowed actions: SEARCH, GENERATE_CANDIDATES, REVISE, FINISH.\n"
        "Output format: "
        '{"action":"SEARCH|GENERATE_CANDIDATES|REVISE|FINISH","reason":"short reason","args":{}}'
        "\n"
        "For REVISE, revision_biases can only use: "
        "[fewer_cross_cluster, include_meal_stop, prioritize_indoor, prioritize_relaxed_pacing, prioritize_night_view].\n"
        f"Current request: companion={request.companion_type.value}, purpose={request.purpose.value}, "
        f"period={request.preferred_period}, weather={request.weather.value}, "
        f"walking={request.walking_tolerance.value}, need_meal={request.need_meal}.\n"
        f"Current search_strategy={state.search_strategy}.\n"
        f"Current counters: search_results_count={state.search_results_count}, "
        f"candidate_plans_count={state.candidate_plans_count}, step={state.planning_step_index}.\n"
    )


def _new_step_debug() -> Dict[str, Any]:
    return {
        "llm_called": False,
        "llm_raw_response_exists": False,
        "llm_json_parse_ok": False,
        "llm_schema_ok": False,
        "llm_action_valid": False,
        "fallback_reason": None,
    }


def _extract_json_text(raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None

    # Handle markdown fenced output from some providers.
    if "```" in text:
        text = text.replace("```json", "```").replace("```JSON", "```")
        chunks = [chunk.strip() for chunk in text.split("```") if chunk.strip()]
        for chunk in chunks:
            if chunk.startswith("{") and chunk.endswith("}"):
                return chunk

    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    return None


def _parse_json_payload(raw: str) -> Dict[str, Any] | None:
    json_text = _extract_json_text(raw)
    if not json_text:
        return None
    candidates = [
        json_text,
        json_text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'"),
    ]
    candidates.append(json_text.replace(",}", "}").replace(",]", "]"))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _content_to_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)
    return None


def _normalize_action_name(action: str) -> str | None:
    if not isinstance(action, str):
        return None
    normalized = action.strip().upper().replace("-", "_").replace(" ", "_")
    normalized = ACTION_ALIASES.get(normalized, normalized)
    if normalized in ALLOWED_ACTIONS:
        return normalized
    return None


def _parse_string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        chunks = [part.strip() for part in value.replace("|", ",").split(",")]
        return [chunk for chunk in chunks if chunk]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _sanitize_search_args(args: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    strategies = args.get("strategies")
    if not strategies:
        strategies = args.get("search_strategy")
    if not strategies:
        strategies = args.get("strategy")

    strategy_items = [
        item.lower() for item in _parse_string_list(strategies) if item.lower() in ALLOWED_SEARCH_STRATEGIES
    ]
    if strategy_items:
        sanitized["strategies"] = list(dict.fromkeys(strategy_items))

    cluster_hint = args.get("cluster_hint")
    if isinstance(cluster_hint, str):
        value = cluster_hint.strip()
        if value:
            sanitized["cluster_hint"] = value

    if isinstance(args.get("meal_priority"), bool):
        sanitized["meal_priority"] = args["meal_priority"]

    period_hint = args.get("period_hint")
    if isinstance(period_hint, str):
        period = period_hint.strip().lower()
        if period in ALLOWED_PERIOD_HINTS:
            sanitized["period_hint"] = period

    return sanitized


def _sanitize_revise_args(args: Dict[str, Any]) -> Dict[str, Any]:
    candidates = args.get("revision_biases")
    if candidates is None:
        candidates = args.get("biases")
    bias_items = [
        item
        for item in _parse_string_list(candidates)
        if item in ALLOWED_REVISION_BIASES
    ]
    return {"revision_biases": list(dict.fromkeys(bias_items))} if bias_items else {}


def _sanitize_args_for_action(action: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if action == "SEARCH":
        return _sanitize_search_args(args)
    if action == "REVISE":
        return _sanitize_revise_args(args)
    return {}


def _repair_action_payload(data: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    action = data.get("action") or data.get("next_action")
    reason = data.get("reason")
    args = data.get("args") or data.get("action_args") or {}

    normalized_action = _normalize_action_name(action) if isinstance(action, str) else None
    if not normalized_action:
        # Minimal repair path when action is missing/invalid.
        if isinstance(args, dict):
            if any(key in args for key in ("strategies", "search_strategy", "strategy", "cluster_hint")):
                normalized_action = "SEARCH"
            elif any(key in args for key in ("revision_biases", "biases")):
                normalized_action = "REVISE"
        if not normalized_action and data.get("finish_ready") is True:
            normalized_action = "FINISH"
        if not normalized_action and data.get("candidate_plans_count"):
            normalized_action = "FINISH"

    if not normalized_action:
        return None
    if not isinstance(reason, str) or not reason.strip():
        reason = "auto-repair action payload"
    if not isinstance(args, dict):
        args = {}
    sanitized_args = _sanitize_args_for_action(normalized_action, args)
    return {"action": normalized_action, "reason": reason.strip(), "args": sanitized_args}


def _default_revise_biases(state: AgentState) -> List[str]:
    request = state.parsed_request
    if request is None:
        return ["fewer_cross_cluster"]
    result: List[str] = []
    if request.need_meal:
        result.append("include_meal_stop")
    if request.weather.value in {"hot", "rainy"}:
        result.append("prioritize_indoor")
    if request.walking_tolerance.value == "low" or request.companion_type.value == "parents":
        result.extend(["fewer_cross_cluster", "prioritize_relaxed_pacing"])
    if request.preferred_period == "evening" or request.purpose.value == "dating":
        result.append("prioritize_night_view")
    if not result:
        result.append("fewer_cross_cluster")
    return list(dict.fromkeys(result))


def _normalize_decision_with_schema(
    state: AgentState,
    raw_decision: Dict[str, Any] | None,
) -> tuple[Dict[str, Any] | None, str | None]:
    parsed = _validate_action(raw_decision)
    if parsed is not None:
        return parsed, None
    repaired = _repair_action_payload(raw_decision)
    if repaired is not None:
        return repaired, "auto-repair"
    return None, None


def _call_llm_with_debug(prompt: str) -> tuple[Dict[str, Any] | None, Dict[str, Any]]:
    debug = _new_step_debug()
    _load_dotenv_once()
    provider = str(os.getenv(LLM_PROVIDER_ENV, "")).strip().lower()
    api_key = str(os.getenv(LLM_API_KEY_ENV, "")).strip()
    base_url = str(os.getenv(LLM_BASE_URL_ENV, "")).strip()
    model = str(os.getenv(LLM_MODEL_ENV, "")).strip() or "default"

    debug["llm_called"] = True
    if provider != "custom" or not api_key or not base_url:
        debug["fallback_reason"] = "llm_not_configured"
        return None, debug

    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
    ).encode("utf-8")

    req = Request(
        base_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urlopen(req, timeout=6) as resp:
            raw = resp.read().decode("utf-8")
    except Exception:
        debug["fallback_reason"] = "llm_call_exception"
        return None, debug

    debug["llm_raw_response_exists"] = bool(raw)
    if not raw:
        debug["fallback_reason"] = "llm_empty_response"
        return None, debug

    parsed = _parse_json_payload(raw)
    if parsed is None:
        debug["fallback_reason"] = "llm_json_parse_failed"
        return None, debug
    debug["llm_json_parse_ok"] = True

    if isinstance(parsed, dict) and isinstance(parsed.get("choices"), list):
        choice = parsed.get("choices", [None])[0]
        if isinstance(choice, dict):
            content = _content_to_text((choice.get("message") or {}).get("content"))
            if content:
                inner = _parse_json_payload(content)
                if inner is None:
                    debug["fallback_reason"] = "llm_choice_json_parse_failed"
                    return None, debug
                debug["llm_json_parse_ok"] = True
                return inner, debug

    if isinstance(parsed, dict):
        return parsed, debug
    debug["fallback_reason"] = "llm_payload_not_dict"
    return None, debug


def _call_llm(prompt: str) -> Dict[str, Any] | None:
    payload, _ = _call_llm_with_debug(prompt)
    return payload


def _validate_action(data: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    action = data.get("action") or data.get("next_action")
    reason = data.get("reason")
    args = data.get("args") or data.get("action_args") or {}

    normalized_action = _normalize_action_name(action) if isinstance(action, str) else None

    if not normalized_action:
        return None
    if not isinstance(reason, str) or not reason.strip():
        reason = "LLM did not provide reason."
    if not isinstance(args, dict):
        args = {}
    args = _sanitize_args_for_action(normalized_action, args)

    return {"action": normalized_action, "reason": reason.strip(), "args": args}


def _default_action(state: AgentState, reason: str) -> Dict[str, Any]:
    if state.search_results_count <= 0:
        return {"action": "SEARCH", "reason": reason, "args": {}}
    if state.candidate_plans_count <= 0:
        return {"action": "GENERATE_CANDIDATES", "reason": reason, "args": {}}
    return {"action": "FINISH", "reason": reason, "args": {}}


def _coerce_action_order(
    state: AgentState,
    parsed: Dict[str, Any],
    prev_action: str | None,
) -> tuple[Dict[str, Any], str | None]:
    """动作时序约束（轻量版）。

    目标：减少无效循环，让执行顺序尽量收敛为 SEARCH -> GENERATE -> REVISE/FINISH。
    """
    action = parsed.get("action")
    reason = parsed.get("reason", "")
    args = parsed.get("args", {})

    if not isinstance(action, str):
        return parsed, None

    # 没有搜索结果且没有候选时，不允许直接生成/修正/结束。
    if state.search_results_count <= 0 and state.candidate_plans_count <= 0 and action in {
        "GENERATE_CANDIDATES",
        "REVISE",
        "FINISH",
    }:
        return (
            {"action": "SEARCH", "reason": f"{reason} [auto-coerce: require SEARCH first]", "args": args},
            f"{action} -> SEARCH (missing search_results)",
        )

    # 没有候选时，REVISE / FINISH 先转为 GENERATE。
    if state.candidate_plans_count <= 0 and action in {"REVISE", "FINISH"}:
        return (
            {
                "action": "GENERATE_CANDIDATES",
                "reason": f"{reason} [auto-coerce: require CANDIDATES first]",
                "args": args,
            },
            f"{action} -> GENERATE_CANDIDATES (missing candidates)",
        )

    # 连续 SEARCH（且已有搜索结果）时，推进到生成候选。
    if action == "SEARCH" and prev_action == "SEARCH" and state.search_results_count > 0:
        return (
            {
                "action": "GENERATE_CANDIDATES",
                "reason": f"{reason} [auto-coerce: duplicate SEARCH]",
                "args": {},
            },
            "SEARCH -> GENERATE_CANDIDATES (duplicate search)",
        )

    # 连续 GENERATE（且已有候选）时，优先收敛到结束，避免空转。
    if action == "GENERATE_CANDIDATES" and prev_action == "GENERATE_CANDIDATES" and state.candidate_plans_count > 0:
        return (
            {
                "action": "FINISH",
                "reason": f"{reason} [auto-coerce: duplicate GENERATE]",
                "args": {},
            },
            "GENERATE_CANDIDATES -> FINISH (duplicate generate)",
        )

    # 连续 REVISE 且已有候选时，避免无效重复修正，直接收敛。
    if action == "REVISE" and prev_action == "REVISE" and state.candidate_plans_count > 0:
        return (
            {
                "action": "FINISH",
                "reason": f"{reason} [auto-coerce: duplicate REVISE]",
                "args": {},
            },
            "REVISE -> FINISH (duplicate revise)",
        )

    # 已有候选时再 SEARCH 通常收益较低，优先转为生成或结束。
    if action == "SEARCH" and state.candidate_plans_count > 0:
        next_action = "FINISH" if prev_action in {"GENERATE_CANDIDATES", "REVISE"} else "GENERATE_CANDIDATES"
        return (
            {
                "action": next_action,
                "reason": f"{reason} [auto-coerce: candidates already ready]",
                "args": {},
            },
            f"SEARCH -> {next_action} (candidates already available)",
        )

    return parsed, None


def _append_history(state: AgentState, outcome_summary: str, skill_name: str | None = None) -> None:
    state.planning_history.append(
        {
            "step": state.planning_step_index,
            "action": state.planning_action,
            "skill_name": skill_name,
            "reason": state.planning_reason,
            "args": state.planning_args or {},
            "outcome_summary": outcome_summary,
        }
    )


def _merge_search_args(state: AgentState, args: Dict[str, Any]) -> None:
    strategies = args.get("strategies")
    if not strategies:
        strategies = args.get("search_strategy")
    if not strategies:
        strategies = args.get("strategy")

    if isinstance(strategies, str):
        strategies = [strategies]

    if isinstance(strategies, list):
        for strategy in strategies:
            if not isinstance(strategy, str):
                continue
            value = strategy.strip().lower()
            if value and value not in state.search_strategy:
                state.search_strategy.append(value)

    cluster_hint = args.get("cluster_hint")
    if isinstance(cluster_hint, str):
        state.planning_args = dict(state.planning_args or {})
        state.planning_args["cluster_hint"] = cluster_hint

    if args.get("meal_priority") and "food" not in state.search_strategy:
        state.search_strategy.append("food")
    if args.get("period_hint") == "evening" and "night" not in state.search_strategy:
        state.search_strategy.append("night")


def _apply_revise_args(state: AgentState, args: Dict[str, Any]) -> None:
    revision_biases = args.get("revision_biases")
    if not isinstance(revision_biases, list):
        revision_biases = args.get("biases", [])
    if not isinstance(revision_biases, list):
        return

    for bias in revision_biases:
        if not isinstance(bias, str):
            continue
        if bias not in ALLOWED_REVISION_BIASES:
            continue
        if bias not in state.revision_biases:
            state.revision_biases.append(bias)
        if bias not in state.candidate_biases:
            state.candidate_biases.append(bias)


def _emit_log(
    state: AgentState,
    logger: Callable[[AgentState, str, str], None] | None,
    message: str,
    level: str = "info",
) -> None:
    if logger:
        logger(state, message, level)


def _record_planning_skill(
    state: AgentState,
    *,
    skill_name: str | None,
    status: str,
    summary: str,
    logger: Callable[[AgentState, str, str], None] | None = None,
) -> None:
    if not skill_name:
        return
    state.active_skill = skill_name
    state.last_skill_result_summary = summary
    state.skill_trace.append(
        {
            "chain": "planning_loop",
            "node": "planning_loop",
            "skill_name": skill_name,
            "status": status,
            "summary": summary,
        }
    )
    _emit_log(
        state,
        logger,
        f"skill invoked: {skill_name}, status={status}, summary={summary}",
        "warn" if status != "success" else "info",
    )


def run_planning_loop(
    state: AgentState,
    *,
    dynamic_search_fn: Callable[[AgentState], AgentState],
    refine_search_results_fn: Callable[[AgentState], AgentState],
    generate_candidates_fn: Callable[[AgentState], AgentState],
    logger: Callable[[AgentState, str, str], None] | None = None,
    action_decider: Callable[[AgentState], Dict[str, Any] | None] | None = None,
) -> AgentState:
    state.current_step = "planning_loop"
    state.current_node = "planning_loop"
    state.planning_step_index = 0
    state.finish_ready = False
    state.planning_history = []

    if not state.planning_loop_enabled:
        _emit_log(state, logger, "Planning loop disabled; fallback to default chain.")
        return state

    if not is_planning_llm_available() and action_decider is None:
        _emit_log(state, logger, "Planning loop skipped: LLM unavailable.", "warn")
        return state

    state.planning_max_steps = min(max(int(state.planning_max_steps or 3), 1), 3)
    prev_action: str | None = None

    for step in range(1, state.planning_max_steps + 1):
        state.planning_step_index = step

        step_debug = _new_step_debug()
        raw_decision: Dict[str, Any] | None = None
        if action_decider is not None:
            try:
                raw_decision = action_decider(state)
                step_debug["fallback_reason"] = "action_decider"
            except Exception:
                raw_decision = None
                step_debug["fallback_reason"] = "action_decider_exception"
        else:
            raw_decision, llm_debug = _call_llm_with_debug(_build_prompt(state))
            step_debug.update(llm_debug)

        parsed, repair_mode = _normalize_decision_with_schema(state, raw_decision)
        if repair_mode:
            _emit_log(
                state,
                logger,
                f"Planning step={step}: action payload {repair_mode}, raw={raw_decision}",
                "warn",
            )

        if parsed is None:
            step_debug["fallback_reason"] = "planning_action_invalid_after_repair"
            parsed = _default_action(state, "Invalid planning action; fallback.")
            _emit_log(state, logger, f"Planning step={step}: invalid action fallback -> {parsed['action']}", "warn")
        else:
            step_debug["llm_schema_ok"] = True
            step_debug["llm_action_valid"] = True

        parsed, coerce_note = _coerce_action_order(state, parsed, prev_action)
        if coerce_note:
            _emit_log(state, logger, f"Planning step={step}: action coerced {coerce_note}", "warn")

        if parsed.get("action") == "REVISE" and not (parsed.get("args") or {}).get("revision_biases"):
            parsed["args"] = {"revision_biases": _default_revise_biases(state)}
            _emit_log(
                state,
                logger,
                f"Planning step={step}: revise args patched with defaults={parsed['args']['revision_biases']}",
                "warn",
            )

        state.planning_action = parsed["action"]
        state.planning_reason = parsed["reason"]
        state.planning_args = parsed["args"]
        prev_action = state.planning_action
        planning_skill = get_skill_for_planning_action(state.planning_action)

        _emit_log(
            state,
            logger,
            "Planning diagnostics: "
            f"llm_called={step_debug['llm_called']}, "
            f"llm_raw_response_exists={step_debug['llm_raw_response_exists']}, "
            f"llm_json_parse_ok={step_debug['llm_json_parse_ok']}, "
            f"llm_schema_ok={step_debug['llm_schema_ok']}, "
            f"llm_action_valid={step_debug['llm_action_valid']}, "
            f"fallback_reason={step_debug['fallback_reason']}",
            "warn" if step_debug.get("fallback_reason") else "info",
        )
        _emit_log(
            state,
            logger,
            "Planning step="
            f"{step} action={state.planning_action} skill={planning_skill} "
            f"reason={state.planning_reason} args={state.planning_args}",
        )

        outcome = ""

        if state.planning_action == "SEARCH":
            _merge_search_args(state, state.planning_args or {})
            dynamic_search_fn(state)
            refine_search_results_fn(state)
            state.search_results_count = len(state.search_results)

            cluster_hint = (state.planning_args or {}).get("cluster_hint")
            if isinstance(cluster_hint, str) and cluster_hint:
                filtered = [poi for poi in state.search_results if poi.get("district_cluster") == cluster_hint]
                if filtered:
                    state.search_results = filtered + [poi for poi in state.search_results if poi not in filtered]
                    state.search_results_count = len(state.search_results)

            outcome = f"search_results_count={state.search_results_count}"

        elif state.planning_action == "GENERATE_CANDIDATES":
            generate_candidates_fn(state)
            state.candidate_plans_count = len(state.candidate_plans)
            outcome = f"candidate_plans_count={state.candidate_plans_count}"

        elif state.planning_action == "REVISE":
            _apply_revise_args(state, state.planning_args or {})
            generate_candidates_fn(state)
            state.candidate_plans_count = len(state.candidate_plans)
            outcome = (
                f"revision_biases={state.revision_biases}, "
                f"candidate_plans_count={state.candidate_plans_count}"
            )

        elif state.planning_action == "FINISH":
            state.candidate_plans_count = len(state.candidate_plans)
            if state.candidate_plans_count <= 0:
                _emit_log(state, logger, "FINISH rejected: no candidates. fallback generate.", "warn")
                generate_candidates_fn(state)
                state.candidate_plans_count = len(state.candidate_plans)
                outcome = f"finish_rejected,candidate_plans_count={state.candidate_plans_count}"
            else:
                state.finish_ready = True
                outcome = "finish_ready=true"
                skill_status = "fallback" if step_debug.get("fallback_reason") else "success"
                _record_planning_skill(
                    state,
                    skill_name=planning_skill,
                    status=skill_status,
                    summary=outcome,
                    logger=logger,
                )
                _append_history(state, outcome, planning_skill)
                _emit_log(state, logger, f"Planning step={step} finished.")
                break

        skill_status = "fallback" if step_debug.get("fallback_reason") else "success"
        _record_planning_skill(
            state,
            skill_name=planning_skill,
            status=skill_status,
            summary=outcome,
            logger=logger,
        )
        _append_history(state, outcome, planning_skill)
        _emit_log(
            state,
            logger,
            f"Planning step={step} action={state.planning_action} skill={planning_skill} outcome={outcome}",
        )

    if not state.finish_ready and state.planning_step_index >= state.planning_max_steps:
        _emit_log(state, logger, f"Planning loop reached max steps={state.planning_max_steps}.", "warn")

    return state
