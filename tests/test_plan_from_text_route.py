from __future__ import annotations

from app.models.schemas import TextPlanRequest
from app.routes.plan import plan_trip_from_text, plan_trip_from_text_readable


def test_plan_from_text_returns_parsed_request(monkeypatch) -> None:
    monkeypatch.delenv("AMAP_API_KEY", raising=False)
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)

    payload = TextPlanRequest(
        text="\u966a\u7236\u6bcd\u534a\u5929\uff0c\u4e0d\u60f3\u8d70\u592a\u591a\uff0c\u60f3\u5403\u996d\uff0c\u4ece\u949f\u697c\u51fa\u53d1"
    )
    response = plan_trip_from_text(payload)

    assert response.parsed_request.companion_type.value == "parents"
    assert response.parsed_request.available_hours == 4.0
    assert response.parsed_request.origin == "\u949f\u697c"
    assert isinstance(response.itinerary.route, list)


def test_night_partner_case_has_evening_signal_and_non_empty_route(monkeypatch) -> None:
    monkeypatch.delenv("AMAP_API_KEY", raising=False)
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)

    payload = TextPlanRequest(
        text="\u548c\u5bf9\u8c61\u665a\u4e0a\u51fa\u53bb\uff0c\u60f3\u5403\u996d\u3001\u62cd\u7167\u3001\u901b\u591c\u666f"
    )
    response = plan_trip_from_text_readable(payload)

    assert response.parsed_request.companion_type.value == "partner"
    assert response.parsed_request.purpose.value == "dating"
    assert response.parsed_request.preferred_period == "evening"
    assert len(response.itinerary.route) >= 2
    assert any(item.type == "restaurant" for item in response.itinerary.route)


def test_nearby_origin_prefers_nearby_first_stop(monkeypatch) -> None:
    monkeypatch.delenv("AMAP_API_KEY", raising=False)
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)

    payload = TextPlanRequest(
        text="\u6211\u5728\u949f\u697c\u9644\u8fd1\uff0c\u53ea\u67093\u5c0f\u65f6\uff0c\u60f3\u8f7b\u677e\u4e00\u70b9"
    )
    response = plan_trip_from_text_readable(payload)

    assert response.parsed_request.origin == "\u949f\u697c"
    assert response.parsed_request.origin_preference_mode == "nearby"
    assert len(response.itinerary.route) > 0
    assert response.itinerary.route[0].district_cluster == "\u57ce\u5899\u949f\u9f13\u697c\u7c07"
