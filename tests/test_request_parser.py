from __future__ import annotations

import app.services.request_parser as request_parser
from app.services.request_parser import parse_free_text_to_plan_request


def test_parse_parents_half_day_low_walk_and_meal() -> None:
    text = (
        "\u966a\u7236\u6bcd\u534a\u5929\u901b\u901b\uff0c"
        "\u4e0d\u60f3\u8d70\u592a\u591a\uff0c"
        "\u4e2d\u5348\u60f3\u5403\u996d\uff0c"
        "\u4ece\u949f\u697c\u51fa\u53d1"
    )
    req = parse_free_text_to_plan_request(text)

    assert req.companion_type.value == "parents"
    assert req.available_hours == 4
    assert req.walking_tolerance.value == "low"
    assert req.need_meal is True
    assert req.origin == "\u949f\u697c"


def test_parse_friends_full_day_and_low_budget() -> None:
    text = "\u548c\u670b\u53cb\u5168\u5929\u73a9\uff0c\u7a77\u6e38\uff0c\u4ece\u5c0f\u5be8\u51fa\u53d1"
    req = parse_free_text_to_plan_request(text)

    assert req.companion_type.value == "friends"
    assert req.available_hours == 8
    assert req.budget_level.value == "low"
    assert req.origin == "\u5c0f\u5be8"


def test_parse_partner_dating_evening_and_meal() -> None:
    text = "\u548c\u5bf9\u8c61\u7ea6\u4f1a\uff0c\u665a\u4e0a\u60f3\u5403\u996d\uff0c\u4ece\u5927\u96c1\u5854\u51fa\u53d1"
    req = parse_free_text_to_plan_request(text)

    assert req.companion_type.value == "partner"
    assert req.purpose.value == "dating"
    assert req.need_meal is True
    assert req.preferred_period == "evening"
    assert req.available_hours == 4
    assert req.origin == "\u5927\u96c1\u5854"


def test_parse_defaults_when_missing_fields() -> None:
    req = parse_free_text_to_plan_request("\u5468\u672b\u60f3\u5728\u897f\u5b89\u8f6c\u8f6c")

    assert req.companion_type.value == "solo"
    assert req.available_hours == 4
    assert req.budget_level.value == "medium"
    assert req.purpose.value == "tourism"
    assert req.need_meal is True
    assert req.walking_tolerance.value == "medium"
    assert req.weather.value == "sunny"
    assert req.origin in {"\u949f\u697c", "\u897f\u5b89"}


def test_parse_weather_hot_word_noise_not_misclassified() -> None:
    req = parse_free_text_to_plan_request("\u60f3\u53bb\u70ed\u95f9\u4e00\u70b9\u7684\u5730\u65b9\uff0c\u548c\u670b\u53cb\u51fa\u53bb\u73a9")
    assert req.weather.value == "sunny"


def test_parse_low_budget_phrase() -> None:
    req = parse_free_text_to_plan_request("\u548c\u670b\u53cb\u4e00\u5929\uff0c\u9884\u7b97\u4f4e\u4e00\u70b9")
    assert req.budget_level.value == "low"


def test_parse_low_walk_phrase() -> None:
    req = parse_free_text_to_plan_request("\u966a\u7236\u6bcd\u51fa\u53bb\uff0c\u4e0d\u60f3\u592a\u7d2f")
    assert req.walking_tolerance.value == "low"


def test_parse_meal_phrase_does_not_overwrite_purpose() -> None:
    req = parse_free_text_to_plan_request(
        "\u966a\u7236\u6bcd\u534a\u5929\uff0c\u4e2d\u5348\u60f3\u5403\u996d\uff0c\u522b\u592a\u7d2f"
    )
    assert req.need_meal is True
    assert req.purpose.value != "food"
    assert req.walking_tolerance.value == "low"


def test_parse_friends_with_meal_as_secondary_need() -> None:
    req = parse_free_text_to_plan_request(
        "\u548c\u670b\u53cb\u4e00\u5929\uff0c\u60f3\u591a\u901b\u51e0\u4e2a\u70ed\u95f9\u7684\u5730\u65b9\uff0c\u987a\u4fbf\u5403\u996d"
    )
    assert req.need_meal is True
    assert req.purpose.value != "food"


def test_parse_nearby_origin_signal() -> None:
    req = parse_free_text_to_plan_request(
        "\u6211\u5728\u949f\u697c\u9644\u8fd1\uff0c\u53ea\u67093\u5c0f\u65f6\uff0c\u60f3\u8f7b\u677e\u4e00\u70b9"
    )
    assert req.origin == "\u949f\u697c"
    assert req.origin_preference_mode == "nearby"


def test_parse_morning_period() -> None:
    req = parse_free_text_to_plan_request("\u4e0a\u5348\u966a\u7236\u6bcd\u901b\u4e00\u901b\uff0c\u4e0d\u60f3\u592a\u7d2f")
    assert req.preferred_period == "morning"


def test_parse_midday_period_and_meal() -> None:
    req = parse_free_text_to_plan_request("\u4e2d\u5348\u524d\u540e\u51fa\u53bb\u5403\u70b9\u4e1c\u897f\u518d\u901b\u901b")
    assert req.preferred_period == "midday"
    assert req.need_meal is True


def test_parse_afternoon_period() -> None:
    req = parse_free_text_to_plan_request("\u4e0b\u5348\u60f3\u8f7b\u677e\u4e00\u70b9\uff0c\u901b\u4e24\u4e2a\u5730\u65b9\u5c31\u884c")
    assert req.preferred_period == "afternoon"


def test_parse_park_intent_as_relax_purpose() -> None:
    req = parse_free_text_to_plan_request("\u60f3\u53bb\u516c\u56ed\u6563\u6b65\uff0c\u987a\u4fbf\u5403\u70e7\u70e4")
    assert req.purpose.value == "relax"


def test_parse_multiple_periods_prefers_later() -> None:
    req = parse_free_text_to_plan_request("\u4e0a\u5348\u60f3\u901b\uff0c\u665a\u4e0a\u60f3\u5403\u996d")
    assert req.preferred_period == "evening"


def test_parse_midday_and_afternoon_prefers_afternoon() -> None:
    req = parse_free_text_to_plan_request("\u4e2d\u5348\u524d\u540e\u51fa\u53bb\uff0c\u4e0b\u5348\u60f3\u8f7b\u677e\u4e00\u70b9")
    assert req.preferred_period == "afternoon"


def test_parse_origin_keeps_rule_when_geocode_fails(monkeypatch) -> None:
    monkeypatch.setattr(request_parser, "load_valid_amap_api_key", lambda env_name="AMAP_API_KEY": ("A" * 32, None))
    monkeypatch.setattr(request_parser, "geocode_address", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("geo down")))
    req = parse_free_text_to_plan_request("我在钟楼附近，只有3小时，想轻松一点")
    assert req.origin == "钟楼"
    assert req.origin_preference_mode == "nearby"
