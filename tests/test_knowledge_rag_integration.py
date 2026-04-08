from __future__ import annotations

import uuid
from pathlib import Path

from app.models.schemas import PlanRequest
from app.services import agent_graph
from app.services.agent_state import AgentState
from app.services.data_loader import load_pois


def _sample_request() -> PlanRequest:
    return PlanRequest(
        companion_type="parents",
        available_hours=3.0,
        budget_level="medium",
        purpose="tourism",
        need_meal=True,
        walking_tolerance="low",
        weather="rainy",
        origin="钟楼",
        preferred_period="midday",
    )


def _temp_db_path() -> str:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"agent_state_{uuid.uuid4().hex}.db")


def test_analyze_search_intent_populates_knowledge_bias(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    state = AgentState(
        user_input="陪父母半天，不想太累，中午想吃饭，下雨也能玩",
        parsed_request=_sample_request(),
        thread_id="knowledge-thread-1",
    )

    agent_graph.analyze_search_intent(state)

    assert state.knowledge_used_count > 0
    assert state.knowledge_ids
    assert state.knowledge_bias
    assert isinstance(state.explanation_basis, list)
    assert "prefer_indoor" in state.knowledge_bias


def test_main_chain_nodes_keep_running_with_knowledge_fields(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    request = _sample_request()
    state = AgentState(
        user_input="陪父母半天，不想太累，中午想吃饭，下雨也能玩",
        parsed_request=request,
        thread_id="knowledge-thread-2",
    )

    agent_graph.analyze_search_intent(state)
    state.search_results = load_pois(request_context=request)[:30]
    state.search_results_count = len(state.search_results)
    agent_graph.generate_candidates(state)

    assert state.candidate_plans_count > 0
    state.selected_plan = state.candidate_plans[0]["itinerary"]
    state.selection_reason = "test"
    state.selected_by = "fallback_rule"
    agent_graph.render_output(state)

    response = agent_graph._response_from_state(state)
    assert response.knowledge_used_count == state.knowledge_used_count
    assert response.knowledge_ids == state.knowledge_ids
    assert response.knowledge_bias == state.knowledge_bias
    assert response.explanation_basis == state.explanation_basis
    assert response.final_response is not None
    assert "knowledge_used_count" in response.final_response
    assert "knowledge_ids" in response.final_response
    assert "knowledge_bias" in response.final_response
    assert "explanation_basis" in response.final_response
