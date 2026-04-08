from __future__ import annotations

from app.models.schemas import PlanRequest
from app.services import agent_graph
from app.services.agent_state import AgentState
from app.services.candidate_discovery import DiscoveryResult
from app.services.data_quality import govern_candidate_pool


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


def _valid_poi(poi_id: str, name: str, kind: str = "sight") -> dict:
    return {
        "id": poi_id,
        "name": name,
        "kind": kind,
        "district_cluster": "城墙钟鼓楼簇",
        "category": "landmark" if kind == "sight" else "food",
        "latitude": 34.265,
        "longitude": 108.953,
        "poi_source": "mock",
    }


def test_govern_candidate_pool_dedup_and_quarantine() -> None:
    pois = [
        _valid_poi("p1", "钟楼"),
        _valid_poi("p2", "钟楼"),  # duplicate by normalized dedupe key
        {
            "id": "bad1",
            "name": "坏数据",
            "kind": "sight",
            "district_cluster": "城墙钟鼓楼簇",
            "category": "landmark",
            "latitude": None,
            "longitude": 108.953,
            "poi_source": "mock",
        },
    ]
    outcome = govern_candidate_pool(pois)
    assert len(outcome.usable_pois) == 1
    assert len(outcome.quarantined_pois) == 1
    assert outcome.report is not None
    assert outcome.report.total_input == 3
    assert outcome.report.total_after_dedup == 2
    assert outcome.report.quarantined_count == 1
    assert outcome.report.issue_counts.get("duplicate", 0) >= 1


def test_data_quality_called_before_discovery_in_agent_v2(monkeypatch) -> None:
    order: list[str] = []

    monkeypatch.setattr(agent_graph, "parse_free_text_to_plan_request", lambda text: _sample_request())

    def _fake_data_quality(state):
        order.append("data_quality")
        state.governed_pois = [_valid_poi("g1", "钟楼")]
        state.data_quality_report = {
            "total_input": 2,
            "total_after_dedup": 1,
            "quarantined_count": 1,
            "issue_counts": {"missing_field": 1},
            "quality_fallback": False,
        }
        state.quarantined_count = 1
        state.quality_issue_summary = {"missing_field": 1}
        return state

    def _fake_candidate_discovery(state):
        order.append("candidate_discovery")
        state.discovered_pois = list(state.governed_pois)
        state.discovered_pois_count = len(state.discovered_pois)
        state.discovery_coverage_summary = {"coverage_ok": True, "kind_counts": {"sight": 1, "restaurant": 1}}
        return state

    monkeypatch.setattr(agent_graph, "data_quality", _fake_data_quality)
    monkeypatch.setattr(agent_graph, "candidate_discovery", _fake_candidate_discovery)
    monkeypatch.setattr(agent_graph, "dynamic_search", lambda s: s)
    monkeypatch.setattr(agent_graph, "refine_search_results", lambda s: s)
    monkeypatch.setattr(agent_graph, "gather_context", lambda s: s)
    monkeypatch.setattr(agent_graph, "generate_candidates", lambda s: s)
    monkeypatch.setattr(agent_graph, "select_plan", lambda s: s)
    monkeypatch.setattr(agent_graph, "render_output", lambda s: s)
    monkeypatch.setattr(agent_graph, "finalize_memory", lambda s: s)

    resp = agent_graph.run_agent_v2("陪父母半天，中午想吃饭", thread_id="t-order", user_key=None)
    assert order[:2] == ["data_quality", "candidate_discovery"]
    assert resp.data_quality_report.get("total_after_dedup") == 1
    assert resp.quarantined_count == 1


def test_candidate_discovery_consumes_governed_pool(monkeypatch) -> None:
    state = AgentState(parsed_request=_sample_request(), thread_id="t-governed")
    state.governed_pois = [_valid_poi("g1", "钟楼")]
    state.data_quality_report = {"quality_fallback": False}

    captured = {"base_count": 0}

    def _fake_discover(query, context=None, limits=None, filters=None):
        base = list((context or {}).get("base_pois") or [])
        captured["base_count"] = len(base)
        return DiscoveryResult(
            discovered_pois=base,
            discovery_sources=["test_source"],
            discovery_notes=["from governed"],
            coverage_summary={"coverage_ok": True, "kind_counts": {"sight": 1, "restaurant": 1}},
        )

    monkeypatch.setattr(agent_graph, "discover_candidates", _fake_discover)
    monkeypatch.setattr(agent_graph, "load_pois", lambda request_context=None: [_valid_poi("raw1", "原始池")])

    agent_graph.candidate_discovery(state)
    assert captured["base_count"] == 1
    assert state.discovered_pois[0]["id"] == "g1"


def test_candidate_discovery_quality_fallback_logs(monkeypatch) -> None:
    state = AgentState(parsed_request=_sample_request(), thread_id="t-fallback")
    state.governed_pois = [_valid_poi("g1", "钟楼")]
    state.data_quality_report = {"quality_fallback": True, "fallback_reason": "usable_pool_too_small"}

    raw_pool = [_valid_poi("raw1", "钟楼"), _valid_poi("raw2", "鼓楼"), _valid_poi("raw3", "回民街", kind="restaurant")]

    monkeypatch.setattr(agent_graph, "load_pois", lambda request_context=None: raw_pool)
    monkeypatch.setattr(
        agent_graph,
        "discover_candidates",
        lambda query, context=None, limits=None, filters=None: DiscoveryResult(
            discovered_pois=list((context or {}).get("base_pois") or []),
            discovery_sources=["raw_fallback"],
            discovery_notes=["fallback used"],
            coverage_summary={"coverage_ok": True, "kind_counts": {"sight": 2, "restaurant": 1}},
        ),
    )

    agent_graph.candidate_discovery(state)
    assert len(state.discovered_pois) == len(raw_pool)
    assert any("quality fallback happened" in log.message for log in state.debug_logs)


def test_data_quality_node_updates_agent_state(monkeypatch) -> None:
    state = AgentState(parsed_request=_sample_request(), thread_id="t-quality")
    monkeypatch.setattr(
        agent_graph,
        "load_pois",
        lambda request_context=None: [
            _valid_poi("p1", "钟楼"),
            _valid_poi("p2", "钟楼"),
            {
                "id": "bad",
                "name": "坏数据",
                "kind": "sight",
                "district_cluster": "城墙钟鼓楼簇",
                "category": "landmark",
                "latitude": None,
                "longitude": 108.95,
            },
        ],
    )

    agent_graph.data_quality(state)
    assert state.data_quality_report
    assert state.quarantined_count >= 1
    assert state.quality_issue_summary
    assert any("data_quality totals" in log.message for log in state.debug_logs)
