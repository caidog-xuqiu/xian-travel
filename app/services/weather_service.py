from __future__ import annotations

import os
from typing import Any, Dict, List

from app.models.schemas import PlanRequest
from app.services.amap_client import load_valid_amap_api_key, weather_query

AMAP_KEY_ENV = "AMAP_API_KEY"
AMAP_CITY_ENV = "AMAP_CITY"
AMAP_WEATHER_CITY_ENV = "AMAP_WEATHER_CITY"
AMAP_DEFAULT_CITY = "610100"  # Xi'an adcode


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_manual_weather_context(request: PlanRequest) -> Dict[str, Any]:
    weather = request.weather.value
    return {
        "source": "fallback_request",
        "weather_condition": weather,
        "temperature_c": None,
        "feels_like_c": None,
        "is_rainy": weather == "rainy",
        "is_hot": weather == "hot",
        "obs_time": None,
    }


def _infer_is_rainy(condition_text: str) -> bool:
    rainy_keywords = ("雨", "雪", "storm", "shower", "rain", "sleet", "drizzle")
    lowered = condition_text.lower()
    return any(keyword in lowered for keyword in rainy_keywords)


def _infer_is_hot(condition_text: str, temperature_c: float | None) -> bool:
    if temperature_c is not None and temperature_c >= 30:
        return True
    lowered = condition_text.lower()
    return any(keyword in lowered for keyword in ("高温", "炎热", "hot", "heat"))


def _resolve_city_for_weather(
    request: PlanRequest,
    candidate_pois: List[Dict[str, Any]] | None,
) -> str:
    """Resolve city/adcode for AMap weather query.

    We keep this intentionally simple for V1:
    - prefer dedicated weather city env
    - then generic AMAP_CITY
    - then Xi'an default adcode
    """
    del request, candidate_pois
    explicit = str(os.getenv(AMAP_WEATHER_CITY_ENV, "")).strip()
    if explicit:
        return explicit
    city = str(os.getenv(AMAP_CITY_ENV, "")).strip()
    if city:
        normalized = city.lower().replace("'", "").replace(" ", "")
        if normalized in {"xian", "xian市", "西安", "西安市"}:
            return AMAP_DEFAULT_CITY
        return city
    return AMAP_DEFAULT_CITY


def _fetch_real_weather_context(api_key: str, city_or_adcode: str) -> Dict[str, Any]:
    context = weather_query(city_or_adcode=city_or_adcode, api_key=api_key)
    condition_text = str(context.get("weather_condition") or "").strip()
    temperature_c = _safe_float(context.get("temperature_c"))
    return {
        "source": "amap_weather",
        "weather_condition": condition_text or "unknown",
        "temperature_c": temperature_c,
        "feels_like_c": _safe_float(context.get("feels_like_c")),
        "is_rainy": bool(context.get("is_rainy")) or _infer_is_rainy(condition_text),
        "is_hot": bool(context.get("is_hot")) or _infer_is_hot(condition_text, temperature_c),
        "obs_time": context.get("obs_time"),
    }


def get_weather_context(
    request: PlanRequest,
    candidate_pois: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Get weather context with graceful fallback.

    Policy:
    1) valid AMAP_API_KEY + call success -> amap_weather context
    2) key missing/invalid -> request.weather fallback
    3) key present but call failed -> request.weather fallback
    """
    api_key, _ = load_valid_amap_api_key(AMAP_KEY_ENV)
    if not api_key:
        return _to_manual_weather_context(request)

    try:
        city_or_adcode = _resolve_city_for_weather(request, candidate_pois)
        return _fetch_real_weather_context(api_key, city_or_adcode)
    except Exception:  # noqa: BLE001
        return _to_manual_weather_context(request)
