from __future__ import annotations

from app.models.schemas import PlanRequest
from app.services import weather_service


def _build_request(weather: str = "sunny") -> PlanRequest:
    return PlanRequest(
        companion_type="solo",
        available_hours=6,
        budget_level="medium",
        purpose="tourism",
        need_meal=True,
        walking_tolerance="medium",
        weather=weather,
        origin="钟楼",
    )


def test_weather_context_fallback_when_key_missing(monkeypatch) -> None:
    monkeypatch.delenv(weather_service.AMAP_KEY_ENV, raising=False)
    request = _build_request(weather="rainy")

    context = weather_service.get_weather_context(request)

    assert context["source"] == "fallback_request"
    assert context["weather_condition"] == "rainy"
    assert context["is_rainy"] is True
    assert context["is_hot"] is False


def test_weather_context_fallback_when_invalid_key(monkeypatch) -> None:
    monkeypatch.setenv(weather_service.AMAP_KEY_ENV, "bad-key")
    request = _build_request(weather="hot")

    context = weather_service.get_weather_context(request)

    assert context["source"] == "fallback_request"
    assert context["weather_condition"] == "hot"
    assert context["is_hot"] is True
    assert context["is_rainy"] is False


def test_weather_context_fallback_when_real_call_fails(monkeypatch) -> None:
    monkeypatch.setenv(weather_service.AMAP_KEY_ENV, "A" * 32)
    request = _build_request(weather="rainy")

    def _raise_fetch(api_key: str, city_or_adcode: str) -> dict:
        raise RuntimeError("weather api failed")

    monkeypatch.setattr(weather_service, "_fetch_real_weather_context", _raise_fetch)

    context = weather_service.get_weather_context(request)

    assert context["source"] == "fallback_request"
    assert context["weather_condition"] == "rainy"
    assert context["is_rainy"] is True
    assert context["is_hot"] is False


def test_weather_context_prefers_real_weather_when_available(monkeypatch) -> None:
    monkeypatch.setenv(weather_service.AMAP_KEY_ENV, "A" * 32)
    request = _build_request(weather="rainy")

    monkeypatch.setattr(
        weather_service,
        "_fetch_real_weather_context",
        lambda api_key, city_or_adcode: {
            "source": "amap_weather",
            "weather_condition": "晴",
            "temperature_c": 31.0,
            "feels_like_c": None,
            "is_rainy": False,
            "is_hot": True,
            "obs_time": "2026-03-31 10:00:00",
        },
    )

    context = weather_service.get_weather_context(request)

    assert context["source"] == "amap_weather"
    assert context["weather_condition"] == "晴"
    assert context["is_rainy"] is False
    assert context["is_hot"] is True
    # request.weather=rainy should not override real weather result.
    assert context["weather_condition"] != request.weather.value

