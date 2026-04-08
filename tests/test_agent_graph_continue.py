from __future__ import annotations

import uuid
from pathlib import Path

import pytest

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


def _mock_candidates(
    request: PlanRequest,
    candidate_pois: list[dict] | None = None,
    quality_feedback: dict | None = None,
    area_context: dict | None = None,
    **kwargs,
) -> list[dict]:
    return [{"plan_id": "classic_first", "request": request, "itinerary": _sample_itinerary(), "summary": _sample_summary()}]


def test_agent_continue_success(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setattr(agent_graph, "parse_free_text_to_plan_request", lambda text: _sample_request())
    monkeypatch.setattr(agent_graph, "generate_candidate_plans", _mock_candidates)
    monkeypatch.setattr(
        agent_graph,
        "select_plan_with_llm",
        lambda request, plan_summaries: {"selected_plan_id": "classic_first", "selection_reason": "ok", "reason_tags": ["少跨簇"]},
    )
    monkeypatch.setattr(
        agent_graph,
        "load_pois",
        lambda request_context=None: [],
    )

    first = agent_graph.run_agent("想出去玩", thread_id=None)
    assert first.clarification_needed is True

    resumed = agent_graph.continue_agent(thread_id=first.thread_id, clarification_answer="下午出去，预算中等，从钟楼出发")
    assert resumed.selected_plan is not None
    assert resumed.clarification_needed is False
    snapshot = agent_graph.get_latest_thread_state(first.thread_id)
    assert snapshot is not None
    assert "补充：" in (snapshot.get("user_input") or "")


def test_agent_continue_still_needs_clarification(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setattr(agent_graph, "parse_free_text_to_plan_request", lambda text: _sample_request())
    monkeypatch.setattr(
        agent_graph,
        "load_pois",
        lambda request_context=None: [],
    )

    first = agent_graph.run_agent("想出去玩", thread_id=None)
    resumed = agent_graph.continue_agent(thread_id=first.thread_id, clarification_answer="我在钟楼附近")
    assert resumed.clarification_needed is True
    assert resumed.clarification_question


def test_agent_continue_invalid_thread(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    with pytest.raises(ValueError):
        agent_graph.continue_agent(thread_id="missing-thread", clarification_answer="下午出去")


def test_agent_continue_not_in_clarification(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setattr(agent_graph, "parse_free_text_to_plan_request", lambda text: _sample_request())
    monkeypatch.setattr(agent_graph, "generate_candidate_plans", _mock_candidates)
    monkeypatch.setattr(agent_graph, "select_plan_with_llm", lambda request, plan_summaries: None)
    monkeypatch.setattr(
        agent_graph,
        "load_pois",
        lambda request_context=None: [],
    )

    completed = agent_graph.run_agent("陪父母半天", thread_id="thread-ok")
    assert completed.clarification_needed is False

    with pytest.raises(ValueError):
        agent_graph.continue_agent(thread_id="thread-ok", clarification_answer="下午出去")
