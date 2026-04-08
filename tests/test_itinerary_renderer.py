from __future__ import annotations

from app.models.schemas import ItineraryResponse, PlanRequest, RouteItem, TextPlanRequest
from app.routes import plan as plan_routes
from app.services.itinerary_renderer import render_itinerary_text


def _sample_request() -> PlanRequest:
    return PlanRequest(
        companion_type="parents",
        available_hours=4,
        budget_level="medium",
        purpose="tourism",
        need_meal=True,
        walking_tolerance="low",
        weather="rainy",
        origin="钟楼",
    )


def _sample_itinerary() -> ItineraryResponse:
    return ItineraryResponse(
        summary="测试摘要",
        route=[
            RouteItem(
                time_slot="09:00-10:00",
                type="sight",
                name="陕西历史博物馆",
                district_cluster="小寨文博簇",
                transport_from_prev="从起点前往（地铁/打车 约12分钟）",
                reason="室内友好，同簇连续，减少折返",
                estimated_distance_meters=1200,
                estimated_duration_minutes=12,
            ),
            RouteItem(
                time_slot="10:20-11:20",
                type="restaurant",
                name="小寨餐馆",
                district_cluster="小寨文博簇",
                transport_from_prev="步行 约8分钟",
                reason="位于顺路簇内，减少绕行",
                estimated_distance_meters=800,
                estimated_duration_minutes=8,
            ),
        ],
        tips=[
            "天气来源：请求参数 weather 兜底；当前天气：rainy。",
            "今日有雨，已优先室内点位并适度下调夜游点。",
            "时间预算 240 分钟，当前已排入 200 分钟。",
        ],
    )


def test_renderer_generates_all_readable_sections() -> None:
    readable = render_itinerary_text(_sample_itinerary(), request=_sample_request())

    assert {"title", "overview", "schedule_text", "transport_text", "tips_text"} <= set(readable.keys())
    assert readable["title"]
    assert readable["overview"]
    assert readable["schedule_text"]
    assert readable["transport_text"]
    assert readable["tips_text"]


def test_schedule_text_contains_times_and_places() -> None:
    readable = render_itinerary_text(_sample_itinerary(), request=_sample_request())
    assert "09:00-10:00" in readable["schedule_text"]
    assert "陕西历史博物馆" in readable["schedule_text"]
    assert "10:20-11:20" in readable["schedule_text"]
    assert "小寨餐馆" in readable["schedule_text"]


def test_transport_text_contains_total_distance_and_duration() -> None:
    readable = render_itinerary_text(_sample_itinerary(), request=_sample_request())
    assert "公里" in readable["transport_text"]
    assert "交通耗时约" in readable["transport_text"]


def test_plan_readable_route_returns_readable_output(monkeypatch) -> None:
    sample_itinerary = _sample_itinerary()
    monkeypatch.setattr(plan_routes, "generate_itinerary", lambda request: sample_itinerary)

    response = plan_routes.plan_trip_readable(_sample_request())
    assert response.readable_output.title
    assert "schedule_text" in response.readable_output.model_dump()


def test_plan_from_text_readable_route_returns_readable_output(monkeypatch) -> None:
    sample_itinerary = _sample_itinerary()
    monkeypatch.setattr(plan_routes, "generate_itinerary", lambda request: sample_itinerary)
    monkeypatch.setattr(plan_routes, "parse_free_text_to_plan_request", lambda text: _sample_request())

    response = plan_routes.plan_trip_from_text_readable(TextPlanRequest(text="陪父母半天，从钟楼出发"))
    assert response.parsed_request.companion_type.value == "parents"
    assert response.readable_output.title
    assert "overview" in response.readable_output.model_dump()


def test_empty_route_copy_is_not_budget_misleading() -> None:
    empty_itinerary = ItineraryResponse(
        summary="空路线测试",
        route=[],
        tips=["当前路线未触发时间裁剪。"],
    )
    readable = render_itinerary_text(empty_itinerary, request=_sample_request())

    assert "预算较紧" not in readable["schedule_text"]
    assert "营业时间" in readable["schedule_text"] or "时段" in readable["schedule_text"]


def test_morning_copy_mentions_period() -> None:
    request = _sample_request().model_copy(update={"preferred_period": "morning"})
    readable = render_itinerary_text(_sample_itinerary(), request=request)
    assert "上午" in readable["overview"] or "上午" in readable["tips_text"]
    assert "09:00" in readable["overview"] or "09:00" in readable["tips_text"]
    assert "时段说明" in readable["schedule_text"]


def test_midday_copy_mentions_meal() -> None:
    request = _sample_request().model_copy(update={"preferred_period": "midday", "need_meal": True})
    readable = render_itinerary_text(_sample_itinerary(), request=request)
    assert "午间" in readable["overview"] or "午间" in readable["tips_text"]
    assert "正餐" in readable["overview"] or "正餐" in readable["tips_text"]
    assert "11:30" in readable["overview"] or "11:30" in readable["tips_text"]


def test_afternoon_copy_mentions_relaxed_rhythm() -> None:
    request = _sample_request().model_copy(update={"preferred_period": "afternoon"})
    readable = render_itinerary_text(_sample_itinerary(), request=request)
    assert "午后" in readable["overview"] or "午后" in readable["tips_text"]
    assert "14:00" in readable["overview"] or "14:00" in readable["tips_text"]


def test_evening_copy_mentions_night_tour() -> None:
    request = _sample_request().model_copy(update={"preferred_period": "evening"})
    readable = render_itinerary_text(_sample_itinerary(), request=request)
    assert "晚间" in readable["overview"] or "晚间" in readable["tips_text"]
    assert "18:00" in readable["overview"] or "18:00" in readable["tips_text"]


def test_evening_empty_route_copy_mentions_limited_candidates() -> None:
    evening_request = _sample_request().model_copy(update={"preferred_period": "evening"})
    empty_itinerary = ItineraryResponse(summary="夜游空路线", route=[], tips=[])
    readable = render_itinerary_text(empty_itinerary, request=evening_request)
    assert "晚间" in readable["overview"] or "晚间" in readable["schedule_text"]
    assert "18:00" in readable["overview"] or "18:00" in readable["tips_text"]


def test_evening_single_stop_copy_explains_limited_candidates() -> None:
    evening_request = PlanRequest(
        companion_type="partner",
        available_hours=4,
        budget_level="medium",
        purpose="dating",
        need_meal=True,
        walking_tolerance="medium",
        weather="sunny",
        origin="钟楼",
        preferred_period="evening",
    )
    one_stop_itinerary = ItineraryResponse(
        summary="夜游单站测试",
        route=[
            RouteItem(
                time_slot="18:30-19:50",
                type="sight",
                name="大唐不夜城",
                district_cluster="曲江夜游簇",
                transport_from_prev="从起点前往（地铁/打车（估算）约30分钟）",
                reason="更有氛围感，同簇连续，减少折返",
                estimated_distance_meters=6200,
                estimated_duration_minutes=30,
            )
        ],
        tips=["检测到夜游偏好，已按晚间起步（18:00）并优先尝试夜游簇点位。"],
    )

    readable = render_itinerary_text(one_stop_itinerary, request=evening_request)
    assert "候选" in readable["overview"] or "候选" in readable["schedule_text"]


def test_evening_with_meal_mentions_night_meal_link() -> None:
    evening_request = PlanRequest(
        companion_type="partner",
        available_hours=4,
        budget_level="medium",
        purpose="dating",
        need_meal=True,
        walking_tolerance="medium",
        weather="sunny",
        origin="钟楼",
        preferred_period="evening",
    )

    readable = render_itinerary_text(_sample_itinerary(), request=evening_request)
    assert "晚间" in readable["overview"] or "晚间" in readable["schedule_text"]
    assert "晚餐" in readable["overview"] or "晚餐" in readable["schedule_text"]


def test_overview_and_tips_not_identical() -> None:
    request = _sample_request().model_copy(update={"preferred_period": "afternoon"})
    readable = render_itinerary_text(_sample_itinerary(), request=request)
    assert readable["overview"] != readable["tips_text"]


def test_title_not_repeating_area_terms() -> None:
    readable = render_itinerary_text(_sample_itinerary(), request=_sample_request())
    assert "西安市区西安" not in readable["title"]


def test_schedule_text_period_hint_only_once() -> None:
    request = _sample_request().model_copy(update={"preferred_period": "morning"})
    readable = render_itinerary_text(_sample_itinerary(), request=request)
    assert readable["schedule_text"].count("时段说明") <= 1
