from __future__ import annotations

import uuid
from pathlib import Path

from app.models.schemas import PlanRequest
from app.services import agent_graph, discovery_sources, search_planner


def _temp_db_path() -> str:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"agent_state_{uuid.uuid4().hex}.db")


def _request(**overrides) -> dict:
    payload = {
        "companion_type": "solo",
        "available_hours": 4,
        "budget_level": "medium",
        "purpose": "relax",
        "need_meal": True,
        "walking_tolerance": "low",
        "weather": "sunny",
        "origin": "西安电子科技大学长安校区",
    }
    payload.update(overrides)
    return PlanRequest(**payload).model_dump()


def test_build_search_plan_handles_park_bbq_with_origin(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan(
        "我在西安电子科技大学长安校区，想去市中心逛公园，再去吃烧烤，早上九点出发",
        _request(),
    )

    assert plan["clarification_needed"] is False
    assert "park" in plan["primary_intents"]
    assert "bbq" in plan["primary_intents"]
    assert len(plan["search_rounds"]) >= 2
    assert plan["search_rounds"][0]["tool"] == "keyword_search"
    assert plan["search_rounds"][1]["tool"] == "nearby_search"
    assert plan["search_rounds"][1]["anchor_from_round"] == 1
    assert plan["search_rounds"][1]["anchor_top_k"] >= 1
    assert plan["search_rounds"][1]["radius_meters"] >= 300
    assert "公园" in search_planner.flatten_search_queries(plan)
    assert "烧烤" in search_planner.flatten_search_queries(plan)


def test_build_search_plan_asks_search_related_clarification_when_origin_missing(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan("想去公园再吃烧烤", _request(origin="钟楼"))

    assert plan["clarification_needed"] is True
    assert "起点" in (plan["clarification_question"] or "") or "市中心" in (plan["clarification_question"] or "")
    assert len(plan["clarification_options"]) >= 2


def test_rerank_search_results_prefers_park_for_park_intent(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan("我想逛公园", _request())
    candidates = [
        {"id": "clock", "name": "钟楼", "kind": "sight", "category": "landmark", "_score": 99},
        {"id": "park", "name": "曲江池公园", "kind": "sight", "category": "park", "_score": 50},
    ]

    reranked = search_planner.rerank_search_results(candidates, _request(), plan)

    assert reranked[0]["name"] == "曲江池公园"


def test_run_search_round_nearby_adds_anchor_metadata(monkeypatch) -> None:
    monkeypatch.setattr(discovery_sources, "load_valid_amap_api_key", lambda *_: (None, "missing_api_key"))
    anchor = {
        "id": "park",
        "name": "曲江池公园",
        "kind": "sight",
        "category": "park",
        "latitude": 34.215,
        "longitude": 108.976,
    }
    nearby = {
        "id": "bbq",
        "name": "曲江烧烤店",
        "kind": "restaurant",
        "category": "烧烤",
        "latitude": 34.216,
        "longitude": 108.977,
    }

    output = discovery_sources.run_search_round(
        {"tool": "nearby_search", "queries": ["烧烤"], "radius_meters": 2000, "max_results": 5},
        {"base_pois": [anchor, nearby]},
        around_poi=anchor,
        round_index=2,
    )

    assert output["results"]
    hit = output["results"][0]
    assert hit["anchor_poi_id"] == "park"
    assert hit["anchor_poi_name"] == "曲江池公园"
    assert hit["derived_round"] == 2
    assert isinstance(hit["distance_to_anchor_m"], int)


def test_execute_search_plan_runs_second_round_around_anchor(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setattr(discovery_sources, "load_valid_amap_api_key", lambda *_: (None, "missing_api_key"))
    request = PlanRequest(**_request())
    anchor = {
        "id": "park",
        "name": "曲江池公园",
        "kind": "sight",
        "category": "park",
        "latitude": 34.215,
        "longitude": 108.976,
        "walking_level": "low",
    }
    bbq = {
        "id": "bbq",
        "name": "曲江烧烤店",
        "kind": "restaurant",
        "category": "烧烤",
        "latitude": 34.216,
        "longitude": 108.977,
        "walking_level": "low",
    }
    plan = search_planner.build_search_plan(
        "我在西安电子科技大学长安校区，想去市中心逛公园，再去吃烧烤，早上九点出发",
        request.model_dump(),
    )
    state = agent_graph.AgentState(
        user_input="我在西安电子科技大学长安校区，想去市中心逛公园，再去吃烧烤，早上九点出发",
        parsed_request=request,
        governed_pois=[anchor, bbq],
        search_plan_used=plan,
        search_plan=plan,
        search_strategy=["park", "food"],
        primary_strategies=["park", "food"],
        final_search_queries=search_planner.flatten_search_queries(plan),
        thread_id="search-plan-round-test",
    )

    agent_graph.execute_search_plan(state)

    assert state.first_round_candidates
    assert state.anchor_candidates
    assert state.second_round_grouped_results
    assert any((item.get("tool") == "nearby_search") and int(item.get("result_count") or 0) >= 1 for item in state.search_round_outputs)


def test_intent_detect_park_walk(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan("我想去公园散步", _request())

    assert "park" in plan["primary_intents"]


def test_intent_detect_bbq(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan("想吃烧烤", _request())

    assert "bbq" in plan["primary_intents"]


def test_intent_detect_night_view(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan("晚上夜游", _request())

    assert "night_view" in plan["primary_intents"]


def test_intent_detect_parents_low_walk(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan("带父母少走路", _request(companion_type="parents", need_meal=False))

    assert "low_walk" in plan["primary_intents"]
    assert "parents" in plan["primary_intents"] or "family" in plan["primary_intents"]


def test_intent_detect_rain_indoor(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan("下雨天室内一点", _request(weather="rainy", need_meal=False))

    assert "rain" in plan["primary_intents"]
    assert any("室内" in q for q in search_planner.flatten_search_queries(plan))


def test_intent_detect_family(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan("想做亲子路线，带娃轻松逛", _request(need_meal=False))

    assert "family" in plan["primary_intents"]


def test_intent_detect_couple(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan("和对象约会拍照", _request(companion_type="partner"))

    assert "couple" in plan["primary_intents"]


def test_intent_detect_budget(monkeypatch) -> None:
    monkeypatch.setenv("LLM_SEARCH_PLANNER_ENABLED", "0")
    plan = search_planner.build_search_plan("预算低一点，想要性价比", _request(budget_level="low", need_meal=False))

    assert "budget" in plan["primary_intents"]
