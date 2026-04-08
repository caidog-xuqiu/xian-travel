from __future__ import annotations

from app.services.eval_ablation import compute_knowledge_gain
from app.services.eval_constraints import evaluate_explanation_consistency
from app.services.eval_metrics import evaluate_agent_cases


def _route_item(
    *,
    time_slot: str,
    item_type: str = "sight",
    name: str = "钟楼",
    cluster: str = "城墙钟鼓楼簇",
    duration: int = 10,
) -> dict:
    return {
        "time_slot": time_slot,
        "type": item_type,
        "name": name,
        "district_cluster": cluster,
        "transport_from_prev": "步行 约10分钟",
        "reason": "顺路",
        "estimated_duration_minutes": duration,
        "estimated_distance_meters": 300,
    }


def _case(
    *,
    case_id: str,
    text: str,
    available_hours: float,
    need_meal: bool = True,
    walking_tolerance: str = "low",
    purpose: str = "relax",
    weather: str = "rainy",
    companion_type: str = "parents",
    route: list[dict] | None = None,
    amap_events: list[dict] | None = None,
    debug_logs: list[dict] | None = None,
    knowledge_used_count: int | None = None,
) -> dict:
    payload = {
        "case_id": case_id,
        "text": text,
        "selected_by": "llm",
        "selected_plan": {
            "summary": "test",
            "route": route or [],
            "tips": [],
        },
        "parsed_request": {
            "companion_type": companion_type,
            "available_hours": available_hours,
            "budget_level": "medium",
            "purpose": purpose,
            "need_meal": need_meal,
            "walking_tolerance": walking_tolerance,
            "weather": weather,
        },
        "amap_called": True,
        "amap_sources_used": ["text_search"],
        "amap_events": amap_events or [],
        "debug_logs": debug_logs or [],
        "candidate_plans_summary": [
            {
                "plan_id": "relaxed_first",
                "stop_count": 2,
                "cross_area_count": 0,
                "has_meal": True,
                "total_duration_minutes": 60,
            }
        ],
    }
    if knowledge_used_count is not None:
        payload["knowledge_used_count"] = knowledge_used_count
    return payload


def test_task_success_rate_computation() -> None:
    ok_case = _case(
        case_id="case_01",
        text="陪父母半天，不想太累",
        available_hours=3.0,
        route=[_route_item(time_slot="10:00-10:40")],
    )
    bad_case = _case(
        case_id="case_02",
        text="空路线",
        available_hours=3.0,
        route=[],
    )

    summary, _details = evaluate_agent_cases([ok_case, bad_case], gold_index={})
    assert summary["task_success_rate"] == 0.5


def test_fallback_rate_by_tool() -> None:
    fallback_case = _case(
        case_id="case_01",
        text="route fallback",
        available_hours=3.0,
        route=[_route_item(time_slot="10:00-10:30")],
        amap_events=[
            {
                "amap_tool": "route",
                "amap_attempted": True,
                "amap_hit": False,
                "amap_fallback_reason": "route_fallback_local",
            }
        ],
    )
    ok_case = _case(
        case_id="case_02",
        text="route ok",
        available_hours=3.0,
        route=[_route_item(time_slot="10:00-10:30")],
        amap_events=[
            {
                "amap_tool": "route",
                "amap_attempted": True,
                "amap_hit": True,
                "amap_fallback_reason": None,
            }
        ],
    )

    summary, _details = evaluate_agent_cases([fallback_case, ok_case], gold_index={})
    assert summary["fallback_rate"]["route_fallback_rate"] == 0.5
    assert summary["fallback_rate"]["overall_fallback_rate"] == 0.5


def test_pairs_warning_rate() -> None:
    warning_case = _case(
        case_id="case_01",
        text="warning",
        available_hours=3.0,
        route=[_route_item(time_slot="10:00-10:30")],
        debug_logs=[{"message": "候选质量告警：方案差异度不足"}],
    )
    normal_case = _case(
        case_id="case_02",
        text="normal",
        available_hours=3.0,
        route=[_route_item(time_slot="10:00-10:30")],
    )

    summary, _details = evaluate_agent_cases([warning_case, normal_case], gold_index={})
    assert summary["pairs_warning_rate"] == 0.5


def test_time_budget_fit_rate() -> None:
    over_case = _case(
        case_id="case_01",
        text="超时",
        available_hours=1.0,
        route=[_route_item(time_slot="10:00-11:30")],
    )
    fit_case = _case(
        case_id="case_02",
        text="不超时",
        available_hours=1.0,
        route=[_route_item(time_slot="10:00-10:40")],
    )

    summary, details = evaluate_agent_cases([over_case, fit_case], gold_index={})
    assert summary["time_budget_fit_rate"] == 0.5
    assert details[0]["time_budget_fit"] is False
    assert details[1]["time_budget_fit"] is True


def test_knowledge_used_rate() -> None:
    with_knowledge = _case(
        case_id="case_01",
        text="有知识",
        available_hours=2.0,
        route=[_route_item(time_slot="10:00-10:30")],
        knowledge_used_count=2,
    )
    without_knowledge = _case(
        case_id="case_02",
        text="无知识",
        available_hours=2.0,
        route=[_route_item(time_slot="10:00-10:30")],
        knowledge_used_count=0,
    )

    summary, _details = evaluate_agent_cases([with_knowledge, without_knowledge], gold_index={})
    assert summary["knowledge_used_rate"] == 0.5


def test_minimal_knowledge_gain_ablation() -> None:
    with_cases = [
        {
            "case_name": "ablation_01",
            "stop_count": 2,
            "cross_area_count": 0,
            "total_duration_minutes": 80,
            "constraint_satisfaction_rate": 0.9,
        }
    ]
    without_cases = [
        {
            "case_name": "ablation_01",
            "stop_count": 3,
            "cross_area_count": 1,
            "total_duration_minutes": 100,
            "constraint_satisfaction_rate": 0.7,
        }
    ]

    result = compute_knowledge_gain(with_knowledge=with_cases, without_knowledge=without_cases)
    assert result["case_count"] == 1
    assert result["averages"]["avg_stop_count_delta"] == -1.0
    assert result["averages"]["avg_cross_area_count_delta"] == -1.0
    assert result["averages"]["avg_total_duration_minutes_delta"] == -20.0
    assert result["averages"]["avg_constraint_satisfaction_rate_delta"] == 0.2


def test_explanation_consistency_rule() -> None:
    route_stats = {
        "stop_count": 2,
        "cross_area_count": 0,
        "meal_count": 0,
    }
    explanation_basis = ["需用餐时餐点为硬锚点：用户明确需要吃饭时，餐饮节点优先保留，不随意裁剪。"]

    result = evaluate_explanation_consistency(route_stats=route_stats, explanation_basis=explanation_basis)
    assert result["explanation_consistent"] is False
    assert result["explanation_consistency_rate"] == 0.0
