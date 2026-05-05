from __future__ import annotations

from app.models.schemas import ItineraryResponse, PlanRequest, RouteItem
from app.services import plan_selector


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


def _sample_itinerary(name_suffix: str) -> ItineraryResponse:
    return ItineraryResponse(
        summary="test",
        route=[
            RouteItem(
                time_slot="09:00-10:00",
                type="sight",
                name=f"点位{name_suffix}",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行 约5分钟",
                reason="同簇连续，减少折返",
                estimated_distance_meters=500,
                estimated_duration_minutes=5,
            )
        ],
        tips=[],
    )


def test_generate_candidate_plans_at_least_two(monkeypatch) -> None:
    counter = {"i": 0}

    def _fake_generate(_: PlanRequest, candidate_pois=None) -> ItineraryResponse:
        counter["i"] += 1
        return _sample_itinerary(str(counter["i"]))

    monkeypatch.setattr(plan_selector, "generate_itinerary", _fake_generate)

    candidates = plan_selector.generate_candidate_plans(_sample_request())
    assert len(candidates) >= 2


def test_llm_selects_plan_id(monkeypatch) -> None:
    monkeypatch.setattr(plan_selector, "generate_itinerary", lambda *_, **__: _sample_itinerary("A"))

    def _fake_llm(*_, **__) -> dict:
        return {"selected_plan_id": "relaxed_first", "selection_reason": "偏轻松"}

    monkeypatch.setattr(plan_selector, "select_plan_with_llm", _fake_llm)
    result = plan_selector.select_best_plan(_sample_request())

    assert result["selected_by"] == "llm"
    assert result["selection_reason"]


def test_llm_invalid_plan_id_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(plan_selector, "generate_itinerary", lambda *_, **__: _sample_itinerary("A"))

    def _fake_llm(*_, **__) -> dict:
        return {"selected_plan_id": "unknown", "selection_reason": "not valid"}

    monkeypatch.setattr(plan_selector, "select_plan_with_llm", _fake_llm)
    result = plan_selector.select_best_plan(_sample_request())

    assert result["selected_by"] == "fallback_rule"
    assert "回退" in result["selection_reason"]


def test_plan_summary_includes_budget_and_walk() -> None:
    request = _sample_request().model_copy(update={"budget_level": "low", "walking_tolerance": "low"})
    summary = plan_selector._summarize_plan(
        "classic_first",
        _sample_itinerary("S"),
        request,
        "note",
    )
    assert summary.variant_label
    assert summary.budget_level == "low"
    assert summary.walking_tolerance == "low"
    assert summary.diff_points
    assert summary.bias_tags
    assert summary.is_cross_cluster is False
    assert summary.cross_cluster_count == 0
    assert summary.cluster_transition_summary


def test_dedup_keeps_single_candidate(monkeypatch) -> None:
    def _fake_generate(_: PlanRequest, candidate_pois=None) -> ItineraryResponse:
        return _sample_itinerary("X")

    monkeypatch.setattr(plan_selector, "generate_itinerary", _fake_generate)
    candidates = plan_selector.generate_candidate_plans(_sample_request())
    assert len(candidates) >= 2
    assert len({item["plan_id"] for item in candidates}) >= 2


def test_empty_candidate_sorted_last(monkeypatch) -> None:
    def _fake_generate(request: PlanRequest, candidate_pois=None) -> ItineraryResponse:
        purpose_value = request.purpose.value if hasattr(request.purpose, "value") else str(request.purpose)
        if purpose_value == "relax":
            return ItineraryResponse(summary="empty", route=[], tips=[])
        return _sample_itinerary("Y")

    monkeypatch.setattr(plan_selector, "generate_itinerary", _fake_generate)
    candidates = plan_selector.generate_candidate_plans(_sample_request())
    assert candidates
    assert candidates[0]["summary"].stop_count > 0


def test_cross_cluster_metrics(monkeypatch) -> None:
    def _fake_generate(request: PlanRequest, candidate_pois=None) -> ItineraryResponse:
        return ItineraryResponse(
            summary="x",
            route=[
                RouteItem(
                    time_slot="09:00-10:00",
                    type="sight",
                    name="A",
                    district_cluster="城墙钟鼓楼簇",
                    transport_from_prev="步行",
                    reason="test",
                ),
                RouteItem(
                    time_slot="10:30-11:30",
                    type="sight",
                    name="B",
                    district_cluster="小寨文博簇",
                    transport_from_prev="地铁",
                    reason="test",
                ),
            ],
            tips=[],
        )

    monkeypatch.setattr(plan_selector, "generate_itinerary", _fake_generate)
    candidates = plan_selector.generate_candidate_plans(_sample_request())
    summary = candidates[0]["summary"]
    assert summary.is_cross_cluster is True
    assert summary.cross_cluster_count > 0
    assert "->" in summary.cluster_transition_summary


def test_relaxed_variant_reduces_hours_for_long_day() -> None:
    request = _sample_request().model_copy(update={"available_hours": 8})
    variant = plan_selector._build_request_variant(request, "relaxed_first")
    assert variant.available_hours == 3.8
    walking = variant.walking_tolerance.value if hasattr(variant.walking_tolerance, "value") else str(variant.walking_tolerance)
    assert walking == "low"


def test_food_variant_candidate_pois_prefers_restaurant_clusters() -> None:
    request = _sample_request()
    candidate_pois = [
        {"id": "r1", "name": "A1", "kind": "restaurant", "district_cluster": "城墙钟鼓楼簇"},
        {"id": "r2", "name": "A2", "kind": "restaurant", "district_cluster": "城墙钟鼓楼簇"},
        {"id": "r3", "name": "A3", "kind": "restaurant", "district_cluster": "城墙钟鼓楼簇"},
        {"id": "r4", "name": "B1", "kind": "restaurant", "district_cluster": "小寨文博簇"},
        {"id": "r5", "name": "B2", "kind": "restaurant", "district_cluster": "小寨文博簇"},
        {"id": "s1", "name": "SA1", "kind": "sight", "district_cluster": "城墙钟鼓楼簇"},
        {"id": "s2", "name": "SA2", "kind": "sight", "district_cluster": "城墙钟鼓楼簇"},
        {"id": "s3", "name": "SB1", "kind": "sight", "district_cluster": "小寨文博簇"},
        {"id": "s4", "name": "SC1", "kind": "sight", "district_cluster": "大雁塔簇"},
        {"id": "s5", "name": "SC2", "kind": "sight", "district_cluster": "大雁塔簇"},
    ]
    biased = plan_selector._variant_candidate_pois(request, "food_friendly", candidate_pois)
    assert biased is not None
    assert len([p for p in biased if p.get("kind") == "restaurant"]) >= 3
    assert not any(
        p.get("kind") == "sight" and p.get("district_cluster") == "大雁塔簇"
        for p in biased
    )


def test_candidate_diversity_retry_or_flag(monkeypatch) -> None:
    def _fake_generate(request: PlanRequest, candidate_pois=None) -> ItineraryResponse:
        purpose_value = request.purpose.value if hasattr(request.purpose, "value") else str(request.purpose)
        # food 方案在候选扩展后产出不同序列，便于验证重试有机会拉开差异
        if purpose_value == "food" and candidate_pois and len(candidate_pois) >= 10:
            route = [
                RouteItem(
                    time_slot="11:30-12:30",
                    type="restaurant",
                    name="餐饮优先站",
                    district_cluster="城墙钟鼓楼簇",
                    transport_from_prev="步行",
                    reason="餐饮衔接",
                ),
                RouteItem(
                    time_slot="12:40-13:30",
                    type="sight",
                    name="景点补充站",
                    district_cluster="城墙钟鼓楼簇",
                    transport_from_prev="步行",
                    reason="顺路补点",
                ),
            ]
            return ItineraryResponse(summary="food", route=route, tips=[])
        return _sample_itinerary("same")

    monkeypatch.setattr(plan_selector, "generate_itinerary", _fake_generate)
    quality: dict = {}
    candidates = plan_selector.generate_candidate_plans(
        _sample_request(),
        candidate_pois=[
            {"id": "r1", "name": "A1", "kind": "restaurant", "district_cluster": "城墙钟鼓楼簇"},
            {"id": "r2", "name": "A2", "kind": "restaurant", "district_cluster": "城墙钟鼓楼簇"},
            {"id": "r3", "name": "A3", "kind": "restaurant", "district_cluster": "城墙钟鼓楼簇"},
            {"id": "r4", "name": "B1", "kind": "restaurant", "district_cluster": "小寨文博簇"},
            {"id": "r5", "name": "B2", "kind": "restaurant", "district_cluster": "小寨文博簇"},
            {"id": "s1", "name": "SA1", "kind": "sight", "district_cluster": "城墙钟鼓楼簇"},
            {"id": "s2", "name": "SA2", "kind": "sight", "district_cluster": "城墙钟鼓楼簇"},
            {"id": "s3", "name": "SB1", "kind": "sight", "district_cluster": "小寨文博簇"},
            {"id": "s4", "name": "SC1", "kind": "sight", "district_cluster": "大雁塔簇"},
            {"id": "s5", "name": "SC2", "kind": "sight", "district_cluster": "大雁塔簇"},
        ],
        quality_feedback=quality,
    )
    assert candidates
    assert quality.get("diversity_retry_count", 0) >= 1 or quality.get("diversity_insufficient") is True


def test_food_summary_contains_meal_position() -> None:
    request = _sample_request()
    itinerary = ItineraryResponse(
        summary="food",
        route=[
            RouteItem(
                time_slot="11:30-12:30",
                type="restaurant",
                name="餐饮A",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="餐饮优先",
            ),
            RouteItem(
                time_slot="12:40-13:20",
                type="sight",
                name="景点A",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="顺路",
            ),
        ],
        tips=[],
    )
    summary = plan_selector._summarize_plan("food_friendly", itinerary, request, "餐饮友好")
    assert any(point.startswith("餐位:") for point in summary.diff_points)
    assert "meal_position" in summary.bias_tags


def test_relaxed_variant_prefers_less_cross_cluster(monkeypatch) -> None:
    def _fake_generate(request: PlanRequest, candidate_pois=None) -> ItineraryResponse:
        walking = request.walking_tolerance.value if hasattr(request.walking_tolerance, "value") else str(request.walking_tolerance)
        if walking == "low":
            route = [
                RouteItem(
                    time_slot="09:00-10:00",
                    type="sight",
                    name="A",
                    district_cluster="城墙钟鼓楼簇",
                    transport_from_prev="步行",
                    reason="test",
                ),
                RouteItem(
                    time_slot="10:10-11:00",
                    type="restaurant",
                    name="B",
                    district_cluster="城墙钟鼓楼簇",
                    transport_from_prev="步行",
                    reason="test",
                ),
            ]
            return ItineraryResponse(summary="relaxed", route=route, tips=[])

        route = [
            RouteItem(
                time_slot="09:00-10:00",
                type="sight",
                name="A",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="test",
            ),
            RouteItem(
                time_slot="10:30-11:30",
                type="sight",
                name="C",
                district_cluster="小寨文博簇",
                transport_from_prev="地铁",
                reason="test",
            ),
        ]
        return ItineraryResponse(summary="classic", route=route, tips=[])

    monkeypatch.setattr(plan_selector, "generate_itinerary", _fake_generate)
    candidates = plan_selector.generate_candidate_plans(_sample_request())
    by_id = {item["plan_id"]: item["summary"] for item in candidates}
    assert "classic_first" in by_id and "relaxed_first" in by_id
    assert by_id["relaxed_first"].cross_cluster_count <= by_id["classic_first"].cross_cluster_count


def test_classic_variant_keeps_core_points(monkeypatch) -> None:
    def _fake_generate(request: PlanRequest, candidate_pois=None) -> ItineraryResponse:
        purpose = request.purpose.value if hasattr(request.purpose, "value") else str(request.purpose)
        if purpose == "tourism":
            route = [
                RouteItem(
                    time_slot="09:00-10:00",
                    type="sight",
                    name="钟楼",
                    district_cluster="城墙钟鼓楼簇",
                    transport_from_prev="步行",
                    reason="地标",
                ),
                RouteItem(
                    time_slot="10:20-11:20",
                    type="sight",
                    name="城墙南门",
                    district_cluster="城墙钟鼓楼簇",
                    transport_from_prev="步行",
                    reason="经典",
                ),
                RouteItem(
                    time_slot="11:30-12:20",
                    type="restaurant",
                    name="回民街餐饮",
                    district_cluster="城墙钟鼓楼簇",
                    transport_from_prev="步行",
                    reason="顺路",
                ),
            ]
            return ItineraryResponse(summary="classic", route=route, tips=[])
        return _sample_itinerary("R")

    monkeypatch.setattr(plan_selector, "generate_itinerary", _fake_generate)
    candidates = plan_selector.generate_candidate_plans(_sample_request())
    by_id = {item["plan_id"]: item for item in candidates}
    assert "classic_first" in by_id
    assert by_id["classic_first"]["summary"].stop_count >= 3
    assert "classic" in by_id["classic_first"]["summary"].bias_tags


def test_plan_summary_contains_area_metrics() -> None:
    request = _sample_request()
    itinerary = ItineraryResponse(
        summary="area",
        route=[
            RouteItem(
                time_slot="09:00-10:00",
                type="sight",
                name="钟楼",
                district_cluster="城墙钟鼓楼簇",
                transport_from_prev="步行",
                reason="经典地标",
            ),
            RouteItem(
                time_slot="10:20-11:10",
                type="sight",
                name="高新商务区街景",
                district_cluster="小寨文博簇",
                transport_from_prev="打车",
                reason="区域扩展",
            ),
        ],
        tips=[],
    )
    summary = plan_selector._summarize_plan(
        "classic_first",
        itinerary,
        request,
        "区域覆盖测试",
        area_context={"area_priority_order": ["城墙钟鼓楼", "高新"], "area_scope_used": ["城墙钟鼓楼", "高新"]},
    )
    assert summary.is_cross_area is True
    assert summary.cross_area_count >= 1
    assert "->" in summary.area_transition_summary
    assert summary.area_bias_note


def test_area_aware_candidate_generation_separates_variants(monkeypatch) -> None:
    def _fake_generate(request: PlanRequest, candidate_pois=None) -> ItineraryResponse:
        purpose = request.purpose.value if hasattr(request.purpose, "value") else str(request.purpose)
        walking = request.walking_tolerance.value if hasattr(request.walking_tolerance, "value") else str(request.walking_tolerance)
        if purpose == "food":
            return ItineraryResponse(
                summary="food",
                route=[
                    RouteItem(
                        time_slot="11:30-12:20",
                        type="restaurant",
                        name="回民街小吃",
                        district_cluster="城墙钟鼓楼簇",
                        transport_from_prev="步行",
                        reason="餐饮优先",
                    ),
                    RouteItem(
                        time_slot="12:30-13:20",
                        type="sight",
                        name="钟楼",
                        district_cluster="城墙钟鼓楼簇",
                        transport_from_prev="步行",
                        reason="顺路补点",
                    ),
                ],
                tips=[],
            )
        if walking == "low":
            return ItineraryResponse(
                summary="relaxed",
                route=[
                    RouteItem(
                        time_slot="09:00-10:00",
                        type="sight",
                        name="小寨漫步点",
                        district_cluster="小寨文博簇",
                        transport_from_prev="步行",
                        reason="轻松节奏",
                    )
                ],
                tips=[],
            )
        return ItineraryResponse(
            summary="classic",
            route=[
                RouteItem(
                    time_slot="09:00-10:00",
                    type="sight",
                    name="钟楼",
                    district_cluster="城墙钟鼓楼簇",
                    transport_from_prev="步行",
                    reason="经典覆盖",
                ),
                RouteItem(
                    time_slot="10:40-11:30",
                    type="sight",
                    name="大雁塔",
                    district_cluster="大雁塔簇",
                    transport_from_prev="地铁",
                    reason="跨区域经典补充",
                ),
            ],
            tips=[],
        )

    monkeypatch.setattr(plan_selector, "generate_itinerary", _fake_generate)
    candidates = plan_selector.generate_candidate_plans(
        _sample_request(),
        area_context={
            "area_priority_order": ["城墙钟鼓楼", "大雁塔", "小寨文博"],
            "area_scope_used": ["城墙钟鼓楼", "大雁塔", "小寨文博"],
        },
    )
    by_id = {item["plan_id"]: item["summary"] for item in candidates}
    assert by_id["classic_first"].cross_area_count >= 1
    assert by_id["relaxed_first"].cross_area_count <= by_id["classic_first"].cross_area_count
    assert by_id["food_friendly"].has_meal is True
    assert any(point.startswith("餐位:") for point in by_id["food_friendly"].diff_points)


def test_relaxed_variant_prefers_park_sights_when_demand_tag_present() -> None:
    request = _sample_request().model_copy(update={"walking_tolerance": "low"})
    candidate_pois = [
        {
            "id": "s_park",
            "name": "曲江池公园",
            "kind": "sight",
            "district_cluster": "曲江夜游簇",
            "area_name": "曲江夜游",
            "category": "park",
            "walking_level": "low",
            "_score": 80,
        },
        {
            "id": "s_old",
            "name": "钟楼",
            "kind": "sight",
            "district_cluster": "城墙钟鼓楼簇",
            "area_name": "城墙钟鼓楼",
            "category": "landmark",
            "walking_level": "low",
            "_score": 90,
        },
        {
            "id": "r1",
            "name": "曲江餐厅",
            "kind": "restaurant",
            "district_cluster": "曲江夜游簇",
            "area_name": "曲江夜游",
            "walking_level": "low",
            "_score": 70,
        },
    ]

    biased = plan_selector._variant_candidate_pois(
        request,
        "relaxed_first",
        candidate_pois,
        area_context={"search_strategy": ["park", "relaxed"], "demand_tags": ["park"]},
    )
    assert biased is not None
    sights = [item for item in biased if item.get("kind") == "sight"]
    assert sights
    assert any("公园" in str(item.get("name")) for item in sights)


def test_park_demand_repairs_restaurant_only_route(monkeypatch) -> None:
    request = _sample_request().model_copy(update={"purpose": "relax", "walking_tolerance": "low"})
    candidate_pois = [
        {
            "id": "s_park",
            "name": "曲江池公园",
            "kind": "sight",
            "district_cluster": "曲江夜游簇",
            "area_name": "曲江夜游",
            "category": "park",
            "walking_level": "low",
            "_score": 80,
        },
        {
            "id": "r1",
            "name": "文博蔬食面坊",
            "kind": "restaurant",
            "district_cluster": "小寨文博簇",
            "area_name": "小寨文博",
            "walking_level": "low",
            "_score": 70,
        },
    ]

    def _fake_generate(_: PlanRequest, candidate_pois=None) -> ItineraryResponse:
        return ItineraryResponse(
            summary="restaurant only",
            route=[
                RouteItem(
                    time_slot="14:46-15:46",
                    type="restaurant",
                    name="文博蔬食面坊",
                    district_cluster="小寨文博簇",
                    transport_from_prev="地铁/打车 约46分钟",
                    reason="位于顺路簇内，减少绕行",
                )
            ],
            tips=[],
        )

    monkeypatch.setattr(plan_selector, "generate_itinerary", _fake_generate)

    item = plan_selector._build_candidate_item(
        request,
        "relaxed_first",
        "轻松优先",
        candidate_pois,
        area_context={"search_strategy": ["park", "relaxed"], "demand_tags": ["park"]},
        knowledge_bias={"prefer_park_scene": True},
    )

    route = item["itinerary"].route
    assert any(stop.type == "sight" and "公园" in stop.name for stop in route)
    assert any(stop.type == "restaurant" for stop in route)
    assert len(route) <= 2
