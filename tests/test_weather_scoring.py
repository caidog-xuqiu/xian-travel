from __future__ import annotations

from app.models.schemas import PlanRequest
from app.services.scoring import score_poi


def _build_request(weather: str = "sunny", preferred_period: str | None = None, need_meal: bool = False) -> PlanRequest:
    return PlanRequest(
        companion_type="solo",
        available_hours=6,
        budget_level="medium",
        purpose="tourism",
        need_meal=need_meal,
        walking_tolerance="medium",
        weather=weather,
        origin="钟楼",
        preferred_period=preferred_period,
    )


def _build_poi(
    *,
    indoor_or_outdoor: str,
    estimated_visit_minutes: int = 60,
    tags: list[str] | None = None,
    kind: str = "sight",
) -> dict:
    return {
        "id": "poi-1",
        "name": "测试点位",
        "kind": kind,
        "district_cluster": "城墙钟鼓楼簇",
        "category": "landmark",
        "indoor_or_outdoor": indoor_or_outdoor,
        "parent_friendly": False,
        "friend_friendly": False,
        "couple_friendly": False,
        "cost_level": "medium",
        "walking_level": "medium",
        "estimated_visit_minutes": estimated_visit_minutes,
        "tags": tags or [],
        "poi_source": "amap",
        "inferred_fields": [],
    }


def test_rainy_weather_prefers_indoor_over_outdoor() -> None:
    request = _build_request(weather="sunny")
    weather_context = {"weather_condition": "小雨", "is_rainy": True, "is_hot": False}

    indoor_score = score_poi(_build_poi(indoor_or_outdoor="indoor"), request, weather_context=weather_context)
    outdoor_score = score_poi(_build_poi(indoor_or_outdoor="outdoor"), request, weather_context=weather_context)

    assert indoor_score > outdoor_score


def test_hot_weather_penalizes_long_outdoor_more_than_indoor() -> None:
    request = _build_request(weather="sunny")
    weather_context = {"weather_condition": "晴", "is_rainy": False, "is_hot": True}

    indoor_score = score_poi(_build_poi(indoor_or_outdoor="indoor", estimated_visit_minutes=60), request, weather_context)
    long_outdoor_score = score_poi(
        _build_poi(indoor_or_outdoor="outdoor", estimated_visit_minutes=120),
        request,
        weather_context,
    )

    assert indoor_score > long_outdoor_score


def test_severe_weather_applies_night_view_penalty() -> None:
    request = _build_request(weather="sunny")
    weather_context = {"weather_condition": "阵雨", "is_rainy": True, "is_hot": False}

    normal_score = score_poi(_build_poi(indoor_or_outdoor="indoor", tags=["classic"]), request, weather_context)
    night_view_score = score_poi(
        _build_poi(indoor_or_outdoor="indoor", tags=["classic", "night_view"]),
        request,
        weather_context,
    )

    assert night_view_score < normal_score


def test_scoring_prefers_real_weather_context_over_request_weather() -> None:
    request = _build_request(weather="rainy")
    real_weather_context = {"weather_condition": "晴", "is_rainy": False, "is_hot": False}

    indoor_score_real = score_poi(
        _build_poi(indoor_or_outdoor="indoor"),
        request,
        weather_context=real_weather_context,
    )
    outdoor_score_real = score_poi(
        _build_poi(indoor_or_outdoor="outdoor"),
        request,
        weather_context=real_weather_context,
    )

    # Real context says no rain/hot, so weather should not create indoor-outdoor gap.
    assert indoor_score_real == outdoor_score_real

    # Without real context, request.weather=rainy should trigger indoor preference.
    indoor_score_fallback = score_poi(_build_poi(indoor_or_outdoor="indoor"), request, weather_context=None)
    outdoor_score_fallback = score_poi(_build_poi(indoor_or_outdoor="outdoor"), request, weather_context=None)
    assert indoor_score_fallback > outdoor_score_fallback


def test_evening_period_prefers_night_view() -> None:
    request = _build_request(preferred_period="evening")
    night_score = score_poi(
        _build_poi(indoor_or_outdoor="outdoor", tags=["night_view", "classic"]),
        request,
        weather_context=None,
    )
    classic_score = score_poi(
        _build_poi(indoor_or_outdoor="outdoor", tags=["classic"]),
        request,
        weather_context=None,
    )
    assert night_score > classic_score


def test_morning_period_downweights_night_view() -> None:
    request = _build_request(preferred_period="morning")
    night_score = score_poi(
        _build_poi(indoor_or_outdoor="outdoor", tags=["night_view"]),
        request,
        weather_context=None,
    )
    classic_score = score_poi(
        _build_poi(indoor_or_outdoor="outdoor", tags=["classic"]),
        request,
        weather_context=None,
    )
    assert classic_score > night_score


def test_midday_period_boosts_restaurant_when_need_meal() -> None:
    request = _build_request(preferred_period="midday", need_meal=True)
    restaurant_score = score_poi(
        _build_poi(indoor_or_outdoor="indoor", tags=["food"], kind="restaurant"),
        request,
        weather_context=None,
    )
    sight_score = score_poi(
        _build_poi(indoor_or_outdoor="indoor", tags=["classic"], kind="sight"),
        request,
        weather_context=None,
    )
    assert restaurant_score > sight_score
