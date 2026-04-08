from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.services import evaluation_harness


class _FakeRequest:
    def model_dump(self):
        return {
            "parsed_by": "llm",
            "need_meal": True,
            "preferred_period": "evening",
            "purpose": "dating",
            "origin_preference_mode": "nearby",
            "origin": "钟楼",
            "walking_tolerance": "low",
            "companion_type": "partner",
            "weather": "rainy",
        }


class _FakeV3Response:
    def model_dump(self):
        return {
            "parsed_request": {
                "parsed_by": "llm",
                "need_meal": True,
                "preferred_period": "evening",
                "purpose": "dating",
                "origin_preference_mode": "nearby",
                "origin": "钟楼",
                "walking_tolerance": "low",
                "companion_type": "partner",
                "weather": "rainy",
            },
            "selected_by": "llm",
            "clarification_needed": False,
            "amap_called": True,
            "amap_sources_used": ["amap_web_search"],
            "amap_fallback_reason": "",
            "route_source": "amap",
            "weather_source": "amap_weather",
            "area_scope_used": ["城墙钟鼓楼", "曲江夜游", "大雁塔"],
            "discovered_area_counts": {"城墙钟鼓楼": 5, "曲江夜游": 4},
            "data_quality_report": {
                "total_input": 20,
                "total_after_dedup": 15,
                "quarantined_count": 3,
                "issue_counts": {"duplicate": 5, "missing_field": 3},
            },
            "candidate_plans_summary": [
                {
                    "variant_label": "classic_first",
                    "clusters": ["曲江夜游簇"],
                    "cross_cluster_count": 0,
                    "has_meal": True,
                    "rhythm": "轻松",
                    "knowledge_tags": ["晚间氛围更强"],
                },
                {
                    "variant_label": "food_friendly",
                    "clusters": ["曲江夜游簇", "大雁塔簇"],
                    "cross_cluster_count": 1,
                    "has_meal": True,
                    "rhythm": "均衡",
                    "knowledge_tags": ["适合拍照打卡"],
                },
            ],
            "selected_plan": {
                "route": [
                    {
                        "type": "sight",
                        "name": "大唐不夜城",
                        "district_cluster": "曲江夜游簇",
                        "reason": "室内友好",
                    },
                    {
                        "type": "restaurant",
                        "name": "曲江餐饮",
                        "district_cluster": "曲江夜游簇",
                        "reason": "顺路用餐",
                    },
                ]
            },
            "debug_logs": [
                {"message": "Planning step=1 invalid action fallback -> SEARCH"},
                {"message": "planning ok"},
            ],
        }


def test_load_eval_cases() -> None:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    file_path = base / f"eval_cases_{uuid.uuid4().hex}.json"
    file_path.write_text(
        json.dumps(
            [
                {"case_id": "c1", "text": "陪父母半天", "expected_focus": ["meal", "relax"]},
                {"text": "晚上约会看夜景", "expected_focus": "night"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cases = evaluation_harness.load_eval_cases(file_path)
    assert len(cases) == 2
    assert cases[0]["case_id"] == "c1"
    assert cases[1]["case_id"].startswith("case_")
    assert cases[1]["expected_focus"] == ["night"]


def test_run_eval_for_endpoint_produces_summary_and_details(monkeypatch) -> None:
    monkeypatch.setattr(evaluation_harness, "parse_free_text_to_plan_request", lambda text: _FakeRequest())
    monkeypatch.setattr(
        evaluation_harness,
        "select_best_plan",
        lambda request: {
            "selected_by": "llm",
            "alternative_plans_summary": [
                {
                    "variant_label": "classic_first",
                    "clusters": ["城墙钟鼓楼簇"],
                    "cross_cluster_count": 0,
                    "has_meal": True,
                    "rhythm": "轻松",
                    "knowledge_tags": ["文博密度高"],
                }
            ],
            "selected_plan": {
                "route": [
                    {
                        "type": "restaurant",
                        "name": "回民街餐饮",
                        "district_cluster": "城墙钟鼓楼簇",
                        "reason": "室内友好",
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(evaluation_harness, "run_agent_v3", lambda **kwargs: _FakeV3Response())

    cases = [
        {"case_id": "c1", "text": "和对象晚上出去，想吃饭、拍照、逛夜景", "expected_focus": ["night", "meal"]},
        {"case_id": "c2", "text": "我在钟楼附近，只有3小时，想轻松一点", "expected_focus": ["nearby", "relax"]},
    ]

    v2 = evaluation_harness.run_eval_for_endpoint("v2", cases, options={})
    v3 = evaluation_harness.run_eval_for_endpoint("v3", cases, options={})

    assert v2["summary"]["total_cases"] == 2
    assert v3["summary"]["total_cases"] == 2
    assert "candidate_quality_signal" in v3["summary"]
    assert "amap_usage_rate" in v3["summary"]
    assert "amap_search_hit_rate" in v3["summary"]
    assert "amap_route_hit_rate" in v3["summary"]
    assert "amap_weather_hit_rate" in v3["summary"]
    assert "discovery_source_coverage" in v3["summary"]
    assert "area_coverage_signal" in v3["summary"]
    assert "cross_area_signal" in v3["summary"]
    assert "area_fit_hit_rate" in v3["summary"]
    assert len(v3["details"]) == 2
    assert v3["skill_trace"]
    assert any(item.get("skill_name") == "evaluation_skill" for item in v3["skill_trace"])
    assert any("evaluation_skill" in log.get("message", "") for log in v3.get("debug_logs", []))
    for detail in v3["details"]:
        assert "case_id" in detail
        assert "parsed_by" in detail
        assert "selected_by" in detail
        assert "candidate_quality_signal" in detail
        assert "amap_called" in detail
        assert "amap_sources_used" in detail
        assert "route_source" in detail
        assert "weather_source" in detail
        assert "amap_fallback_reason" in detail
        assert "discovery_source_coverage" in detail
        assert "area_coverage_signal" in detail
        assert "area_scope_used" in detail
        assert "discovered_area_counts" in detail
        assert "cross_area_count" in detail
        assert "area_transition_summary" in detail
        assert "area_fit_hit" in detail
        assert "short_result_label" in detail


def test_compare_eval_results_outputs_delta() -> None:
    v2_results = {
        "details": [
            {
                "case_id": "c1",
                "text": "test",
                "parsed_by": "rule",
                "selected_by": "fallback_rule",
                "clarification_needed": False,
                "invalid_action_fallback": 2,
                "candidate_count": 1,
                "candidate_diversity_score": 0.3,
                "candidate_quality_signal": 1.0,
                "amap_called": False,
                "amap_search_hit": False,
                "amap_route_hit": False,
                "amap_weather_hit": False,
                "route_quality_hit": False,
                "meal_intent_hit": False,
                "night_intent_hit": False,
                "nearby_intent_hit": False,
                "relax_intent_hit": False,
            }
        ]
    }
    v3_results = {
        "details": [
            {
                "case_id": "c1",
                "text": "test",
                "parsed_by": "llm",
                "selected_by": "llm",
                "clarification_needed": False,
                "invalid_action_fallback": 0,
                "candidate_count": 3,
                "candidate_diversity_score": 0.9,
                "candidate_quality_signal": 0.95,
                "amap_called": True,
                "amap_search_hit": True,
                "amap_route_hit": True,
                "amap_weather_hit": True,
                "route_quality_hit": True,
                "meal_intent_hit": True,
                "night_intent_hit": True,
                "nearby_intent_hit": True,
                "relax_intent_hit": True,
            }
        ]
    }
    compare = evaluation_harness.compare_eval_results(v2_results=v2_results, v3_results=v3_results, v4_results=v3_results)

    assert "v2_vs_v3" in compare
    assert "parsed_by_llm_rate_delta" in compare["v2_vs_v3"]
    assert "candidate_quality_signal_delta" in compare["v2_vs_v3"]
    assert "amap_usage_rate_delta" in compare["v2_vs_v3"]
    assert "amap_search_hit_rate_delta" in compare["v2_vs_v3"]
    assert "amap_route_hit_rate_delta" in compare["v2_vs_v3"]
    assert "amap_weather_hit_rate_delta" in compare["v2_vs_v3"]
    assert "discovery_source_coverage_delta" in compare["v2_vs_v3"]
    assert "area_coverage_signal_delta" in compare["v2_vs_v3"]
    assert "cross_area_signal_delta" in compare["v2_vs_v3"]
    assert "area_fit_hit_rate_delta" in compare["v2_vs_v3"]
    assert "v3_vs_v4" in compare


def test_v4_current_uses_independent_agent_chain(monkeypatch) -> None:
    calls = {"v3": 0, "v4": 0}

    def _fake_v3(**kwargs):
        calls["v3"] += 1
        return _FakeV3Response()

    def _fake_v4(**kwargs):
        calls["v4"] += 1
        return _FakeV3Response()

    monkeypatch.setattr(evaluation_harness, "run_agent_v3", _fake_v3)
    monkeypatch.setattr(evaluation_harness, "run_agent_v4_current", _fake_v4)

    cases = [{"case_id": "c1", "text": "下午想轻松一点", "expected_focus": ["relax"]}]
    _ = evaluation_harness.run_eval_for_endpoint("v4_current", cases, options={})

    assert calls["v4"] == 1
    assert calls["v3"] == 0
