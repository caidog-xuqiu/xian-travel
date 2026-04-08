from __future__ import annotations

import uuid
from pathlib import Path

from app.models.schemas import ItineraryResponse, PlanRequest, PlanSummary, RouteItem
from app.services import agent_graph
from app.services.agent_state import AgentState
from app.services.memory_store import recall_user_memory


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


def test_agent_graph_full_flow(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setattr(agent_graph, "parse_free_text_to_plan_request", lambda text: _sample_request())
    monkeypatch.setattr(
        agent_graph,
        "generate_candidate_plans",
        lambda request, candidate_pois=None, quality_feedback=None, area_context=None, **kwargs: [
            {"plan_id": "classic_first", "request": request, "itinerary": _sample_itinerary(), "summary": _sample_summary()}
        ],
    )
    monkeypatch.setattr(
        agent_graph,
        "select_plan_with_llm",
        lambda request, plan_summaries: {"selected_plan_id": "classic_first", "selection_reason": "ok", "reason_tags": ["少跨簇"]},
    )
    monkeypatch.setattr(
        agent_graph,
        "load_pois",
        lambda request_context=None: [
            {"id": "s1", "name": "钟楼", "kind": "sight", "district_cluster": "城墙钟鼓楼簇", "category": "landmark"},
            {"id": "r1", "name": "回民街", "kind": "restaurant", "district_cluster": "城墙钟鼓楼簇", "category": "food"},
        ],
    )

    response = agent_graph.run_agent("陪父母半天，不想太累", thread_id=None)
    assert response.thread_id
    assert response.selected_plan is not None
    assert response.selected_by == "llm"
    assert response.readable_output is not None
    assert response.debug_logs
    assert response.debug_logs[0].message
    assert "fewer_cross_cluster" in response.candidate_biases
    assert recall_user_memory(response.thread_id) is not None


def test_agent_graph_clarification_branch(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setattr(
        agent_graph,
        "load_pois",
        lambda request_context=None: [],
    )
    response = agent_graph.run_agent("想出去玩", thread_id=None)
    assert response.clarification_needed is True
    assert response.clarification_question


def test_agent_graph_thread_checkpoint(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setattr(agent_graph, "parse_free_text_to_plan_request", lambda text: _sample_request())
    monkeypatch.setattr(
        agent_graph,
        "generate_candidate_plans",
        lambda request, candidate_pois=None, quality_feedback=None, area_context=None, **kwargs: [
            {"plan_id": "classic_first", "request": request, "itinerary": _sample_itinerary(), "summary": _sample_summary()}
        ],
    )
    monkeypatch.setattr(
        agent_graph,
        "select_plan_with_llm",
        lambda request, plan_summaries: None,
    )
    monkeypatch.setattr(
        agent_graph,
        "load_pois",
        lambda request_context=None: [],
    )

    response = agent_graph.run_agent("陪父母半天", thread_id="thread-1")
    snapshot = agent_graph.get_latest_thread_state("thread-1")
    assert snapshot is not None
    assert response.thread_id == "thread-1"


def test_agent_graph_logs_candidate_quality_warning(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setattr(agent_graph, "parse_free_text_to_plan_request", lambda text: _sample_request())

    def _fake_candidates(request, candidate_pois=None, quality_feedback=None, area_context=None, **kwargs):
        if quality_feedback is not None:
            quality_feedback.update(
                {
                    "candidate_count": 1,
                    "diversity_insufficient": True,
                    "too_similar_pairs": [("classic_first", "relaxed_first")],
                    "diversity_retry_count": 1,
                }
            )
        return [{"plan_id": "classic_first", "request": request, "itinerary": _sample_itinerary(), "summary": _sample_summary()}]

    monkeypatch.setattr(agent_graph, "generate_candidate_plans", _fake_candidates)
    monkeypatch.setattr(
        agent_graph,
        "select_plan_with_llm",
        lambda request, plan_summaries: {"selected_plan_id": "classic_first", "selection_reason": "ok", "reason_tags": ["少跨簇"]},
    )
    monkeypatch.setattr(
        agent_graph,
        "load_pois",
        lambda request_context=None: [
            {"id": "s1", "name": "钟楼", "kind": "sight", "district_cluster": "城墙钟鼓楼簇", "category": "landmark"},
            {"id": "r1", "name": "回民街", "kind": "restaurant", "district_cluster": "城墙钟鼓楼簇", "category": "food"},
        ],
    )

    response = agent_graph.run_agent("陪父母半天，不想太累", thread_id=None)
    messages = [log.message for log in response.debug_logs]
    assert any("候选质量告警" in msg for msg in messages)


def test_select_plan_post_check_prefers_meal_when_needed(monkeypatch) -> None:
    request = _sample_request().model_copy(update={"need_meal": True})
    no_meal_itinerary = ItineraryResponse(
        summary="no-meal",
        route=[
            RouteItem(
                time_slot="09:00-10:00",
                type="sight",
                name="钟楼",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="test",
            )
        ],
        tips=[],
    )
    meal_itinerary = ItineraryResponse(
        summary="with-meal",
        route=[
            RouteItem(
                time_slot="09:00-10:00",
                type="sight",
                name="钟楼",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="test",
            ),
            RouteItem(
                time_slot="10:10-11:00",
                type="restaurant",
                name="回民街餐饮",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="test",
            ),
        ],
        tips=[],
    )
    no_meal_summary = _sample_summary().model_copy(
        update={"plan_id": "classic_first", "has_meal": False, "stop_count": 1}
    )
    meal_summary = _sample_summary().model_copy(
        update={"plan_id": "food_friendly", "has_meal": True, "stop_count": 2, "purpose": "food"}
    )

    state = AgentState(
        parsed_request=request,
        candidate_plans=[
            {"plan_id": "classic_first", "request": request, "itinerary": no_meal_itinerary, "summary": no_meal_summary},
            {"plan_id": "food_friendly", "request": request, "itinerary": meal_itinerary, "summary": meal_summary},
        ],
        alternative_plans_summary=[no_meal_summary, meal_summary],
    )

    monkeypatch.setattr(
        agent_graph,
        "select_plan_with_llm",
        lambda request, plan_summaries: {
            "selected_plan_id": "classic_first",
            "selection_reason": "raw llm",
            "reason_tags": ["llm_raw"],
        },
    )

    agent_graph.select_plan(state)
    assert state.selected_plan is not None
    assert any(item.type == "restaurant" for item in state.selected_plan.route)
    assert "后置复核改选" in state.reason_tags


def test_select_plan_post_check_prefers_evening_night_meal(monkeypatch) -> None:
    request = _sample_request().model_copy(
        update={
            "companion_type": "partner",
            "purpose": "dating",
            "preferred_period": "evening",
            "need_meal": True,
            "available_hours": 6,
        }
    )
    classic_itinerary = ItineraryResponse(
        summary="classic",
        route=[
            RouteItem(
                time_slot="18:00-19:00",
                type="sight",
                name="钟楼",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="test",
            )
        ],
        tips=[],
    )
    night_itinerary = ItineraryResponse(
        summary="night",
        route=[
            RouteItem(
                time_slot="18:00-19:00",
                type="sight",
                name="大唐不夜城",
                district_cluster="曲江夜游簇",
                transport_from_prev="地铁",
                reason="test",
            ),
            RouteItem(
                time_slot="19:10-20:00",
                type="restaurant",
                name="曲江晚餐",
                district_cluster="曲江夜游簇",
                transport_from_prev="步行",
                reason="test",
            ),
        ],
        tips=[],
    )
    classic_summary = _sample_summary().model_copy(
        update={
            "plan_id": "classic_first",
            "clusters": ["城墙钟鼓楼簇"],
            "cluster_transition_summary": "城墙钟鼓楼簇",
            "has_meal": False,
            "stop_count": 1,
            "purpose": "tourism",
        }
    )
    night_summary = _sample_summary().model_copy(
        update={
            "plan_id": "food_friendly",
            "clusters": ["曲江夜游簇"],
            "cluster_transition_summary": "曲江夜游簇",
            "has_meal": True,
            "stop_count": 2,
            "purpose": "dating",
            "bias_tags": ["food", "prioritize_night_view"],
            "note": "夜游+晚餐",
        }
    )

    state = AgentState(
        parsed_request=request,
        candidate_plans=[
            {"plan_id": "classic_first", "request": request, "itinerary": classic_itinerary, "summary": classic_summary},
            {"plan_id": "food_friendly", "request": request, "itinerary": night_itinerary, "summary": night_summary},
        ],
        alternative_plans_summary=[classic_summary, night_summary],
    )

    monkeypatch.setattr(
        agent_graph,
        "select_plan_with_llm",
        lambda request, plan_summaries: {
            "selected_plan_id": "classic_first",
            "selection_reason": "raw llm",
            "reason_tags": ["llm_raw"],
        },
    )

    agent_graph.select_plan(state)
    assert state.selected_plan is not None
    assert any(item.district_cluster == "曲江夜游簇" for item in state.selected_plan.route)


def test_select_plan_network_fallback_uses_local_constraint_ranking(monkeypatch) -> None:
    request = _sample_request().model_copy(update={"need_meal": True, "available_hours": 6})
    weak_first_itinerary = ItineraryResponse(
        summary="weak",
        route=[
            RouteItem(
                time_slot="09:00-10:00",
                type="sight",
                name="钟楼",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="test",
            )
        ],
        tips=[],
    )
    better_second_itinerary = ItineraryResponse(
        summary="better",
        route=[
            RouteItem(
                time_slot="09:00-10:00",
                type="sight",
                name="钟楼",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="test",
            ),
            RouteItem(
                time_slot="10:10-11:00",
                type="restaurant",
                name="回民街餐饮",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="test",
            ),
        ],
        tips=[],
    )
    weak_summary = _sample_summary().model_copy(
        update={"plan_id": "classic_first", "has_meal": False, "stop_count": 1, "purpose": "tourism"}
    )
    better_summary = _sample_summary().model_copy(
        update={"plan_id": "food_friendly", "has_meal": True, "stop_count": 2, "purpose": "food", "bias_tags": ["food"]}
    )
    state = AgentState(
        parsed_request=request,
        candidate_plans=[
            {"plan_id": "classic_first", "request": request, "itinerary": weak_first_itinerary, "summary": weak_summary},
            {"plan_id": "food_friendly", "request": request, "itinerary": better_second_itinerary, "summary": better_summary},
        ],
        alternative_plans_summary=[weak_summary, better_summary],
    )
    monkeypatch.setattr(agent_graph, "select_plan_with_llm", lambda request, plan_summaries: None)
    monkeypatch.setattr(
        agent_graph,
        "get_last_selector_debug",
        lambda: {
            "llm_selector_called": True,
            "llm_selector_raw_response_exists": False,
            "llm_selector_json_parse_ok": False,
            "llm_selector_schema_ok": False,
            "llm_selector_retry_count": 1,
            "llm_selected_plan_valid": False,
            "selector_error_type": "network_exception",
            "fallback_reason": "llm_selector_call_exception",
        },
    )

    agent_graph.select_plan(state)
    assert state.selected_by == "fallback_rule"
    assert state.selected_plan is not None
    assert any(item.type == "restaurant" for item in state.selected_plan.route)
    assert "本地约束回退" in state.reason_tags
    assert any("llm_selector_call_exception" in log.message for log in state.debug_logs)
    assert any("selector_local_rank_fallback" in log.message for log in state.debug_logs)


def test_select_plan_network_fallback_prefers_night_meal_plan(monkeypatch) -> None:
    request = _sample_request().model_copy(
        update={
            "companion_type": "partner",
            "purpose": "dating",
            "preferred_period": "evening",
            "need_meal": True,
            "available_hours": 4,
        }
    )
    day_itinerary = ItineraryResponse(
        summary="day",
        route=[
            RouteItem(
                time_slot="18:00-19:00",
                type="sight",
                name="钟楼",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="test",
            )
        ],
        tips=[],
    )
    night_itinerary = ItineraryResponse(
        summary="night",
        route=[
            RouteItem(
                time_slot="18:00-19:00",
                type="sight",
                name="大唐不夜城",
                district_cluster="曲江夜游簇",
                transport_from_prev="地铁",
                reason="test",
            ),
            RouteItem(
                time_slot="19:10-20:00",
                type="restaurant",
                name="曲江晚餐",
                district_cluster="曲江夜游簇",
                transport_from_prev="步行",
                reason="test",
            ),
        ],
        tips=[],
    )
    day_summary = _sample_summary().model_copy(
        update={
            "plan_id": "classic_first",
            "clusters": ["城墙钟鼓楼簇"],
            "cluster_transition_summary": "城墙钟鼓楼簇",
            "has_meal": False,
            "stop_count": 1,
            "purpose": "tourism",
            "bias_tags": ["classic"],
        }
    )
    night_summary = _sample_summary().model_copy(
        update={
            "plan_id": "food_friendly",
            "clusters": ["曲江夜游簇"],
            "cluster_transition_summary": "曲江夜游簇",
            "has_meal": True,
            "stop_count": 2,
            "purpose": "dating",
            "bias_tags": ["food", "prioritize_night_view"],
            "note": "夜游+晚餐",
        }
    )
    state = AgentState(
        parsed_request=request,
        candidate_plans=[
            {"plan_id": "classic_first", "request": request, "itinerary": day_itinerary, "summary": day_summary},
            {"plan_id": "food_friendly", "request": request, "itinerary": night_itinerary, "summary": night_summary},
        ],
        alternative_plans_summary=[day_summary, night_summary],
    )
    monkeypatch.setattr(agent_graph, "select_plan_with_llm", lambda request, plan_summaries: None)
    monkeypatch.setattr(
        agent_graph,
        "get_last_selector_debug",
        lambda: {
            "llm_selector_called": True,
            "llm_selector_raw_response_exists": False,
            "llm_selector_json_parse_ok": False,
            "llm_selector_schema_ok": False,
            "llm_selector_retry_count": 1,
            "llm_selected_plan_valid": False,
            "selector_error_type": "network_exception",
            "fallback_reason": "llm_selector_call_exception",
        },
    )

    agent_graph.select_plan(state)
    assert state.selected_by == "fallback_rule"
    assert state.selected_plan is not None
    assert any(item.district_cluster == "曲江夜游簇" for item in state.selected_plan.route)
    assert any(item.type == "restaurant" for item in state.selected_plan.route)
    assert any("selector_local_rank_fallback" in log.message for log in state.debug_logs)
