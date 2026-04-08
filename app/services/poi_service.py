from __future__ import annotations

import math
import os
import re
from typing import Any, Dict, Iterable, List

from app.services.amap_client import amap_get_json, load_valid_amap_api_key
from app.services.cache_service import build_cache_key, get_cache, is_cache_enabled, set_cache

AMAP_KEY_ENV = "AMAP_API_KEY"
AMAP_CITY_ENV = "AMAP_CITY"
AMAP_DEFAULT_CITY = "\u897f\u5b89"
POI_SEARCH_CACHE_TTL_SECONDS = 45 * 60
POI_MERGED_CACHE_TTL_SECONDS = 45 * 60
# Bump this version whenever merged POI rules change (cluster mapping, scope filter,
# inferred-field strategy, dedup logic, minimum-count guard, etc.) so old cache keys
# do not pollute new merged results.
POI_MERGED_CACHE_VERSION = "v1"

DISTRICT_BEILIN = "\u7891\u6797\u533a"
DISTRICT_LIANHU = "\u83b2\u6e56\u533a"
DISTRICT_YANTA = "\u96c1\u5854\u533a"
CORE_DISTRICTS = {DISTRICT_BEILIN, DISTRICT_LIANHU, DISTRICT_YANTA}

CLUSTER_CITY_WALL = "\u57ce\u5899\u949f\u9f13\u697c\u7c07"
CLUSTER_XIAOZHAI = "\u5c0f\u5be8\u6587\u535a\u7c07"
CLUSTER_DAYANTA = "\u5927\u96c1\u5854\u7c07"
CLUSTER_QUJIANG = "\u66f2\u6c5f\u591c\u6e38\u7c07"

FIXED_CLUSTERS = {
    CLUSTER_CITY_WALL,
    CLUSTER_XIAOZHAI,
    CLUSTER_DAYANTA,
    CLUSTER_QUJIANG,
}

CLUSTER_KEYWORD_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        CLUSTER_CITY_WALL,
        (
            "\u949f\u697c",
            "\u9f13\u697c",
            "\u56de\u6c11\u8857",
            "\u57ce\u5899",
            "\u5357\u95e8",
            "\u949f\u9f13\u697c",
        ),
    ),
    (CLUSTER_XIAOZHAI, ("\u9655\u897f\u5386\u53f2\u535a\u7269\u9986", "\u5c0f\u5be8", "\u8d5b\u683c")),
    (CLUSTER_DAYANTA, ("\u5927\u96c1\u5854", "\u5927\u6148\u6069\u5bfa", "\u6148\u6069\u5bfa", "\u6148\u6069")),
    (CLUSTER_QUJIANG, ("\u5927\u5510\u8299\u84c9\u56ed", "\u5927\u5510\u4e0d\u591c\u57ce", "\u66f2\u6c5f")),
]

# Coordinate order: (longitude, latitude)
CLUSTER_CENTERS: dict[str, tuple[float, float]] = {
    CLUSTER_CITY_WALL: (108.9485, 34.2585),
    CLUSTER_XIAOZHAI: (108.9580, 34.2280),
    CLUSTER_DAYANTA: (108.9650, 34.2210),
    CLUSTER_QUJIANG: (108.9800, 34.2170),
}

SIGHT_SEARCH_KEYWORDS = [
    "\u949f\u697c",
    "\u9f13\u697c",
    "\u56de\u6c11\u8857",
    "\u57ce\u5899\u5357\u95e8",
    "\u9655\u897f\u5386\u53f2\u535a\u7269\u9986",
    "\u5c0f\u5be8",
    "\u5927\u96c1\u5854",
    "\u5927\u6148\u6069\u5bfa",
    "\u5927\u5510\u8299\u84c9\u56ed",
    "\u5927\u5510\u4e0d\u591c\u57ce",
]

RESTAURANT_SEARCH_KEYWORDS = [
    "\u949f\u697c \u9910\u5385",
    "\u56de\u6c11\u8857 \u9910\u5385",
    "\u5357\u95e8 \u9910\u5385",
    "\u5c0f\u5be8 \u9910\u5385",
    "\u9655\u897f\u5386\u53f2\u535a\u7269\u9986 \u9910\u5385",
    "\u5927\u96c1\u5854 \u9910\u5385",
    "\u5927\u5510\u4e0d\u591c\u57ce \u9910\u5385",
    "\u66f2\u6c5f \u9910\u5385",
]

MIN_REAL_SIGHTS = 4
MIN_REAL_RESTAURANTS = 4
TARGET_SIGHTS = 8
TARGET_RESTAURANTS = 8

INFERRED_CORE_FIELDS = [
    "district_cluster",
    "indoor_or_outdoor",
    "parent_friendly",
    "friend_friendly",
    "couple_friendly",
    "walking_level",
    "estimated_visit_minutes",
    "tags",
    "child_friendly",
    "accessible",
]

SIGHT_CLASSIFY_KEYWORDS = {
    "博物馆",
    "展馆",
    "美术馆",
    "科技馆",
    "城墙",
    "塔",
    "寺",
    "园",
    "广场",
    "古镇",
    "步行街",
    "遗址",
    "景区",
    "文化",
    "博览",
    "museum",
    "scenic",
    "landmark",
    "history",
}
RESTAURANT_CLASSIFY_KEYWORDS = {
    "餐",
    "饭",
    "面",
    "火锅",
    "串",
    "烤",
    "小吃",
    "咖啡",
    "甜品",
    "茶",
    "饮品",
    "food",
    "restaurant",
    "cafe",
    "snack",
}
SHOPPING_CLASSIFY_KEYWORDS = {
    "购物中心",
    "商场",
    "超市",
    "mall",
    "shopping",
}


def _http_get_json(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return amap_get_json(path=path, params=params, timeout_seconds=6)


def _haversine_meters(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    lon1, lat1 = origin
    lon2, lat2 = destination
    earth_radius = 6371000.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius * c


def _parse_location(location_text: str) -> tuple[float, float] | None:
    if not location_text or "," not in location_text:
        return None
    lon_text, lat_text = location_text.split(",", 1)
    try:
        return float(lon_text), float(lat_text)
    except ValueError:
        return None


def _district_in_scope(adname: str) -> bool:
    if not adname:
        return False
    return any(district in adname for district in CORE_DISTRICTS)


def _match_cluster(name: str, address: str, business_area: str, coordinate: tuple[float, float]) -> str | None:
    text = f"{name} {address} {business_area}"
    for cluster, keywords in CLUSTER_KEYWORD_RULES:
        if any(keyword in text for keyword in keywords):
            return cluster

    nearest_cluster = None
    nearest_distance = 9999999.0
    for cluster, center in CLUSTER_CENTERS.items():
        distance = _haversine_meters(center, coordinate)
        if distance < nearest_distance:
            nearest_distance = distance
            nearest_cluster = cluster

    if nearest_cluster and nearest_distance <= 4500:
        return nearest_cluster
    return None


def _extract_cost_level(cost_text: Any, default_level: str = "medium") -> str:
    try:
        cost = float(cost_text)
    except (TypeError, ValueError):
        return default_level

    if cost <= 40:
        return "low"
    if cost <= 120:
        return "medium"
    return "high"


def _extract_rating(value: Any) -> float | None:
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    if rating <= 0:
        return None
    return round(rating, 1)


def _extract_rating_count(value: Any) -> int | None:
    try:
        count = int(float(value))
    except (TypeError, ValueError):
        return None
    if count <= 0:
        return None
    return count


def _kind_match_ok(kind: str, name: str, category_text: str, address: str) -> bool:
    text = f"{name} {category_text} {address}"
    if any(keyword in text for keyword in SHOPPING_CLASSIFY_KEYWORDS):
        return False
    if kind == "restaurant":
        return any(keyword in text for keyword in RESTAURANT_CLASSIFY_KEYWORDS)
    if kind == "sight":
        return any(keyword in text for keyword in SIGHT_CLASSIFY_KEYWORDS)
    return False


def _sight_visit_minutes(name: str, category: str) -> int:
    text = f"{name} {category}"
    if "\u535a\u7269\u9986" in text:
        return 90
    if any(keyword in text for keyword in ["\u57ce\u5899", "\u8299\u84c9\u56ed", "\u4e0d\u591c\u57ce"]):
        return 80
    if any(keyword in text for keyword in ["\u5927\u96c1\u5854", "\u6148\u6069\u5bfa", "\u949f\u697c", "\u9f13\u697c"]):
        return 70
    return 60


def _sight_walking_level(name: str, category: str) -> str:
    text = f"{name} {category}"
    if any(keyword in text for keyword in ["\u57ce\u5899", "\u6b65\u884c\u8857", "\u5e7f\u573a"]):
        return "high"
    if any(keyword in text for keyword in ["\u535a\u7269\u9986", "\u5c55\u89c8\u9986"]):
        return "low"
    return "medium"


def _sight_indoor_or_outdoor(name: str, category: str) -> str:
    text = f"{name} {category}"
    if any(keyword in text for keyword in ["\u535a\u7269\u9986", "\u5c55\u89c8\u9986", "\u7f8e\u672f\u9986", "\u79d1\u6280\u9986"]):
        return "indoor"
    if any(keyword in text for keyword in ["\u516c\u56ed", "\u57ce\u5899", "\u6b65\u884c\u8857", "\u666f\u533a"]):
        return "outdoor"
    return "mixed"


def _default_tags(kind: str, cluster: str) -> list[str]:
    if kind == "sight":
        tags = ["real_poi", "amap", "core_city", "classic"]
    else:
        tags = ["real_poi", "amap", "core_city", "food"]

    if cluster == CLUSTER_QUJIANG:
        tags.append("night_view")
    if cluster == CLUSTER_CITY_WALL:
        tags.append("history")
    return tags


def _default_opening_hours(kind: str, name: str, category: str, cluster: str) -> tuple[str, str, bool]:
    text = f"{name} {category} {cluster}"

    if kind == "restaurant":
        return "10:00", "21:00", False

    if any(keyword in text for keyword in ["\u535a\u7269\u9986", "\u6587\u535a", "\u5c55\u9986"]):
        return "09:00", "17:00", False
    if any(keyword in text for keyword in ["\u4e0d\u591c\u57ce", "\u591c\u6e38", "\u591c\u666f"]) or cluster == CLUSTER_QUJIANG:
        return "18:00", "23:00", False
    return "09:00", "21:00", False


def _extract_business_hours(raw: Dict[str, Any]) -> tuple[str, str, bool] | None:
    candidates = []
    business_hours = raw.get("business_hours")
    if isinstance(business_hours, str):
        candidates.append(business_hours)

    biz_ext = raw.get("biz_ext")
    if isinstance(biz_ext, dict):
        for key in ("business_hours", "opentime_today", "open_time"):
            value = biz_ext.get(key)
            if isinstance(value, str):
                candidates.append(value)

    for text in candidates:
        text = text.strip()
        if not text:
            continue
        if "24" in text and "\u5c0f\u65f6" in text:
            return "00:00", "23:59", True

        match = re.search(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})", text)
        if match:
            return match.group(1), match.group(2), False

    return None


def _map_raw_poi(raw: Dict[str, Any], kind: str) -> Dict[str, Any] | None:
    name = (raw.get("name") or "").strip()
    if not name:
        return None

    adname = (raw.get("adname") or "").strip()
    if not _district_in_scope(adname):
        return None

    coordinate = _parse_location(raw.get("location", ""))
    if not coordinate:
        return None

    address = (raw.get("address") or "").strip()
    business_area = (raw.get("business_area") or "").strip()
    cluster = _match_cluster(name=name, address=address, business_area=business_area, coordinate=coordinate)
    if not cluster or cluster not in FIXED_CLUSTERS:
        return None

    category_text = (raw.get("type") or "").strip()
    category = category_text.split(";")[0] if category_text else ("scenic_spot" if kind == "sight" else "restaurant")
    biz_ext = raw.get("biz_ext") if isinstance(raw.get("biz_ext"), dict) else {}
    place_id = raw.get("id") or f"{name}_{cluster}"

    if not _kind_match_ok(kind, name, category_text, address):
        return None

    lon, lat = coordinate
    opening = _extract_business_hours(raw)
    inferred_fields = list(INFERRED_CORE_FIELDS)

    if opening:
        open_time, close_time, is_all_day = opening
        opening_hours_type = "amap"
    else:
        open_time, close_time, is_all_day = _default_opening_hours(kind, name, category, cluster)
        opening_hours_type = "default_rule"
        inferred_fields.extend(["opening_hours_type", "open_time", "close_time", "is_all_day"])

    rating = _extract_rating(biz_ext.get("rating"))
    rating_count = _extract_rating_count(biz_ext.get("rating_num") or biz_ext.get("comment_num"))

    if kind == "sight":
        inferred_fields.append("rating")
        inferred_fields.append("rating_count")
        mapped = {
            "id": f"amap_sight_{place_id}",
            "name": name,
            "kind": "sight",
            "district_cluster": cluster,
            "category": category,
            "indoor_or_outdoor": _sight_indoor_or_outdoor(name, category),
            "parent_friendly": True,
            "friend_friendly": True,
            "couple_friendly": True,
            "child_friendly": True,
            "accessible": _sight_indoor_or_outdoor(name, category) == "indoor",
            "cost_level": _extract_cost_level(biz_ext.get("cost"), default_level="medium"),
            "walking_level": _sight_walking_level(name, category),
            "estimated_visit_minutes": _sight_visit_minutes(name, category),
            "latitude": lat,
            "longitude": lon,
            "tags": _default_tags("sight", cluster),
            "photo_friendly": True,
            "rating": rating if rating is not None else 4.3,
            "rating_count": rating_count if rating_count is not None else 20,
            "poi_source": "amap",
            "opening_hours_type": opening_hours_type,
            "open_time": open_time,
            "close_time": close_time,
            "is_all_day": is_all_day,
            "inferred_fields": sorted(
                set(
                    inferred_fields
                    + ["cost_level", "photo_friendly", "rating", "rating_count", "child_friendly", "accessible"]
                )
            ),
        }
        return mapped

    inferred_rest_fields = list(inferred_fields)
    if rating is None:
        inferred_rest_fields.append("rating")
    if rating_count is None:
        inferred_rest_fields.append("rating_count")

    mapped = {
        "id": f"amap_restaurant_{place_id}",
        "name": name,
        "kind": "restaurant",
        "district_cluster": cluster,
        "category": category,
        "indoor_or_outdoor": "indoor",
        "parent_friendly": True,
        "friend_friendly": True,
        "couple_friendly": True,
        "child_friendly": True,
        "accessible": True,
        "cost_level": _extract_cost_level(biz_ext.get("cost"), default_level="medium"),
        "walking_level": "low",
        "estimated_visit_minutes": 60,
        "latitude": lat,
        "longitude": lon,
        "tags": _default_tags("restaurant", cluster),
        "rating": rating if rating is not None else 4.3,
        "rating_count": rating_count if rating_count is not None else 20,
        "flavor_profile": ["local"],
        "spicy_level": "medium",
        "queue_level": "medium",
        "parking_friendly": False,
        "poi_source": "amap",
        "opening_hours_type": opening_hours_type,
        "open_time": open_time,
        "close_time": close_time,
        "is_all_day": is_all_day,
        "inferred_fields": sorted(
            set(
                inferred_rest_fields
                + [
                    "cost_level",
                    "rating_count",
                    "child_friendly",
                    "accessible",
                    "flavor_profile",
                    "spicy_level",
                    "queue_level",
                    "parking_friendly",
                ]
            )
        ),
    }
    return mapped


def _search_pois_by_keywords(api_key: str, keywords: Iterable[str]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    city = os.getenv(AMAP_CITY_ENV, AMAP_DEFAULT_CITY)
    cache_enabled = is_cache_enabled()
    for keyword in keywords:
        # Key layout example:
        # xian-agent:poi-search:xian-core:<city>:<keyword>:<payload-hash>
        cache_key = build_cache_key(
            "poi-search",
            "xian-core",
            city,
            keyword,
            payload={"citylimit": True, "offset": 20, "page": 1, "extensions": "all"},
        )
        cached = get_cache(cache_key) if cache_enabled else None
        if isinstance(cached, list):
            if cache_enabled:
                print(f"[cache hit] poi search {keyword}")
            results.extend(cached)
            continue

        if cache_enabled:
            print(f"[cache miss] poi search {keyword}")
        payload = _http_get_json(
            "/v3/place/text",
            {
                "key": api_key,
                "keywords": keyword,
                "city": city,
                "citylimit": "true",
                "offset": 20,
                "page": 1,
                "extensions": "all",
            },
        )
        if payload.get("status") != "1":
            raise RuntimeError(f"amap place text failed for keyword={keyword}")
        pois = payload.get("pois") or []
        if isinstance(pois, list):
            if cache_enabled:
                set_cache(cache_key, pois, POI_SEARCH_CACHE_TTL_SECONDS)
            results.extend(pois)
    return results


def _deduplicate_pois(pois: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for poi in pois:
        key = (
            poi.get("kind"),
            poi.get("district_cluster"),
            poi.get("name", "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(poi)
    return deduped


def _count_by_kind(pois: List[Dict[str, Any]], kind: str) -> int:
    return sum(1 for poi in pois if poi.get("kind") == kind)


def _top_up_with_mock(
    real_pois: List[Dict[str, Any]],
    fallback_pois: List[Dict[str, Any]],
    target_sights: int = TARGET_SIGHTS,
    target_restaurants: int = TARGET_RESTAURANTS,
) -> List[Dict[str, Any]]:
    merged = list(real_pois)
    seen = {(p.get("kind"), p.get("district_cluster"), p.get("name")) for p in merged}

    for kind, target in [("sight", target_sights), ("restaurant", target_restaurants)]:
        need = target - _count_by_kind(merged, kind)
        if need <= 0:
            continue
        for poi in fallback_pois:
            if poi.get("kind") != kind:
                continue
            key = (poi.get("kind"), poi.get("district_cluster"), poi.get("name"))
            if key in seen:
                continue
            merged.append(poi)
            seen.add(key)
            need -= 1
            if need <= 0:
                break

    return merged


def _fetch_real_pois(api_key: str) -> List[Dict[str, Any]]:
    cache_enabled = is_cache_enabled()
    merged_cache_key = _build_poi_merged_cache_key()
    cached = get_cache(merged_cache_key) if cache_enabled else None
    if isinstance(cached, list):
        if cache_enabled:
            print("[cache hit] poi merged")
        return cached

    if cache_enabled:
        print("[cache miss] poi merged")
    raw_sights = _search_pois_by_keywords(api_key=api_key, keywords=SIGHT_SEARCH_KEYWORDS)
    raw_restaurants = _search_pois_by_keywords(api_key=api_key, keywords=RESTAURANT_SEARCH_KEYWORDS)

    mapped: List[Dict[str, Any]] = []
    for raw in raw_sights:
        poi = _map_raw_poi(raw, kind="sight")
        if poi:
            mapped.append(poi)
    for raw in raw_restaurants:
        poi = _map_raw_poi(raw, kind="restaurant")
        if poi:
            mapped.append(poi)

    mapped = _deduplicate_pois(mapped)

    sight_count = _count_by_kind(mapped, "sight")
    restaurant_count = _count_by_kind(mapped, "restaurant")
    if sight_count < MIN_REAL_SIGHTS or restaurant_count < MIN_REAL_RESTAURANTS:
        raise RuntimeError("insufficient real pois in core scope")

    if cache_enabled:
        set_cache(merged_cache_key, mapped, POI_MERGED_CACHE_TTL_SECONDS)
    return mapped


def _build_poi_merged_cache_key() -> str:
    # Key layout example:
    # xian-agent:poi-merged:xian-core:<city>:<version>:<payload-hash>
    # Version must be bumped when merged-data rules change, so stale cached results
    # from older rules cannot be reused.
    return build_cache_key(
        "poi-merged",
        "xian-core",
        os.getenv(AMAP_CITY_ENV, AMAP_DEFAULT_CITY),
        POI_MERGED_CACHE_VERSION,
        payload={
            "sight_keywords": SIGHT_SEARCH_KEYWORDS,
            "restaurant_keywords": RESTAURANT_SEARCH_KEYWORDS,
            "districts": sorted(CORE_DISTRICTS),
            "clusters": sorted(FIXED_CLUSTERS),
        },
    )


def load_pois(
    request_context: Any = None,
    fallback_pois: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Unified POI entry.

    Strategy:
    1) key + success -> real POIs first
    2) key missing -> mock
    3) key present but failure -> mock
    """
    del request_context

    mock = list(fallback_pois or [])
    api_key, key_error = load_valid_amap_api_key(AMAP_KEY_ENV)
    if not api_key:
        if key_error == "invalid_api_key":
            print("[amap] invalid api key format, fallback to mock pois")
        return mock

    try:
        real = _fetch_real_pois(api_key)
        if not mock:
            return real
        return _top_up_with_mock(real_pois=real, fallback_pois=mock)
    except Exception:  # noqa: BLE001
        return mock
