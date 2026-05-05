from __future__ import annotations

import uuid
from pathlib import Path

from app.models.schemas import ItineraryResponse, PlanRequest, PlanSummary, RouteItem
from app.services import agent_graph
from app.services.candidate_discovery import DiscoveryResult
from app.services.agent_state import AgentState
import app.services.candidate_discovery as candidate_discovery_service


def _temp_db_path() -> str:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"agent_state_{uuid.uuid4().hex}.db")


def _sample_request() -> PlanRequest:
    return PlanRequest(
        companion_type="friends",
        available_hours=6,
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
            )
        ],
        tips=[],
    )


def _sample_summary(plan_id: str = "classic_first") -> PlanSummary:
    return PlanSummary(
        plan_id=plan_id,
        variant_label="经典优先",
        stop_count=1,
        clusters=["城墙钟鼓楼簇"],
        is_cross_cluster=False,
        cross_cluster_count=0,
        cluster_transition_summary="城墙钟鼓楼簇",
        has_meal=False,
        total_distance_meters=300,
        total_duration_minutes=8,
        rhythm="均衡",
        budget_level="medium",
        walking_tolerance="medium",
        purpose="tourism",
        diff_points=["簇分布:城墙钟鼓楼簇"],
        bias_tags=["classic"],
        note="test",
    )


def _make_base_pois(count: int = 20):
    items = []
    for idx in range(count):
        items.append(
            {
                "id": f"base_{idx}",
                "name": "钟楼景点" if idx % 2 == 0 else "回民街餐饮",
                "kind": "sight" if idx % 3 else "restaurant",
                "district_cluster": "城墙钟鼓楼簇",
                "category": "landmark" if idx % 2 == 0 else "food",
                "indoor_or_outdoor": "indoor" if idx % 4 == 0 else "outdoor",
                "walking_level": "medium",
            }
        )
    return items


def test_candidate_discovery_called_in_agent_v2_chain(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    monkeypatch.setenv("CANDIDATE_DISCOVERY_ENABLED", "true")

    called = {"count": 0}

    def _fake_discover_candidates(query, context=None, limits=None, filters=None):
        called["count"] += 1
        return DiscoveryResult(
            discovered_pois=[
                {
                    "id": "d_1",
                    "name": "钟楼",
                    "kind": "sight",
                    "district_cluster": "城墙钟鼓楼簇",
                    "category": "landmark",
                    "walking_level": "medium",
                    "indoor_or_outdoor": "outdoor",
                },
                {
                    "id": "d_2",
                    "name": "回民街餐饮",
                    "kind": "restaurant",
                    "district_cluster": "城墙钟鼓楼簇",
                    "category": "food",
                    "walking_level": "low",
                    "indoor_or_outdoor": "indoor",
                },
            ],
            discovery_sources=["unit_test_discovery"],
            discovery_notes=["unit test discovery node"],
            coverage_summary={
                "coverage_ok": True,
                "kind_counts": {"sight": 2, "restaurant": 1},
                "strategies_applied": ["classic", "food"],
            },
        )

    monkeypatch.setattr(agent_graph, "discover_candidates", _fake_discover_candidates)
    monkeypatch.setattr(agent_graph, "load_pois", lambda request_context=None: _make_base_pois())
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
            "reason_tags": ["test"],
        },
    )

    resp = agent_graph.run_agent_v2("朋友一起逛钟楼半天", thread_id=None, user_key=None)
    assert called["count"] >= 1
    assert resp.discovered_pois_count >= 2
    assert "unit_test_discovery" in resp.discovery_sources
    assert any("candidate discovery started" in log.message for log in resp.debug_logs)


def test_dynamic_search_prefers_discovery_results(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    state = AgentState(user_input="test", parsed_request=_sample_request(), thread_id="t-pref")
    state.search_strategy = ["classic"]
    state.discovered_pois = [
        {
            "id": f"disc_{idx}",
            "name": "钟楼",
            "kind": "sight" if idx < 10 else "restaurant",
            "district_cluster": "城墙钟鼓楼簇",
            "category": "landmark",
            "walking_level": "medium",
            "indoor_or_outdoor": "outdoor",
        }
        for idx in range(12)
    ]
    state.discovered_pois_count = len(state.discovered_pois)
    state.discovery_coverage_summary = {
        "coverage_ok": True,
        "kind_counts": {"sight": 10, "restaurant": 2},
        "strategies_applied": ["classic"],
    }

    monkeypatch.setattr(agent_graph, "load_pois", lambda request_context=None: _make_base_pois())
    agent_graph.dynamic_search(state)

    assert state.search_results_count >= 12
    assert state.search_results[0]["id"].startswith("disc_")
    assert any("uses discovery result first" in log.message for log in state.debug_logs)


def test_dynamic_search_falls_back_when_discovery_is_weak(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    state = AgentState(user_input="test", parsed_request=_sample_request(), thread_id="t-fallback")
    state.search_strategy = ["classic"]
    state.discovered_pois = [
        {
            "id": "weak_1",
            "name": "临时点",
            "kind": "sight",
            "district_cluster": "城墙钟鼓楼簇",
            "category": "misc",
            "walking_level": "high",
            "indoor_or_outdoor": "outdoor",
        }
    ]
    state.discovered_pois_count = 1
    state.discovery_coverage_summary = {
        "coverage_ok": False,
        "kind_counts": {"sight": 1, "restaurant": 0},
        "strategies_applied": ["classic"],
    }

    monkeypatch.setattr(agent_graph, "load_pois", lambda request_context=None: _make_base_pois())
    agent_graph.dynamic_search(state)

    assert state.search_results_count > 1
    assert any("fallback to old path" in log.message for log in state.debug_logs)


def test_discover_candidates_uses_multiple_sources(monkeypatch) -> None:
    called: list[str] = []

    monkeypatch.setattr(
        candidate_discovery_service,
        "list_discovery_sources",
        lambda: ["existing_poi_pipeline", "local_extended_corpus"],
    )

    def _fake_load(source_name: str, query: str, context=None):
        called.append(source_name)
        if source_name == "existing_poi_pipeline":
            return [
                {
                    "id": "base_1",
                    "name": "钟楼",
                    "kind": "sight",
                    "district_cluster": "城墙钟鼓楼簇",
                    "category": "landmark",
                    "indoor_or_outdoor": "outdoor",
                    "walking_level": "medium",
                    "latitude": 34.259,
                    "longitude": 108.948,
                }
            ]
        return [
            {
                "id": "ext_1",
                "name": "不夜城艺术装置街",
                "kind": "sight",
                "district_cluster": "曲江夜游簇",
                "category": "night_street",
                "indoor_or_outdoor": "outdoor",
                "walking_level": "medium",
                "latitude": 34.2181,
                "longitude": 108.9739,
            }
        ]

    monkeypatch.setattr(candidate_discovery_service, "load_candidates_from_source", _fake_load)

    result = candidate_discovery_service.discover_candidates(
        query="晚上想逛夜景",
        context={"primary_strategies": ["night"], "secondary_strategies": []},
        limits={"max_candidates": 10},
        filters={},
    )
    assert "existing_poi_pipeline" in called
    assert "local_extended_corpus" in called
    assert result.discovered_source_counts.get("existing_poi_pipeline", 0) == 1
    assert result.discovered_source_counts.get("local_extended_corpus", 0) == 1


def test_agent_state_records_discovered_source_counts(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DB_PATH", _temp_db_path())
    state = AgentState(user_input="test", parsed_request=_sample_request(), thread_id="t-source-counts")
    state.governed_pois = _make_base_pois(12)
    state.data_quality_report = {"quality_fallback": False}
    state.search_strategy = ["classic", "food"]

    monkeypatch.setattr(
        agent_graph,
        "discover_candidates",
        lambda query, context=None, limits=None, filters=None: DiscoveryResult(
            discovered_pois=[
                {
                    "id": "d_1",
                    "name": "钟楼",
                    "kind": "sight",
                    "district_cluster": "城墙钟鼓楼簇",
                    "category": "landmark",
                    "latitude": 34.259,
                    "longitude": 108.948,
                }
            ],
            discovery_sources=["existing_poi_pipeline", "local_extended_corpus"],
            discovered_source_counts={"existing_poi_pipeline": 12, "local_extended_corpus": 8},
            area_scope_used=["城墙钟鼓楼", "回民街", "小寨文博"],
            discovered_area_counts={"城墙钟鼓楼": 6, "回民街": 2, "小寨文博": 1},
            area_coverage_summary={"coverage_ok": True, "active_area_count": 3},
            discovery_notes=["multi source"],
            coverage_summary={"coverage_ok": True, "kind_counts": {"sight": 1, "restaurant": 1}},
        ),
    )

    agent_graph.candidate_discovery(state)
    assert state.discovered_source_counts.get("existing_poi_pipeline") == 12
    assert state.discovered_source_counts.get("local_extended_corpus") == 8
    assert state.area_scope_used == ["城墙钟鼓楼", "回民街", "小寨文博"]
    assert state.discovered_area_counts.get("城墙钟鼓楼") == 6
    assert any("source counts" in log.message for log in state.debug_logs)
    assert any("area scope used" in log.message for log in state.debug_logs)
    assert any("area counts" in log.message for log in state.debug_logs)


def test_discover_candidates_supports_area_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        candidate_discovery_service,
        "list_discovery_sources",
        lambda: ["existing_poi_pipeline", "local_extended_corpus"],
    )

    def _fake_load(source_name: str, query: str, context=None):
        if source_name == "existing_poi_pipeline":
            return [
                {
                    "id": "a_1",
                    "name": "Bell Tower Deck",
                    "kind": "sight",
                    "district_cluster": "城墙钟鼓楼簇",
                    "area_name": "城墙钟鼓楼",
                    "category": "landmark",
                    "indoor_or_outdoor": "outdoor",
                    "walking_level": "medium",
                    "latitude": 34.259,
                    "longitude": 108.948,
                }
            ]
        return [
            {
                "id": "a_2",
                "name": "Hi-Tech Night Block",
                "kind": "sight",
                "district_cluster": "小寨文博簇",
                "area_name": "高新",
                "category": "night_street",
                "indoor_or_outdoor": "outdoor",
                "walking_level": "medium",
                "latitude": 34.232,
                "longitude": 108.890,
            }
        ]

    monkeypatch.setattr(candidate_discovery_service, "load_candidates_from_source", _fake_load)

    request = _sample_request()
    request.origin = "钟楼附近"
    request.origin_preference_mode = "nearby"

    result = candidate_discovery_service.discover_candidates(
        query="我在钟楼附近，晚上想逛逛",
        context={
            "request_context": request,
            "primary_strategies": ["nearby", "night"],
            "secondary_strategies": [],
        },
        limits={"max_candidates": 10},
        filters={},
    )
    assert "城墙钟鼓楼" in result.area_scope_used
    assert result.discovered_area_counts.get("城墙钟鼓楼", 0) >= 1
    assert "area_coverage_summary" in result.coverage_summary


def test_candidate_discovery_records_amap_source_meta(monkeypatch) -> None:
    monkeypatch.setattr(
        candidate_discovery_service,
        "list_discovery_sources",
        lambda: ["existing_poi_pipeline", "amap_web_search"],
    )

    def _fake_load(source_name: str, query: str, context=None):
        if source_name == "existing_poi_pipeline":
            return [
                {
                    "id": "base_1",
                    "name": "Bell Tower",
                    "kind": "sight",
                    "district_cluster": "城墙钟鼓楼簇",
                    "category": "landmark",
                    "latitude": 34.259,
                    "longitude": 108.948,
                    "discovery_primary_source": "existing_poi_pipeline",
                }
            ]
        # emulate amap source meta write-back
        if isinstance(context, dict):
            context.setdefault("source_meta", {}).setdefault("amap_web_search", {}).update(
                {
                    "search_mode": "keyword",
                    "query_count": 2,
                    "mapped_result_count": 1,
                    "fallback_reason": None,
                }
            )
        return [
            {
                "id": "amap_1",
                "name": "Datang Everbright City",
                "kind": "sight",
                "district_cluster": "曲江夜游簇",
                "category": "night_street",
                "latitude": 34.2181,
                "longitude": 108.9739,
                "discovery_primary_source": "amap_web_search",
            }
        ]

    monkeypatch.setattr(candidate_discovery_service, "load_candidates_from_source", _fake_load)

    result = candidate_discovery_service.discover_candidates(
        query="晚上想逛夜景",
        context={"primary_strategies": ["night"], "secondary_strategies": []},
        limits={"max_candidates": 10},
        filters={},
    )
    assert result.discovered_source_counts.get("amap_web_search", 0) == 1
    assert "source_meta" in result.coverage_summary
    source_meta = result.coverage_summary.get("source_meta", {})
    assert isinstance(source_meta, dict)
    assert "amap_web_search" in source_meta

