from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any, Dict, List, Tuple

from app.services.poi_service import (
    CLUSTER_CITY_WALL,
    CLUSTER_DAYANTA,
    CLUSTER_QUJIANG,
    CLUSTER_XIAOZHAI,
    CLUSTER_CENTERS,
    CORE_DISTRICTS,
    DISTRICT_BEILIN,
    DISTRICT_LIANHU,
    DISTRICT_YANTA,
    FIXED_CLUSTERS,
)

ALLOWED_KINDS = {"sight", "restaurant"}

CLUSTER_TO_DISTRICTS = {
    CLUSTER_CITY_WALL: {DISTRICT_BEILIN, DISTRICT_LIANHU},
    CLUSTER_XIAOZHAI: {DISTRICT_YANTA},
    CLUSTER_DAYANTA: {DISTRICT_YANTA},
    CLUSTER_QUJIANG: {DISTRICT_YANTA},
}

NOISE_NAME_KEYWORDS = {
    "test",
    "demo",
    "sample",
    "unknown",
    "null",
    "none",
    "placeholder",
    "\u6d4b\u8bd5",
    "\u6837\u4f8b",
    "\u5360\u4f4d",
    "\u672a\u547d\u540d",
    "\u672a\u77e5",
    "\u7a7a",
}

RESTAURANT_ALLOW_KEYWORDS = {
    "\u9910",
    "\u996d",
    "\u9762",
    "\u706b\u9505",
    "\u4e32",
    "\u70e4",
    "\u5c0f\u5403",
    "\u5496\u5561",
    "\u751c\u54c1",
    "\u8336",
    "food",
    "restaurant",
    "cafe",
    "snack",
}
RESTAURANT_DENY_KEYWORDS = {
    "\u533b\u9662",
    "\u5b66\u6821",
    "\u94f6\u884c",
    "\u5546\u573a",
    "\u8d2d\u7269\u4e2d\u5fc3",
    "\u5199\u5b57\u697c",
    "\u516c\u53f8",
    "\u5c0f\u533a",
    "\u666f\u533a",
    "\u535a\u7269\u9986",
    "\u516c\u56ed",
    "\u505c\u8f66\u573a",
    "\u516c\u4ea4",
    "\u5730\u94c1",
}

SIGHT_ALLOW_KEYWORDS = {
    "\u666f",
    "\u535a\u7269\u9986",
    "\u57ce\u5899",
    "\u5854",
    "\u5bfa",
    "\u56ed",
    "\u5e7f\u573a",
    "\u53e4\u9547",
    "\u6b65\u884c\u8857",
    "\u4e0d\u591c\u57ce",
    "\u8299\u84c9\u56ed",
    "\u9057\u5740",
    "museum",
    "scenic",
    "landmark",
    "history",
}
SIGHT_DENY_KEYWORDS = {
    "\u533b\u9662",
    "\u5b66\u6821",
    "\u94f6\u884c",
    "\u5546\u573a",
    "\u8d2d\u7269\u4e2d\u5fc3",
    "\u5199\u5b57\u697c",
    "\u516c\u53f8",
    "\u5c0f\u533a",
    "\u505c\u8f66\u573a",
    "\u516c\u4ea4\u7ad9",
    "\u5730\u94c1\u7ad9",
    "\u4fbf\u5229\u5e97",
    "\u8d85\u5e02",
    "\u83dc\u5e02\u573a",
    "\u4fee\u7406",
    "\u6c7d\u4fee",
}

NEAR_DUP_METERS = 120
NEAR_DUP_NAME_SIMILARITY = 0.9
MAX_CHAIN_PER_CLUSTER = 2
CHAIN_FLOOD_THRESHOLD = 3
RATING_DROP_THRESHOLD = 3.0
RATING_STRICT_COUNT = 10

XIANG_CORE_LAT_RANGE = (34.1, 34.35)
XIANG_CORE_LON_RANGE = (108.75, 109.05)
NEARBY_ORIGIN_MAX_METERS = 6000
CLUSTER_MAX_RADIUS_METERS = {
    CLUSTER_CITY_WALL: 6000,
    CLUSTER_XIAOZHAI: 5500,
    CLUSTER_DAYANTA: 5500,
    CLUSTER_QUJIANG: 5500,
}

_LAST_FILTER_STATS: Dict[str, Any] = {}


def _normalize_name(name: str) -> str:
    text = re.sub(r"[\s.\-_/]+", "", name.lower().strip())
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return text


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _haversine_meters(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    x = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    y = 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))
    return radius * y


def _extract_district_text(poi: Dict[str, Any]) -> str:
    for key in ("district", "adname", "district_name"):
        value = poi.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_core_district(district_text: str) -> str | None:
    if not district_text:
        return None
    for district in CORE_DISTRICTS:
        if district in district_text:
            return district
    return None


def _is_noise_name(name: str) -> bool:
    stripped = name.strip()
    if len(stripped) <= 1:
        return True

    lowered = stripped.lower()
    if lowered in NOISE_NAME_KEYWORDS:
        return True

    if re.fullmatch(r"\d+", stripped):
        return True
    if re.fullmatch(r"[^\w\u4e00-\u9fff]+", stripped):
        return True
    if "\ufffd" in stripped:
        return True

    # Conservative in V1.x: only remove obviously broken names.
    punctuation_count = len(re.findall(r"[^\w\u4e00-\u9fff]", stripped))
    if len(stripped) >= 4 and punctuation_count / len(stripped) > 0.6:
        return True
    return False


def _ensure_category(item: Dict[str, Any]) -> bool:
    category = item.get("category")
    if isinstance(category, str) and category.strip():
        item["category"] = category.strip()
        return True

    kind = item.get("kind")
    if kind == "sight":
        item["category"] = "scenic_spot"
        return True
    if kind == "restaurant":
        item["category"] = "restaurant"
        return True
    return False


def _restaurant_quality_ok(item: Dict[str, Any]) -> bool:
    text = f"{item.get('name', '')} {item.get('category', '')} {' '.join(item.get('tags') or [])}".lower()
    has_allow = any(keyword in text for keyword in RESTAURANT_ALLOW_KEYWORDS)
    has_deny = any(keyword in text for keyword in RESTAURANT_DENY_KEYWORDS)
    if has_allow:
        return True
    if has_deny:
        return False
    return True


def _sight_quality_ok(item: Dict[str, Any]) -> bool:
    text = f"{item.get('name', '')} {item.get('category', '')} {' '.join(item.get('tags') or [])}".lower()
    has_allow = any(keyword in text for keyword in SIGHT_ALLOW_KEYWORDS)
    has_deny = any(keyword in text for keyword in SIGHT_DENY_KEYWORDS)
    if has_allow:
        return True
    if has_deny:
        return False
    return True


def _poi_priority(item: Dict[str, Any]) -> Tuple[int, float]:
    source = item.get("poi_source")
    source_score = 2 if source == "amap" else 1
    rating = _safe_float(item.get("rating")) or 0.0
    return source_score, rating


def _is_near_duplicate(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    norm_a = _normalize_name(str(a.get("name", "")))
    norm_b = _normalize_name(str(b.get("name", "")))
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True

    similarity = SequenceMatcher(None, norm_a, norm_b).ratio()
    if similarity < NEAR_DUP_NAME_SIMILARITY:
        return False

    lon_a = _safe_float(a.get("longitude"))
    lat_a = _safe_float(a.get("latitude"))
    lon_b = _safe_float(b.get("longitude"))
    lat_b = _safe_float(b.get("latitude"))
    if None in {lon_a, lat_a, lon_b, lat_b}:
        return False

    distance = _haversine_meters((lon_a, lat_a), (lon_b, lat_b))
    return distance <= NEAR_DUP_METERS


def _brand_key(name: str) -> str:
    text = name.strip()
    text = re.sub(
        r"[\(\uff08][^()\uff08\uff09]*(?:\u5e97|\u5206\u5e97|\u95e8\u5e97|\u5e97\u94fa)[\)\uff09]",
        "",
        text,
    )
    text = re.sub(r"(?:\u65d7\u8230\u5e97|\u5206\u5e97|\u95e8\u5e97|\u5e97\u94fa)$", "", text)
    text = text.split("\u00b7")[0].split("-")[0].strip()
    for token in (
        "\u897f\u5b89",
        "\u949f\u697c",
        "\u56de\u6c11\u8857",
        "\u5357\u95e8",
        "\u5c0f\u5be8",
        "\u5927\u96c1\u5854",
        "\u66f2\u6c5f",
    ):
        text = text.replace(token, "")
    normalized = _normalize_name(text)
    if normalized.endswith("\u5e97") and len(normalized) > 2:
        normalized = normalized[:-1]
    return normalized[:12]


def _apply_dedup(items: List[Dict[str, Any]], removed: Counter[str]) -> List[Dict[str, Any]]:
    exact_seen = set()
    exact_deduped: List[Dict[str, Any]] = []
    for item in items:
        exact_key = (
            item.get("kind"),
            item.get("district_cluster"),
            _normalize_name(str(item.get("name", ""))),
        )
        if exact_key in exact_seen:
            removed["duplicate_exact"] += 1
            continue
        exact_seen.add(exact_key)
        exact_deduped.append(item)

    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for item in exact_deduped:
        grouped[(str(item.get("kind")), str(item.get("district_cluster")))].append(item)

    kept: List[Dict[str, Any]] = []
    for _, group in grouped.items():
        group_sorted = sorted(group, key=_poi_priority, reverse=True)
        kept_in_group: List[Dict[str, Any]] = []
        for item in group_sorted:
            if any(_is_near_duplicate(item, existing) for existing in kept_in_group):
                removed["duplicate_near"] += 1
                continue
            kept_in_group.append(item)
        kept.extend(kept_in_group)
    return kept


def _apply_restaurant_chain_limit(items: List[Dict[str, Any]], removed: Counter[str]) -> List[Dict[str, Any]]:
    sights = [item for item in items if item.get("kind") == "sight"]
    restaurants = [item for item in items if item.get("kind") == "restaurant"]

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in restaurants:
        cluster = str(item.get("district_cluster"))
        brand = _brand_key(str(item.get("name", "")))
        grouped[f"{cluster}:{brand}"].append(item)

    kept_restaurants: List[Dict[str, Any]] = []
    for _, group in grouped.items():
        ordered = sorted(group, key=_poi_priority, reverse=True)
        if len(ordered) > CHAIN_FLOOD_THRESHOLD:
            removed["restaurant_chain_limit"] += len(ordered) - MAX_CHAIN_PER_CLUSTER
            kept_restaurants.extend(ordered[:MAX_CHAIN_PER_CLUSTER])
            continue
        kept_restaurants.extend(ordered)

    return sights + kept_restaurants


def _rating_quality_ok(item: Dict[str, Any]) -> bool:
    rating = _safe_float(item.get("rating"))
    if rating is None:
        return True
    count = _safe_float(item.get("rating_count") or item.get("review_count"))
    if rating < RATING_DROP_THRESHOLD and (count is None or count >= RATING_STRICT_COUNT):
        return False
    return True


def _ensure_suitability_fields(item: Dict[str, Any]) -> None:
    for key in ("parent_friendly", "friend_friendly", "couple_friendly", "child_friendly", "accessible"):
        if key not in item or item.get(key) is None:
            # default to True for safety; can be tightened in later versions
            item[key] = True


def _ensure_opening_hours(item: Dict[str, Any]) -> None:
    open_time = item.get("open_time")
    close_time = item.get("close_time")
    is_all_day = item.get("is_all_day")

    def _valid_time(value: Any) -> bool:
        if not isinstance(value, str) or ":" not in value:
            return False
        parts = value.split(":", 1)
        if len(parts) != 2:
            return False
        try:
            hh = int(parts[0])
            mm = int(parts[1])
        except ValueError:
            return False
        return 0 <= hh <= 23 and 0 <= mm <= 59

    if isinstance(is_all_day, bool) and is_all_day:
        return

    if _valid_time(open_time) and _valid_time(close_time):
        item["open_time"] = open_time
        item["close_time"] = close_time
        item.setdefault("is_all_day", False)
        return

    # fallback defaults
    kind = item.get("kind")
    if kind == "restaurant":
        item["open_time"] = "10:00"
        item["close_time"] = "21:00"
    else:
        item["open_time"] = "09:00"
        item["close_time"] = "21:00"
    item["is_all_day"] = False
    item.setdefault("opening_hours_type", "default_rule")


def _origin_cluster_hint(origin_text: str) -> str | None:
    text = str(origin_text or "")
    for cluster, keywords in (
        (CLUSTER_CITY_WALL, ("钟楼", "鼓楼", "回民街", "城墙", "南门")),
        (CLUSTER_XIAOZHAI, ("小寨", "陕西历史博物馆", "陕历博")),
        (CLUSTER_DAYANTA, ("大雁塔", "大慈恩寺", "慈恩寺")),
        (CLUSTER_QUJIANG, ("曲江", "大唐不夜城", "大唐芙蓉园")),
    ):
        if any(keyword in text for keyword in keywords):
            return cluster
    return None


def _nearby_distance_ok(item: Dict[str, Any], origin_cluster_hint: str | None) -> bool:
    if not origin_cluster_hint or item.get("district_cluster") != origin_cluster_hint:
        return True
    center = CLUSTER_CENTERS.get(origin_cluster_hint)
    lon = _safe_float(item.get("longitude"))
    lat = _safe_float(item.get("latitude"))
    if center is None or lon is None or lat is None:
        return True
    distance = _haversine_meters((center[0], center[1]), (lon, lat))
    return distance <= NEARBY_ORIGIN_MAX_METERS


def _cluster_distance_ok(item: Dict[str, Any]) -> bool:
    """Lightweight cluster radius guard (approximate polygon)."""
    cluster = item.get("district_cluster")
    center = CLUSTER_CENTERS.get(cluster)
    if center is None:
        return True
    lon = _safe_float(item.get("longitude"))
    lat = _safe_float(item.get("latitude"))
    if lon is None or lat is None:
        return True
    max_radius = CLUSTER_MAX_RADIUS_METERS.get(cluster, 6000)
    distance = _haversine_meters((center[0], center[1]), (lon, lat))
    return distance <= max_radius


def filter_candidate_pois(
    pois: List[Dict[str, Any]],
    request_context: Any = None,
) -> List[Dict[str, Any]]:
    """Filter noisy candidate POIs before scoring.

    Placement in chain:
    - after POI loading (real/mock merged)
    - before scoring and scheduling
    """
    removed = Counter()
    normalized: List[Dict[str, Any]] = []
    origin_cluster_hint = None
    if request_context is not None and getattr(request_context, "origin_preference_mode", None) == "nearby":
        origin_cluster_hint = _origin_cluster_hint(getattr(request_context, "origin", ""))

    for poi in pois:
        item = dict(poi)

        kind = str(item.get("kind") or "").strip()
        if kind not in ALLOWED_KINDS:
            removed["invalid_kind"] += 1
            continue
        item["kind"] = kind

        cluster = str(item.get("district_cluster") or "").strip()
        if not cluster or cluster not in FIXED_CLUSTERS:
            removed["invalid_cluster"] += 1
            continue
        item["district_cluster"] = cluster

        district_text = _extract_district_text(item)
        normalized_district = _normalize_core_district(district_text)
        if district_text and normalized_district is None:
            removed["out_of_scope_district"] += 1
            continue
        if normalized_district and normalized_district not in CLUSTER_TO_DISTRICTS.get(cluster, set()):
            removed["cluster_district_mismatch"] += 1
            continue

        name = str(item.get("name") or "").strip()
        if not name:
            removed["missing_name"] += 1
            continue
        if _is_noise_name(name):
            removed["name_noise"] += 1
            continue
        item["name"] = name

        lat = _safe_float(item.get("latitude"))
        lon = _safe_float(item.get("longitude"))
        if lat is None or lon is None:
            removed["invalid_coordinate"] += 1
            continue
        if not (XIANG_CORE_LAT_RANGE[0] <= lat <= XIANG_CORE_LAT_RANGE[1]):
            removed["out_of_bounds_lat"] += 1
            continue
        if not (XIANG_CORE_LON_RANGE[0] <= lon <= XIANG_CORE_LON_RANGE[1]):
            removed["out_of_bounds_lon"] += 1
            continue
        item["latitude"] = lat
        item["longitude"] = lon

        if not _ensure_category(item):
            removed["missing_category"] += 1
            continue

        if kind == "restaurant" and not _restaurant_quality_ok(item):
            removed["restaurant_low_quality"] += 1
            continue
        if kind == "sight" and not _sight_quality_ok(item):
            removed["sight_low_quality"] += 1
            continue
        if not _rating_quality_ok(item):
            removed["rating_low"] += 1
            continue
        if not _cluster_distance_ok(item):
            removed["cluster_radius_out_of_range"] += 1
            continue
        if not _nearby_distance_ok(item, origin_cluster_hint):
            removed["nearby_out_of_range"] += 1
            continue

        _ensure_suitability_fields(item)
        _ensure_opening_hours(item)

        normalized.append(item)

    deduped = _apply_dedup(normalized, removed)
    filtered = _apply_restaurant_chain_limit(deduped, removed)

    stats: Dict[str, Any] = {
        "input_count": len(pois),
        "output_count": len(filtered),
        "removed_total": len(pois) - len(filtered),
        "removed_by_reason": dict(sorted(removed.items(), key=lambda x: x[0])),
    }
    global _LAST_FILTER_STATS
    _LAST_FILTER_STATS = stats

    if os.getenv("POI_FILTER_DEBUG", "").strip() == "1":
        print(f"[poi_filter] {stats}")

    return filtered


def get_last_filter_stats() -> Dict[str, Any]:
    return dict(_LAST_FILTER_STATS)
