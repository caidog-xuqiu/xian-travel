from __future__ import annotations

from app.models.schemas import PlanRequest
from app.services.area_registry import (
    get_default_area_scope,
    list_supported_areas,
    map_place_to_area,
    resolve_area_scope_from_request,
)


def _sample_request(origin: str = "钟楼附近", nearby: bool = True) -> PlanRequest:
    return PlanRequest(
        companion_type="friends",
        available_hours=4,
        budget_level="medium",
        purpose="tourism",
        need_meal=True,
        walking_tolerance="medium",
        weather="sunny",
        origin=origin,
        origin_preference_mode="nearby" if nearby else None,
    )


def test_list_supported_areas_contains_required_entries() -> None:
    names = {item["area_name"] for item in list_supported_areas()}
    required = {
        "城墙钟鼓楼",
        "小寨文博",
        "大雁塔",
        "曲江夜游",
        "回民街",
        "高新",
        "电视塔会展",
        "浐灞未央",
    }
    assert required.issubset(names)
    assert len(get_default_area_scope()) >= len(required)


def test_resolve_area_scope_prioritizes_origin_when_nearby() -> None:
    request = _sample_request(origin="钟楼附近", nearby=True)
    scope = resolve_area_scope_from_request(request, "我在钟楼附近，晚上出去")
    assert scope["origin_area"] == "城墙钟鼓楼"
    assert scope["areas"][0] == "城墙钟鼓楼"
    assert "nearby_mode" in scope["resolved_from"]


def test_map_place_to_area_from_cluster_and_name() -> None:
    poi = {
        "name": "回民街夜游小吃线",
        "district_cluster": "城墙钟鼓楼簇",
    }
    assert map_place_to_area(poi) == "回民街"

    poi2 = {"name": "高新商务街", "district_cluster": "小寨文博簇"}
    # Name alias has higher priority than cluster fallback.
    assert map_place_to_area(poi2) == "高新"

