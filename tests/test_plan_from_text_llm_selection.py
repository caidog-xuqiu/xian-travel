from __future__ import annotations

from app.models.schemas import PlanRequest, TextPlanRequest
from app.routes import plan as plan_routes


def _sample_request() -> PlanRequest:
    return PlanRequest(
        companion_type="partner",
        available_hours=4,
        budget_level="medium",
        purpose="dating",
        need_meal=True,
        walking_tolerance="medium",
        weather="sunny",
        origin="钟楼",
    )


def test_plan_from_text_with_llm_selection_returns_payload(monkeypatch) -> None:
    monkeypatch.setattr(plan_routes, "parse_free_text_to_plan_request", lambda text: _sample_request())
    monkeypatch.setattr(
        plan_routes,
        "select_best_plan",
        lambda request: {
            "selected_plan": {"summary": "s", "route": [], "tips": []},
            "alternative_plans_summary": [],
            "selection_reason": "test",
            "reason_tags": ["默认回退"],
            "selected_by": "fallback_rule",
            "readable_output": {
                "title": "t",
                "overview": "o",
                "schedule_text": "s",
                "transport_text": "t",
                "tips_text": "t",
            },
        },
    )

    response = plan_routes.plan_from_text_with_llm_selection(
        TextPlanRequest(text="和对象晚上出去，想吃饭、拍照、逛夜景")
    )
    assert response.parsed_request.companion_type.value == "partner"
    assert response.selected_by == "fallback_rule"
    assert response.selection_reason
    assert response.readable_output.title


def test_plan_from_text_with_llm_selection_accepts_llm_choice(monkeypatch) -> None:
    monkeypatch.setattr(plan_routes, "parse_free_text_to_plan_request", lambda text: _sample_request())
    monkeypatch.setattr(
        plan_routes,
        "select_best_plan",
        lambda request: {
            "selected_plan": {"summary": "s", "route": [], "tips": []},
            "alternative_plans_summary": [],
            "selection_reason": "llm picked",
            "reason_tags": ["更顺路含餐"],
            "selected_by": "llm",
            "readable_output": {
                "title": "t",
                "overview": "o",
                "schedule_text": "s",
                "transport_text": "t",
                "tips_text": "t",
            },
        },
    )

    response = plan_routes.plan_from_text_with_llm_selection(
        TextPlanRequest(text="和对象晚上出去，想吃饭、拍照、逛夜景")
    )
    assert response.selected_by == "llm"
    assert response.selection_reason == "llm picked"
    assert response.reason_tags
