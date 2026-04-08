from __future__ import annotations

from typing import Any, Dict

from app.models.schemas import ItineraryResponse, PlanRequest, PlanSummary, RouteItem
from app.services import agent_graph
from app.services.agent_state import AgentState
from app.services.planning_loop import run_planning_loop
from app.services.skills_registry import get_active_skills_for_agent


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
                reason="test",
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
        walking_tolerance="medium",
        purpose="tourism",
        diff_points=["簇分布:城墙钟鼓楼簇"],
        bias_tags=["classic"],
        note="偏经典",
    )


def test_get_active_skills_for_agent_returns_expected_list() -> None:
    active_skills = set(get_active_skills_for_agent())
    assert {
        "parse_request_skill",
        "recall_memory_skill",
        "search_candidate_skill",
        "generate_candidates_skill",
        "select_plan_skill",
        "render_response_skill",
        "knowledge_enrichment_skill",
        "evaluation_skill",
        "amap_search_skill",
        "amap_geocode_skill",
        "amap_route_skill",
        "amap_weather_skill",
    }.issubset(active_skills)


def test_agent_graph_records_runtime_skill_trace(monkeypatch) -> None:
    monkeypatch.setattr(agent_graph, "parse_free_text_to_plan_request", lambda text: _sample_request())
    monkeypatch.setattr(
        agent_graph,
        "generate_candidate_plans",
        lambda request, candidate_pois=None, quality_feedback=None, area_context=None, **kwargs: [
            {
                "plan_id": "classic_first",
                "request": request,
                "itinerary": _sample_itinerary(),
                "summary": _sample_summary(),
            }
        ],
    )
    monkeypatch.setattr(
        agent_graph,
        "select_plan_with_llm",
        lambda request, plan_summaries: {
            "selected_plan_id": "classic_first",
            "selection_reason": "ok",
            "reason_tags": ["少跨簇"],
        },
    )
    monkeypatch.setattr(
        agent_graph,
        "load_pois",
        lambda request_context=None: [
            {
                "id": "s1",
                "name": "钟楼",
                "kind": "sight",
                "district_cluster": "城墙钟鼓楼簇",
                "category": "landmark",
            },
            {
                "id": "r1",
                "name": "回民街",
                "kind": "restaurant",
                "district_cluster": "城墙钟鼓楼簇",
                "category": "food",
            },
        ],
    )

    response = agent_graph.run_agent_v2("陪父母半天，不想太累，中午想吃饭", thread_id="skill-trace-thread")
    trace_names = {item.get("skill_name") for item in response.skill_trace}
    assert {
        "parse_request_skill",
        "recall_memory_skill",
        "search_candidate_skill",
        "generate_candidates_skill",
        "select_plan_skill",
        "render_response_skill",
        "knowledge_enrichment_skill",
    }.issubset(trace_names)
    assert any("skill invoked:" in log.message for log in response.debug_logs)
    assert any("knowledge_enrichment_skill" in log.message for log in response.debug_logs)
    assert response.active_skill in trace_names
    assert response.last_skill_result_summary


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


def test_planning_history_contains_skill_name_and_logs() -> None:
    state = AgentState(
        parsed_request=_sample_request(),
        planning_loop_enabled=True,
        planning_max_steps=3,
        search_strategy=["classic"],
    )
    actions = iter(
        [
            {"action": "SEARCH", "reason": "first search", "args": {}},
            {"action": "GENERATE_CANDIDATES", "reason": "build", "args": {}},
            {"action": "FINISH", "reason": "done", "args": {}},
        ]
    )
    logs: list[str] = []

    run_planning_loop(
        state,
        dynamic_search_fn=_search_fn,
        refine_search_results_fn=_refine_fn,
        generate_candidates_fn=_generate_fn,
        action_decider=lambda _: next(actions),
        logger=lambda _s, message, level="info": logs.append(message),
    )

    assert state.planning_history
    assert all(item.get("skill_name") for item in state.planning_history)
    assert any("skill invoked:" in message for message in logs)
