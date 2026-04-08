from __future__ import annotations

import uuid
from pathlib import Path

from app.models.schemas import ItineraryResponse, PlanRequest, PlanSummary, RouteItem
from app.services import agent_graph


def _temp_db_path() -> str:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"agent_state_{uuid.uuid4().hex}.db")


def _sample_request() -> PlanRequest:
    return PlanRequest(
        companion_type="parents",
        available_hours=4,
        budget_level="medium",
        purpose="tourism",
        need_meal=True,
        walking_tolerance="low",
        weather="sunny",
        origin="钟楼",
    )


def _sample_itinerary() -> ItineraryResponse:
    return ItineraryResponse(
        summary="test",
        route=[
            RouteItem(
                time_slot="09:00-10:00",
                type="sight",
                name="钟楼",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="同簇连续，减少折返",
                estimated_distance_meters=200,
                estimated_duration_minutes=5,
            )
        ],
        tips=[],
    )


def _sample_summary() -> PlanSummary:
    return PlanSummary(
        plan_id="classic_first",
        variant_label="经典优先",
        stop_count=1,
        clusters=["城墙钟鼓楼簇"],
        is_cross_cluster=False,
        cross_cluster_count=0,
        cluster_transition_summary="城墙钟鼓楼簇",
        has_meal=False,
        total_distance_meters=200,
        total_duration_minutes=5,
        rhythm="轻松",
        budget_level="medium",
        walking_tolerance="low",
        purpose="tourism",
        diff_points=["簇分布:城墙钟鼓楼簇"],
        bias_tags=["classic"],
        note="偏经典",
    )


def _mock_candidates(request: PlanRequest, candidate_pois=None, quality_feedback=None, area_context=None, **kwargs):
    return [{"plan_id": "classic_first", "request": request, "itinerary": _sample_itinerary(), "summary": _sample_summary()}]


def test_agent_v3_returns_planning_history(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setattr(agent_graph, "parse_free_text_to_plan_request", lambda text: _sample_request())
    monkeypatch.setattr(agent_graph, "load_pois", lambda request_context=None: [])
    monkeypatch.setattr(agent_graph, "generate_candidate_plans", _mock_candidates)
    monkeypatch.setattr(agent_graph, "select_plan_with_llm", lambda request, plan_summaries: None)

    def fake_loop(state, **kwargs):
        state.planning_history = [
            {"step": 1, "action": "SEARCH", "reason": "first", "args": {}, "outcome_summary": "ok"},
            {"step": 2, "action": "FINISH", "reason": "done", "args": {}, "outcome_summary": "finish_ready=true"},
        ]
        state.finish_ready = True
        state.search_results = [{"id": "s1"}, {"id": "r1"}]
        state.search_results_count = 2
        state.candidate_plans = _mock_candidates(_sample_request())
        state.candidate_plans_count = 1
        state.alternative_plans_summary = [state.candidate_plans[0]["summary"]]
        return state

    monkeypatch.setattr(agent_graph, "run_planning_loop", fake_loop)

    response = agent_graph.run_agent_v3("陪父母半天，不想太累", thread_id=None, user_key="u1")
    assert response.planning_history
    assert response.search_results_count == 2
    assert response.candidate_plans_count == 1
    assert response.selected_plan is not None


def test_agent_v3_clarify_then_continue_keeps_loop_state(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setattr(agent_graph, "parse_free_text_to_plan_request", lambda text: _sample_request())
    monkeypatch.setattr(agent_graph, "load_pois", lambda request_context=None: [])
    monkeypatch.setattr(agent_graph, "generate_candidate_plans", _mock_candidates)
    monkeypatch.setattr(agent_graph, "select_plan_with_llm", lambda request, plan_summaries: None)

    def fake_loop(state, **kwargs):
        state.planning_history = [
            {"step": 1, "action": "GENERATE_CANDIDATES", "reason": "gen", "args": {}, "outcome_summary": "candidate_plans_count=1"}
        ]
        state.candidate_plans = _mock_candidates(_sample_request())
        state.candidate_plans_count = 1
        state.alternative_plans_summary = [state.candidate_plans[0]["summary"]]
        state.finish_ready = True
        return state

    monkeypatch.setattr(agent_graph, "run_planning_loop", fake_loop)

    first = agent_graph.run_agent_v3("想出去玩", thread_id=None, user_key="u2")
    assert first.clarification_needed is True

    resumed = agent_graph.continue_agent(first.thread_id, "下午出去，4小时，从钟楼出发")
    assert resumed.clarification_needed is False
    assert resumed.planning_history
    assert resumed.selected_plan is not None
