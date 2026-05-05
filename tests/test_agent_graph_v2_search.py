from __future__ import annotations

import uuid
from pathlib import Path

from app.models.schemas import PlanRequest
from app.services import agent_graph, search_planner


def _temp_db_path() -> str:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"agent_state_{uuid.uuid4().hex}.db")


def _sample_request() -> PlanRequest:
    return PlanRequest(
        companion_type="partner",
        available_hours=4,
        budget_level="medium",
        purpose="dating",
        need_meal=True,
        walking_tolerance="low",
        weather="hot",
        origin="????",
        origin_preference_mode="nearby",
        preferred_period="evening",
    )


def test_dynamic_search_rounds_and_strategy_matrix(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    sample_pois = [
        {
            "id": "s1",
            "name": "??",
            "kind": "sight",
            "district_cluster": "??????",
            "category": "landmark",
            "indoor_or_outdoor": "outdoor",
            "walking_level": "medium",
        },
        {
            "id": "s2",
            "name": "?????",
            "kind": "sight",
            "district_cluster": "?????",
            "category": "night",
            "indoor_or_outdoor": "outdoor",
            "walking_level": "medium",
        },
        {
            "id": "r1",
            "name": "???",
            "kind": "restaurant",
            "district_cluster": "?????",
            "category": "food",
            "indoor_or_outdoor": "indoor",
            "walking_level": "low",
        },
    ]
    monkeypatch.setattr(agent_graph, "load_pois", lambda request_context=None: sample_pois)

    state = agent_graph.AgentState(user_input="night date with dinner", parsed_request=_sample_request(), thread_id="t-search")
    agent_graph.analyze_search_intent(state)
    agent_graph.dynamic_search(state)
    agent_graph.refine_search_results(state)

    assert state.primary_strategies
    assert "night" in state.primary_strategies
    assert "prioritize_night_view" in state.candidate_biases
    assert state.search_strategy
    assert state.search_round >= 1
    assert state.search_results
    assert state.debug_logs


def test_analyze_search_intent_injects_demand_profile(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    request_payload = _sample_request().model_dump()
    request_payload.update(
        {
            "companion_type": "solo",
            "purpose": "tourism",
            "preferred_period": None,
            "walking_tolerance": "medium",
            "need_meal": True,
        }
    )
    request = PlanRequest(**request_payload)
    state = agent_graph.AgentState(
        user_input="want a relaxed trip with food",
        parsed_request=request,
        thread_id="t-demand",
    )
    agent_graph.analyze_search_intent(state)

    assert state.search_intent is not None
    assert "demand_tags" in state.search_intent
    assert isinstance(state.search_intent.get("demand_tags"), list)
    assert state.search_strategy
    assert "food" in state.search_strategy


def test_search_planner_failure_falls_back_to_rule_based(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())

    def _raise(*_, **__):
        raise RuntimeError("planner boom")

    monkeypatch.setattr(search_planner, "build_search_plan", _raise)
    state = agent_graph.AgentState(user_input="want park then bbq", parsed_request=_sample_request(), thread_id="t-planner-fail")

    agent_graph.analyze_search_intent(state)

    assert state.search_mode == "rule_based"
    assert state.llm_search_planner_success is False
    assert state.llm_search_planner_error_type == "RuntimeError"
    assert state.search_strategy


def test_park_search_plan_keeps_queries_non_empty(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    request_payload = _sample_request().model_dump()
    request_payload.update(
        {
            "origin": "??",
            "origin_preference_mode": None,
            "purpose": "relax",
            "preferred_period": None,
            "walking_tolerance": "low",
        }
    )
    state = agent_graph.AgentState(
        user_input="park then bbq",
        parsed_request=PlanRequest(**request_payload),
        thread_id="t-park-search",
    )

    agent_graph.analyze_search_intent(state)

    assert state.search_plan_used
    assert state.final_search_queries
    assert isinstance(state.search_plan_used.get("search_rounds"), list)
