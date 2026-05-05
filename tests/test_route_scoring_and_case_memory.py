from __future__ import annotations

import uuid
from pathlib import Path

from app.models.schemas import ItineraryResponse, PlanRequest, RouteFeedbackRequest, RouteItem
from app.routes import plan as plan_routes
from app.services import sqlite_store
from app.services.agent_state import AgentState
from app.services import agent_graph
from app.services.case_memory import build_case_bias, retrieve_high_score_cases, save_high_quality_case
from app.services.route_scoring import score_route_case, score_with_user_feedback, should_store_case


def _temp_db_path() -> str:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"agent_state_{uuid.uuid4().hex}.db")


def _request() -> PlanRequest:
    return PlanRequest(
        companion_type="parents",
        available_hours=3.0,
        budget_level="medium",
        purpose="relax",
        need_meal=True,
        walking_tolerance="low",
        weather="rainy",
        origin="钟楼",
        preferred_period="midday",
    )


def _itinerary() -> ItineraryResponse:
    return ItineraryResponse(
        summary="轻松半日路线",
        route=[
            RouteItem(
                time_slot="11:00-11:40",
                type="sight",
                name="城市公园",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="从起点前往（打车 约8分钟）",
                reason="低强度停留",
                estimated_duration_minutes=8,
            ),
            RouteItem(
                time_slot="11:48-12:48",
                type="restaurant",
                name="顺路餐厅",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行 约8分钟",
                reason="保留吃饭节点",
                estimated_duration_minutes=8,
            ),
        ],
        tips=[],
    )


def test_route_scoring_and_threshold_store() -> None:
    score = score_route_case(
        request_context=_request(),
        selected_plan=_itinerary(),
        selected_plan_area_summary={"cross_area_count": 0},
        route_source="amap",
        explanation_basis=["陪父母低强度，少步行，保留吃饭节点"],
    )

    assert score["constraint_score"] >= 2.0
    assert score["plan_quality_score"] > 0
    assert score["total_score"] == 5.0
    assert should_store_case(score, _itinerary())["should_store"] is False
    with_feedback = score_with_user_feedback(score["score_breakdown"], 8)
    decision = should_store_case(with_feedback, _itinerary())
    assert with_feedback["total_score"] >= 8.0
    assert decision["should_store"] is True


def test_sqlite_case_memory_and_case_retrieval(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    score = score_route_case(
        request_context=_request(),
        selected_plan=_itinerary(),
        selected_plan_area_summary={"cross_area_count": 0},
        route_source="amap",
        explanation_basis=["陪父母低强度，少步行，保留吃饭节点"],
    )
    feedback_score = score_with_user_feedback(score["score_breakdown"], 9)
    stored = save_high_quality_case(
        user_key="u1",
        user_query="陪父母半天，想轻松吃饭",
        parsed_request=_request(),
        selected_plan="relaxed_first",
        itinerary=_itinerary(),
        route_summary={"cross_area_count": 0},
        knowledge_ids=["rainy_low_walk"],
        knowledge_bias={"prefer_low_walk": True},
        score_result=feedback_score,
    )

    assert stored["stored_to_case_memory"] is True
    rows = sqlite_store.list_recent_high_score_cases(user_key="u1", limit=5)
    assert len(rows) == 1
    cases = retrieve_high_score_cases(_request().model_dump(), user_key="u1")
    assert cases
    bias = build_case_bias(cases)
    assert bias["prefer_low_walk"] is True
    assert bias["case_ids"] == [stored["case_memory_id"]]


def test_route_feedback_submission_updates_or_stores(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    system_score = score_route_case(
        request_context=_request(),
        selected_plan=_itinerary(),
        selected_plan_area_summary={"cross_area_count": 0},
        route_source="amap",
        explanation_basis=["陪父母低强度，少步行，保留吃饭节点"],
    )
    response = plan_routes.submit_route_feedback(
        RouteFeedbackRequest(
            user_key="u2",
            user_query="陪父母半天，想轻松吃饭",
            selected_plan="relaxed_first",
            itinerary=_itinerary().model_dump(),
            system_score_breakdown=system_score["score_breakdown"],
            user_rating=9,
            feedback_text="很合适",
            parsed_request=_request().model_dump(),
            route_summary={"cross_area_count": 0, "route_source": "amap"},
        )
    )

    assert response.feedback_id is not None
    assert response.final_total_score > system_score["total_score"]
    assert response.stored_to_case_memory is True
    assert response.case_memory_id is not None


def test_score_with_user_feedback_mapping() -> None:
    result = score_with_user_feedback({"constraint_score": 3.0, "plan_quality_score": 2.0}, 10)
    assert result["user_feedback_score"] == 5.0
    assert result["total_score"] == 10.0


def test_high_score_case_participates_in_rag_without_error(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    score = score_route_case(
        request_context=_request(),
        selected_plan=_itinerary(),
        selected_plan_area_summary={"cross_area_count": 0},
        route_source="amap",
        explanation_basis=["陪父母低强度，少步行，保留吃饭节点"],
    )
    feedback_score = score_with_user_feedback(score["score_breakdown"], 9)
    save_high_quality_case(
        user_key="u-rag",
        user_query="陪父母半天，想轻松吃饭",
        parsed_request=_request(),
        selected_plan="relaxed_first",
        itinerary=_itinerary(),
        route_summary={"cross_area_count": 0},
        knowledge_ids=["rainy_low_walk"],
        knowledge_bias={"prefer_low_walk": True},
        score_result=feedback_score,
    )
    state = AgentState(
        user_input="陪父母半天，想轻松吃饭",
        parsed_request=_request(),
        thread_id="case-rag-thread",
        user_key="u-rag",
    )

    agent_graph.analyze_search_intent(state)

    assert state.retrieved_case_count >= 1
    assert state.retrieved_case_ids
    assert state.case_memory_used is True
    assert "high_score_cases" in state.knowledge_source_tags
