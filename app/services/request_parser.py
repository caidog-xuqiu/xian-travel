from __future__ import annotations

import re
from typing import Iterable

from app.models.schemas import PlanRequest
from app.services.amap_client import geocode_address, load_valid_amap_api_key, reverse_geocode
from app.services.llm_parser import parse_free_text_with_llm_debug

# V1 free-text parser defaults (conservative).
DEFAULT_COMPANION_TYPE = "solo"
DEFAULT_AVAILABLE_HOURS = 4.0
DEFAULT_BUDGET_LEVEL = "medium"
DEFAULT_PURPOSE = "tourism"
DEFAULT_NEED_MEAL = True
DEFAULT_WALKING_TOLERANCE = "medium"
DEFAULT_WEATHER = "sunny"
DEFAULT_ORIGIN = "\u949f\u697c"

PARENTS_KEYWORDS = {
    "\u966a\u7236\u6bcd",
    "\u548c\u7238\u5988",
    "\u5e26\u8001\u4eba",
    "\u966a\u8001\u4eba",
    "\u7236\u6bcd",
    "\u7238\u5988",
    "\u957f\u8f88",
}
FRIENDS_KEYWORDS = {
    "\u548c\u670b\u53cb",
    "\u966a\u670b\u53cb",
    "\u670b\u53cb",
}
PARTNER_KEYWORDS = {
    "\u548c\u5973\u670b\u53cb",
    "\u548c\u7537\u670b\u53cb",
    "\u548c\u5bf9\u8c61",
    "\u5bf9\u8c61",
    "\u60c5\u4fa3",
    "\u7ea6\u4f1a",
}
SOLO_KEYWORDS = {
    "\u4e00\u4e2a\u4eba",
    "\u72ec\u81ea",
    "\u81ea\u5df1\u53bb",
    "\u81ea\u5df1",
}

LOW_BUDGET_KEYWORDS = {
    "\u7a77\u6e38",
    "\u4fbf\u5b9c",
    "\u4fbf\u5b9c\u4e00\u70b9",
    "\u7701\u94b1",
    "\u7701\u4e00\u70b9",
    "\u4f4e\u9884\u7b97",
    "\u9884\u7b97\u4f4e\u4e00\u70b9",
    "\u9884\u7b97\u5c11\u4e00\u70b9",
}
MEDIUM_BUDGET_KEYWORDS = {
    "\u4e2d\u7b49",
    "\u9002\u4e2d",
    "\u4e00\u822c\u9884\u7b97",
}
HIGH_BUDGET_KEYWORDS = {
    "\u8c6a\u534e",
    "\u9ad8\u9884\u7b97",
    "\u4e0d\u5dee\u94b1",
}

TOURISM_KEYWORDS = {
    "\u65c5\u6e38",
    "\u901b\u666f\u70b9",
    "\u666f\u70b9",
    "\u6253\u5361",
}
RELAX_KEYWORDS = {
    "\u653e\u677e",
    "\u6563\u5fc3",
    "\u8f7b\u677e",
    "\u4f11\u95f2",
}
FOOD_KEYWORDS = {
    "\u5403\u996d",
    "\u7f8e\u98df",
    "\u5c0f\u5403",
    "\u5403\u70b9\u4e1c\u897f",
}
STRONG_FOOD_PURPOSE_KEYWORDS = {
    "\u60f3\u5403\u597d\u5403\u7684",
    "\u4e13\u95e8\u5403\u996d",
    "\u60f3\u53bb\u5403\u5c0f\u5403",
    "\u7f8e\u98df\u8def\u7ebf",
    "\u5403\u5403\u559d\u559d",
    "\u7f8e\u98df\u6253\u5361",
    "\u4e13\u95e8\u53bb\u5403",
}
DATING_KEYWORDS = {
    "\u7ea6\u4f1a",
    "\u60c5\u4fa3",
    "\u6d6a\u6f2b",
}

NO_MEAL_KEYWORDS = {
    "\u4e0d\u5403\u996d",
    "\u4e0d\u5b89\u6392\u5403\u996d",
    "\u4e0d\u9700\u8981\u5403\u996d",
    "\u4e0d\u7528\u5403\u996d",
}
YES_MEAL_KEYWORDS = {
    "\u60f3\u5403\u996d",
    "\u4e2d\u5348\u5403\u996d",
    "\u4e2d\u5348\u60f3\u5403\u996d",
    "\u665a\u4e0a\u5403\u996d",
    "\u665a\u4e0a\u60f3\u5403\u996d",
    "\u5403\u70b9\u4e1c\u897f",
    "\u987a\u4fbf\u5403\u70b9\u4e1c\u897f",
    "\u987a\u4fbf\u5403\u996d",
    "\u5b89\u6392\u5403\u996d",
    "\u5403\u996d",
}

LOW_WALK_KEYWORDS = {
    "\u4e0d\u60f3\u8d70\u592a\u591a",
    "\u4e0d\u60f3\u592a\u7d2f",
    "\u522b\u592a\u7d2f",
    "\u8f7b\u677e\u4e00\u70b9",
    "\u8d70\u5c11\u4e00\u70b9",
    "\u5c11\u8d70\u8def",
}
MEDIUM_WALK_KEYWORDS = {
    "\u6b63\u5e38",
    "\u4e00\u822c",
}
HIGH_WALK_KEYWORDS = {
    "\u80fd\u591a\u8d70",
    "\u66b4\u8d70",
    "\u8d70\u8def\u6ca1\u95ee\u9898",
}

RAINY_KEYWORDS = {
    "\u4e0b\u96e8",
    "\u96e8\u5929",
    "rain",
    "rainy",
}
HOT_KEYWORDS = {
    "\u9ad8\u6e29",
    "\u5f88\u70ed",
    "\u592a\u70ed",
    "\u592a\u6652",
    "hot",
}
COLD_KEYWORDS = {
    "\u5f88\u51b7",
    "\u5929\u51b7",
    "\u5f88\u51b7",
    "cold",
}
SUNNY_KEYWORDS = {
    "\u6674\u5929",
    "sunny",
}

NON_WEATHER_HOT_WORDS = {
    "\u70ed\u95f9",
    "\u70ed\u95e8",
    "\u70ed\u70b9",
}

EVENING_KEYWORDS = {
    "\u665a\u4e0a",
    "\u591c\u91cc",
    "\u591c\u95f4",
    "\u591c\u666f",
    "\u591c\u6e38",
    "\u665a\u996d\u540e",
    "\u5403\u5b8c\u665a\u996d",
    "\u665a\u9910\u540e",
}

MORNING_KEYWORDS = {
    "\u4e0a\u5348",
    "\u65e9\u4e0a",
    "\u4e00\u65e9",
    "\u65e9\u6668",
}

MIDDAY_KEYWORDS = {
    "\u4e2d\u5348",
    "\u4e2d\u5348\u524d\u540e",
    "\u5348\u996d\u524d\u540e",
    "\u5348\u95f4",
}

AFTERNOON_KEYWORDS = {
    "\u4e0b\u5348",
    "\u5348\u540e",
}

ORIGIN_NEARBY_SUFFIXES = {"\u9644\u8fd1", "\u8fd9\u8fb9", "\u5468\u8fb9"}
ORIGIN_ALIAS_MAP = {
    "\u949f\u697c": {"\u949f\u697c"},
    "\u5c0f\u5be8": {"\u5c0f\u5be8"},
    "\u5927\u96c1\u5854": {"\u5927\u96c1\u5854"},
    "\u66f2\u6c5f": {"\u66f2\u6c5f"},
    "\u56de\u6c11\u8857": {"\u56de\u6c11\u8857"},
}


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _normalize_text(text: str) -> str:
    normalized = text.strip().lower()
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def _parse_companion_type(text: str) -> str:
    if _contains_any(text, PARENTS_KEYWORDS):
        return "parents"
    if _contains_any(text, PARTNER_KEYWORDS):
        return "partner"
    if _contains_any(text, FRIENDS_KEYWORDS):
        return "friends"
    if _contains_any(text, SOLO_KEYWORDS):
        return "solo"
    return DEFAULT_COMPANION_TYPE


def _parse_available_hours(text: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:\u5c0f\u65f6|h|hour)", text, flags=re.IGNORECASE)
    if match:
        value = float(match.group(1))
        return max(1.0, min(value, 24.0))

    if "\u534a\u5929" in text:
        return 4.0
    if _contains_any(text, {"\u4e00\u5929", "\u5168\u5929"}):
        return 8.0
    return DEFAULT_AVAILABLE_HOURS


def _parse_budget_level(text: str) -> str:
    if _contains_any(text, HIGH_BUDGET_KEYWORDS):
        return "high"
    if _contains_any(text, LOW_BUDGET_KEYWORDS):
        return "low"
    if _contains_any(text, MEDIUM_BUDGET_KEYWORDS):
        return "medium"

    amount_match = re.search(r"\u9884\u7b97[:\uff1a]?\s*(\d{2,5})", text)
    if amount_match:
        amount = int(amount_match.group(1))
        if amount <= 200:
            return "low"
        if amount >= 800:
            return "high"
        return "medium"

    return DEFAULT_BUDGET_LEVEL


def _parse_purpose(text: str, companion_type: str) -> str:
    if companion_type == "partner" or _contains_any(text, DATING_KEYWORDS) or _contains_any(text, PARTNER_KEYWORDS):
        return "dating"
    if _contains_any(text, RELAX_KEYWORDS):
        return "relax"
    if _contains_any(text, TOURISM_KEYWORDS):
        return "tourism"
    if _contains_any(text, STRONG_FOOD_PURPOSE_KEYWORDS):
        return "food"
    return DEFAULT_PURPOSE


def _parse_need_meal(text: str) -> bool:
    if _contains_any(text, NO_MEAL_KEYWORDS):
        return False
    if _contains_any(text, YES_MEAL_KEYWORDS):
        return True
    return DEFAULT_NEED_MEAL


def _parse_walking_tolerance(text: str) -> str:
    if _contains_any(text, LOW_WALK_KEYWORDS):
        return "low"
    if _contains_any(text, HIGH_WALK_KEYWORDS):
        return "high"
    if _contains_any(text, MEDIUM_WALK_KEYWORDS):
        return "medium"
    return DEFAULT_WALKING_TOLERANCE


def _parse_weather(text: str) -> str:
    if _contains_any(text, RAINY_KEYWORDS):
        return "rainy"
    if _contains_any(text, HOT_KEYWORDS) and not _contains_any(text, NON_WEATHER_HOT_WORDS):
        return "hot"
    if _contains_any(text, COLD_KEYWORDS):
        return "cold"
    if _contains_any(text, SUNNY_KEYWORDS):
        return "sunny"
    return DEFAULT_WEATHER


def _parse_preferred_period(text: str) -> str | None:
    """Lightweight period signal from time expressions.

    If multiple signals exist, prefer the later time window.
    """
    period_candidates = [
        ("morning", MORNING_KEYWORDS),
        ("midday", MIDDAY_KEYWORDS),
        ("afternoon", AFTERNOON_KEYWORDS),
        ("evening", EVENING_KEYWORDS),
    ]
    matched = [period for period, keywords in period_candidates if _contains_any(text, keywords)]
    if not matched:
        return None
    if len(matched) == 1:
        return matched[0]

    priority = {"morning": 0, "midday": 1, "afternoon": 2, "evening": 3}
    return max(matched, key=lambda value: priority.get(value, -1))


def _clean_origin_text(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"(?:\u9644\u8fd1|\u8fd9\u8fb9|\u5468\u8fb9)$", "", cleaned)
    cleaned = re.sub(r"^(?:\u6211\u5728|\u5728)", "", cleaned)
    return cleaned.strip()


def _extract_nearby_anchor(text: str) -> str | None:
    for canonical, aliases in ORIGIN_ALIAS_MAP.items():
        for alias in aliases:
            if any(f"{alias}{suffix}" in text for suffix in ORIGIN_NEARBY_SUFFIXES):
                return canonical
            if any(f"{prefix}{alias}{tail}" in text for prefix in {"\u4ece", "\u7531"} for tail in {"\u51fa\u53d1", "\u5f00\u59cb", "\u542f\u7a0b"}):
                return canonical
    return None


def _extract_known_anchor(text: str) -> str | None:
    normalized = _normalize_text(str(text or ""))
    for canonical, aliases in ORIGIN_ALIAS_MAP.items():
        if canonical in normalized:
            return canonical
        for alias in aliases:
            if alias in normalized:
                return canonical
    return None


def _parse_origin_and_preference(raw_text: str, normalized_text: str) -> tuple[str, str | None]:
    anchor = _extract_nearby_anchor(normalized_text)
    if anchor:
        return anchor, "nearby"

    patterns = [
        r"(?:\u4ece|\u7531)([^,\uff0c.\u3002;\uff1b\u3001\s]{1,20}?)(?:\u51fa\u53d1|\u5f00\u59cb|\u542f\u7a0b)",
        r"(?:\u8d77\u70b9(?:\u5728|\u662f)?)([^,\uff0c.\u3002;\uff1b\u3001\s]{1,20})",
        r"(?:\u5728)?([^,\uff0c.\u3002;\uff1b\u3001\s]{1,20}?)(?:\u9644\u8fd1|\u8fd9\u8fb9|\u5468\u8fb9)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_text)
        if match:
            value = _clean_origin_text(match.group(1))
            if value:
                if any(suffix in normalized_text for suffix in ORIGIN_NEARBY_SUFFIXES) or any(
                    token in normalized_text for token in {"\u4ece", "\u7531"}
                ):
                    return value, "nearby"
                return value, None
    return DEFAULT_ORIGIN, None


def _enhance_origin_with_amap(origin: str, origin_preference_mode: str | None) -> tuple[dict, dict]:
    """Light geocode/reverse-geocode enhancement.

    Fallback-first policy:
    - no/invalid key -> keep rule parse result
    - API failure -> keep rule parse result
    """
    base = {
        "origin": origin,
        "origin_preference_mode": origin_preference_mode,
        "origin_latitude": None,
        "origin_longitude": None,
        "origin_adcode": None,
    }
    debug = {
        "amap_attempted": False,
        "amap_tool": "geocode",
        "amap_hit": False,
        "amap_infocode": None,
        "amap_info": None,
    }
    # Keep parser stable/fast: only trigger geocode for nearby or non-canonical origin text.
    if origin_preference_mode != "nearby" and _extract_known_anchor(origin) == origin:
        return base, debug
    key, _ = load_valid_amap_api_key("AMAP_API_KEY")
    if not key:
        return base, debug

    try:
        debug["amap_attempted"] = True
        geo_debug = geocode_address(origin, api_key=key, debug=True)
        debug["amap_infocode"] = geo_debug.get("amap_infocode")
        debug["amap_info"] = geo_debug.get("amap_info")
        debug["exception_type"] = geo_debug.get("exception_type")
        debug["exception_message"] = geo_debug.get("exception_message")
        debug["request_url"] = geo_debug.get("request_url")
        debug["timeout_seconds"] = geo_debug.get("timeout_seconds")
        debug["proxy_mode"] = geo_debug.get("proxy_mode")
        debug["env_proxy_snapshot"] = geo_debug.get("env_proxy_snapshot")
        if not geo_debug.get("ok"):
            return base, debug
        geo = geo_debug.get("result", {})
    except Exception:
        return base, debug

    lat = geo.get("latitude")
    lon = geo.get("longitude")
    if isinstance(lat, (int, float)):
        base["origin_latitude"] = float(lat)
    if isinstance(lon, (int, float)):
        base["origin_longitude"] = float(lon)
    adcode = str(geo.get("adcode") or "").strip()
    if adcode:
        base["origin_adcode"] = adcode

    # Keep origin concise: prefer known anchors if geocode/reverse text can map.
    anchor = _extract_known_anchor(geo.get("formatted_address") or "")
    if anchor is None and base["origin_latitude"] is not None and base["origin_longitude"] is not None:
        try:
            reverse = reverse_geocode(base["origin_latitude"], base["origin_longitude"], api_key=key)
            anchor = _extract_known_anchor(
                " ".join(
                    [
                        str(reverse.get("formatted_address") or ""),
                        str(reverse.get("district") or ""),
                    ]
                )
            )
        except Exception:
            anchor = None

    if anchor:
        base["origin"] = anchor
        if base["origin_preference_mode"] is None and (
            "附近" in origin or "这边" in origin or "周边" in origin
        ):
            base["origin_preference_mode"] = "nearby"
    debug["amap_hit"] = True
    return base, debug


def _normalize_llm_origin(origin_text: str | None) -> tuple[str | None, str | None]:
    if not origin_text or not isinstance(origin_text, str):
        return None, None
    normalized = _normalize_text(origin_text)
    anchor = _extract_nearby_anchor(normalized)
    if anchor:
        return anchor, "nearby"
    # fallback: strip nearby suffixes if present
    cleaned = _clean_origin_text(origin_text)
    return cleaned if cleaned else None, None


def _parse_rule_payload(text: str) -> dict:
    normalized = _normalize_text(text)
    companion_type = _parse_companion_type(normalized)
    origin, origin_preference_mode = _parse_origin_and_preference(text, normalized)
    payload = {
        "companion_type": companion_type,
        "available_hours": _parse_available_hours(normalized),
        "budget_level": _parse_budget_level(normalized),
        "purpose": _parse_purpose(normalized, companion_type=companion_type),
        "need_meal": _parse_need_meal(normalized),
        "walking_tolerance": _parse_walking_tolerance(normalized),
        "weather": _parse_weather(normalized),
        "origin": origin,
        "origin_preference_mode": origin_preference_mode,
        "preferred_period": _parse_preferred_period(normalized),
    }
    enhanced, geo_debug = _enhance_origin_with_amap(origin, origin_preference_mode)
    payload.update(enhanced)
    payload["_amap_geo_debug"] = geo_debug
    return payload


def parse_free_text_to_plan_request_with_debug(text: str) -> tuple[PlanRequest, dict]:
    """Parse free text into PlanRequest using lightweight rules.

    V1 parser scope:
    - keyword mapping + minimal regex extraction
    - no LLM, no external dependency
    - includes lightweight period hint extraction (preferred_period)
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")

    rule_payload = _parse_rule_payload(text)
    llm_payload, llm_debug = parse_free_text_with_llm_debug(text)

    if isinstance(llm_payload, dict):
        merged = dict(rule_payload)
        llm_origin_raw = llm_payload.get("origin") if isinstance(llm_payload.get("origin"), str) else None
        llm_origin_clean, llm_origin_pref = _normalize_llm_origin(llm_origin_raw)
        if llm_origin_clean:
            llm_payload["origin"] = llm_origin_clean
            if llm_origin_pref and not llm_payload.get("origin_preference_mode"):
                llm_payload["origin_preference_mode"] = llm_origin_pref
        for key, value in llm_payload.items():
            if value is not None:
                merged[key] = value

        # Lightweight correction for known misjudgments:
        # 1) Keep dating over food when rule has strong dating.
        if merged.get("purpose") == "food" and rule_payload.get("purpose") == "dating":
            merged["purpose"] = "dating"
        # 2) Prefer cleaner rule origin when LLM origin contains nearby suffix.
        if rule_payload.get("origin") and isinstance(llm_origin_raw, str):
            if any(suffix in llm_origin_raw for suffix in ORIGIN_NEARBY_SUFFIXES):
                merged["origin"] = rule_payload.get("origin")
        # 3) Keep rule need_meal=true when LLM sets false (known misjudgment).
        if rule_payload.get("need_meal") is True and merged.get("need_meal") is False:
            merged["need_meal"] = True

        # Re-attach geocode enhancement after merge (LLM may override origin text).
        enhanced, geo_debug = _enhance_origin_with_amap(
            str(merged.get("origin") or DEFAULT_ORIGIN),
            merged.get("origin_preference_mode"),
        )
        merged.update(enhanced)
        merged["_amap_geo_debug"] = geo_debug

        merged["parsed_by"] = "llm"
        try:
            debug_payload = {
                **llm_debug,
                "parsed_by": "llm",
                "llm_action_valid": None,
                "llm_selected_plan_valid": None,
                "amap_geo_debug": merged.get("_amap_geo_debug"),
            }
            merged.pop("_amap_geo_debug", None)
            return PlanRequest(**merged), debug_payload
        except Exception:
            # If merged payload unexpectedly breaks schema, fallback to rule parse.
            llm_debug["fallback_reason"] = "llm_planrequest_build_failed"

    rule_payload["parsed_by"] = "rule"
    amap_geo_debug = rule_payload.pop("_amap_geo_debug", None)
    return PlanRequest(**rule_payload), {
        **llm_debug,
        "parsed_by": "rule",
        "llm_action_valid": None,
        "llm_selected_plan_valid": None,
        "amap_geo_debug": amap_geo_debug,
    }


def parse_free_text_to_plan_request(text: str) -> PlanRequest:
    request, _ = parse_free_text_to_plan_request_with_debug(text)
    return request
