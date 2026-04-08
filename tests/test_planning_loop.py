from __future__ import annotations

import json
from typing import Any, Dict

from app.models.schemas import PlanRequest
from app.services.agent_state import AgentState
from app.services.planning_loop import run_planning_loop


def _sample_request() -> PlanRequest:
    return PlanRequest(
        companion_type="friends",
        available_hours=4,
        budget_level="medium",
        purpose="tourism",
        need_meal=True,
        walking_tolerance="medium",
        weather="sunny",
        origin="钟楼",
    )


def _state() -> AgentState:
    return AgentState(
        thread_id="thread-loop",
        parsed_request=_sample_request(),
        planning_loop_enabled=True,
        planning_max_steps=3,
        search_strategy=["classic"],
    )


def _search_fn(state: AgentState) -> AgentState:
    state.search_results = [
        {"id": "s1", "name": "钟楼", "kind": "sight", "district_cluster": "城墙钟鼓楼簇"},
        {"id": "r1", "name": "回民街", "kind": "restaurant", "district_cluster": "城墙钟鼓楼簇"},
    ]
    state.search_results_count = len(state.search_results)
    return state


def _refine_fn(state: AgentState) -> AgentState:
    state.search_results_count = len(state.search_results)
    return state


def _generate_fn(state: AgentState) -> AgentState:
    state.candidate_plans = [{"plan_id": "classic_first", "summary": object(), "itinerary": object()}]
    state.candidate_plans_count = len(state.candidate_plans)
    return state


def test_loop_max_three_rounds() -> None:
    state = _state()

    def decider(_: AgentState) -> Dict[str, Any]:
        return {"action": "SEARCH", "reason": "keep searching", "args": {}}

    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=decider,
    )
    assert state.planning_step_index <= 3
    assert 1 <= len(state.planning_history) <= 3
    # 收紧动作约束后，重复 SEARCH 可能被自动收敛到 FINISH。
    assert state.finish_ready is True


def test_search_action_updates_search_results_count() -> None:
    state = _state()
    actions = iter(
        [
            {"action": "SEARCH", "reason": "search first", "args": {"strategies": ["night"]}},
            {"action": "FINISH", "reason": "finish", "args": {}},
        ]
    )

    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: next(actions),
    )
    assert state.search_results_count > 0


def test_generate_candidates_action_updates_count() -> None:
    state = _state()
    actions = iter(
        [
            {"action": "GENERATE_CANDIDATES", "reason": "build candidates", "args": {}},
            {"action": "FINISH", "reason": "finish", "args": {}},
        ]
    )
    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: next(actions),
    )
    assert state.candidate_plans_count == 1


def test_revise_action_updates_revision_biases() -> None:
    state = _state()
    state.candidate_plans = [{"plan_id": "classic_first", "summary": object(), "itinerary": object()}]
    state.candidate_plans_count = 1
    actions = iter(
        [
            {
                "action": "REVISE",
                "reason": "reduce cross-cluster",
                "args": {"revision_biases": ["fewer_cross_cluster", "include_meal_stop"]},
            },
            {"action": "FINISH", "reason": "finish", "args": {}},
        ]
    )
    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: next(actions),
    )
    assert "fewer_cross_cluster" in state.revision_biases
    assert state.candidate_plans_count == 1


def test_finish_action_stops_loop() -> None:
    state = _state()
    state.candidate_plans = [{"plan_id": "classic_first"}]
    state.candidate_plans_count = 1
    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: {"action": "FINISH", "reason": "good enough", "args": {}},
    )
    assert state.finish_ready is True
    assert state.planning_step_index == 1


def test_invalid_action_falls_back() -> None:
    state = _state()
    logs: list[str] = []
    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: {"action": "INVALID", "reason": "bad", "args": {}},
        logger=lambda _s, message, level="info": logs.append(message),
    )
    assert state.planning_history
    assert state.planning_history[0]["action"] == "SEARCH"
    assert any("fallback_reason=planning_action_invalid_after_repair" in message for message in logs)


def test_llm_unavailable_skip_loop(monkeypatch) -> None:
    import app.services.planning_loop as planning_loop_module

    state = _state()
    monkeypatch.setattr(planning_loop_module, "is_planning_llm_available", lambda: False)
    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
    )
    assert state.planning_history == []


def test_action_name_normalization_accepts_alias() -> None:
    state = _state()
    state.search_results = [{"id": "s0"}]
    state.search_results_count = 1
    actions = iter(
        [
            {"action": "generate", "reason": "alias action", "args": {}},
            {"action": "finish", "reason": "stop", "args": {}},
        ]
    )
    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: next(actions),
    )
    assert state.planning_history
    assert state.planning_history[0]["action"] == "GENERATE_CANDIDATES"


def test_search_args_accepts_strategy_key() -> None:
    state = _state()
    actions = iter(
        [
            {"action": "SEARCH", "reason": "compat strategy key", "args": {"strategy": "night"}},
            {"action": "FINISH", "reason": "stop", "args": {}},
        ]
    )
    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: next(actions),
    )
    assert "night" in state.search_strategy


def test_generate_without_search_is_coerced_to_search() -> None:
    state = _state()
    actions = iter(
        [
            {"action": "GENERATE_CANDIDATES", "reason": "skip search", "args": {}},
            {"action": "FINISH", "reason": "finish", "args": {}},
        ]
    )
    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: next(actions),
    )
    assert state.planning_history
    assert state.planning_history[0]["action"] == "SEARCH"


def test_duplicate_generate_is_coerced_to_finish() -> None:
    state = _state()
    state.search_results = [{"id": "s0"}]
    state.search_results_count = 1
    actions = iter(
        [
            {"action": "GENERATE_CANDIDATES", "reason": "build", "args": {}},
            {"action": "GENERATE_CANDIDATES", "reason": "build again", "args": {}},
        ]
    )
    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: next(actions),
    )
    assert len(state.planning_history) >= 2
    assert state.planning_history[1]["action"] == "FINISH"
    assert state.finish_ready is True


def test_missing_action_with_strategy_is_auto_repaired() -> None:
    state = _state()
    logs: list[tuple[str, str]] = []
    actions = iter(
        [
            {"reason": "repair me", "args": {"strategy": "night"}},
            {"action": "FINISH", "reason": "stop", "args": {}},
        ]
    )

    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: next(actions),
        logger=lambda _s, message, level="info": logs.append((level, message)),
    )

    assert state.planning_history
    assert state.planning_history[0]["action"] == "SEARCH"
    assert any("auto-repair" in message for _, message in logs)


def test_revise_without_biases_uses_default_biases() -> None:
    state = _state()
    state.search_results = [{"id": "s0"}]
    state.search_results_count = 1
    state.candidate_plans = [{"plan_id": "classic_first", "summary": object(), "itinerary": object()}]
    state.candidate_plans_count = 1

    actions = iter(
        [
            {"action": "REVISE", "reason": "revise but empty args", "args": {}},
            {"action": "FINISH", "reason": "stop", "args": {}},
        ]
    )
    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: next(actions),
    )
    assert state.revision_biases
    assert "include_meal_stop" in state.revision_biases


def test_call_llm_with_dirty_json_can_be_extracted(monkeypatch) -> None:
    import app.services.planning_loop as planning_loop_module

    monkeypatch.setenv(planning_loop_module.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(planning_loop_module.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(planning_loop_module.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(planning_loop_module.LLM_BASE_URL_ENV, "http://example.invalid")
    monkeypatch.setenv(planning_loop_module.LLM_MODEL_ENV, "dummy-model")

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            inner = "```json\n" '{"action":"SEARCH","reason":"补搜","args":{"strategy":"night",}}\n' "```"
            content = {"choices": [{"message": {"content": inner}}]}
            return json.dumps(content, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr(planning_loop_module, "urlopen", lambda req, timeout=6: _FakeResponse())

    payload, debug = planning_loop_module._call_llm_with_debug("test")
    assert payload is not None
    assert payload.get("action") == "SEARCH"
    assert debug["llm_called"] is True
    assert debug["llm_json_parse_ok"] is True


def test_call_llm_exception_returns_llm_call_exception(monkeypatch) -> None:
    import app.services.planning_loop as planning_loop_module

    monkeypatch.setenv(planning_loop_module.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(planning_loop_module.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(planning_loop_module.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(planning_loop_module.LLM_BASE_URL_ENV, "http://example.invalid")
    monkeypatch.setenv(planning_loop_module.LLM_MODEL_ENV, "dummy-model")

    def _raise(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(planning_loop_module, "urlopen", _raise)
    payload, debug = planning_loop_module._call_llm_with_debug("test")
    assert payload is None
    assert debug["fallback_reason"] == "llm_call_exception"
