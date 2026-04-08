from __future__ import annotations

import uuid
from pathlib import Path

from app.models.schemas import PlanRequest
from app.services import agent_graph


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
        origin="钟楼附近",
        origin_preference_mode="nearby",
        preferred_period="evening",
    )


def test_dynamic_search_rounds_and_strategy_matrix(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    sample_pois = [
        {
            "id": "s1",
            "name": "钟楼",
            "kind": "sight",
            "district_cluster": "城墙钟鼓楼簇",
            "category": "landmark",
            "indoor_or_outdoor": "outdoor",
            "walking_level": "medium",
        },
        {
            "id": "s2",
            "name": "大唐不夜城",
            "kind": "sight",
            "district_cluster": "曲江夜游簇",
            "category": "night",
            "indoor_or_outdoor": "outdoor",
            "walking_level": "medium",
        },
        {
            "id": "r1",
            "name": "小吃店",
            "kind": "restaurant",
            "district_cluster": "曲江夜游簇",
            "category": "food",
            "indoor_or_outdoor": "indoor",
            "walking_level": "low",
        },
    ]
    monkeypatch.setattr(agent_graph, "load_pois", lambda request_context=None: sample_pois)

    state = agent_graph.AgentState(user_input="test", parsed_request=_sample_request(), thread_id="t-search")
    agent_graph.analyze_search_intent(state)
    agent_graph.dynamic_search(state)
    agent_graph.refine_search_results(state)

    assert state.primary_strategies
    assert "night" in state.primary_strategies
    assert "prioritize_night_view" in state.candidate_biases
    assert state.search_strategy
    assert state.search_round >= 1
    assert state.search_results
    assert any("策略矩阵" in log.message for log in state.debug_logs)
