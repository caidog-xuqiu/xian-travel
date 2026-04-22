from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.services.amap_client import (
    geocode_address,
    load_valid_amap_api_key,
    search_poi_by_keyword,
    search_poi_nearby,
)
from app.services.data_loader import load_pois
from app.services.poi_service import _map_raw_poi

SOURCE_EXISTING = "existing_poi_pipeline"
SOURCE_LOCAL_EXTENDED = "local_extended_corpus"
SOURCE_AMAP_WEB = "amap_web_search"

LOCAL_EXTENDED_FILE = Path(__file__).resolve().parent.parent / "data" / "extended_places.json"

SOURCE_PRIORITY = {
    SOURCE_AMAP_WEB: 3,
    SOURCE_EXISTING: 2,
    SOURCE_LOCAL_EXTENDED: 1,
}

_RESTAURANT_KEYWORDS = {
    "餐",
    "饭",
    "火锅",
    "咖啡",
    "甜品",
    "小吃",
    "food",
    "restaurant",
    "cafe",
    "bbq",
    "烧烤",
}

_NIGHT_KEYWORDS = {"night", "夜", "不夜城", "曲江", "夜景"}
_MUSEUM_KEYWORDS = {"museum", "博物馆", "文博", "展馆"}
_CLASSIC_KEYWORDS = {"classic", "landmark", "钟楼", "鼓楼", "城墙", "大雁塔"}
_PARK_KEYWORDS = {"park", "garden", "公园", "湿地", "植物园", "森林公园", "曲江池", "芙蓉园", "绿道"}

_FOOD_QUERY_HINTS = ["回民街 餐厅", "小寨 餐厅", "大雁塔 餐厅", "曲江 餐厅"]
_SIGHT_QUERY_HINTS = ["钟楼", "鼓楼", "城墙", "大雁塔", "博物馆", "景点"]
_NIGHT_QUERY_HINTS = ["大唐不夜城", "曲江 夜景", "夜游"]
_INDOOR_QUERY_HINTS = ["博物馆", "展馆", "室内景点"]
_PARK_QUERY_HINTS = ["西安 公园", "西安 城市公园", "西安 湿地公园", "曲江池", "大唐芙蓉园"]
_BBQ_QUERY_HINTS = ["西安 烧烤", "西安 烤串", "西安 夜宵"]

_QUERY_ALIAS_MAP = {
    "钟楼": "钟鼓楼",
    "回民街": "回民街 小吃",
    "大雁塔": "大雁塔 景区",
    "曲江": "曲江 夜景",
    "小寨": "小寨 商圈",
}
_ASCII_ALIAS_MAP = {
    "钟楼": "Bell Tower",
    "鼓楼": "Drum Tower",
    "回民街": "Muslim Street",
    "大雁塔": "Giant Wild Goose Pagoda",
    "曲江": "Qujiang",
    "小寨": "Xiaozhai",
    "博物馆": "Museum",
    "景点": "Scenic Spot",
    "餐厅": "Restaurant",
}


def list_discovery_sources() -> List[str]:
    return [SOURCE_EXISTING, SOURCE_LOCAL_EXTENDED, SOURCE_AMAP_WEB]


def _normalize_name(text: str) -> str:
    return "".join(str(text or "").strip().lower().split())


def _dedupe_key(poi: Dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(poi.get("kind", "")),
        str(poi.get("district_cluster", "")),
        _normalize_name(str(poi.get("name", ""))),
    )


def _annotate_source(pois: List[Dict[str, Any]], source_name: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for poi in pois:
        item = dict(poi)
        source_tags = [str(x) for x in (item.get("discovery_source_tags") or []) if str(x)]
        if source_name not in source_tags:
            source_tags.append(source_name)
        item["discovery_source_tags"] = source_tags
        item["discovery_primary_source"] = source_name
        items.append(item)
    return items


def _load_local_extended_corpus() -> List[Dict[str, Any]]:
    if not LOCAL_EXTENDED_FILE.exists():
        return []
    payload = json.loads(LOCAL_EXTENDED_FILE.read_text(encoding="utf-8-sig"))
    places = payload.get("places", [])
    if not isinstance(places, list):
        return []
    return [dict(item) for item in places if isinstance(item, dict)]


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = str(text or "").lower()
    return any(str(word or "").lower() in lowered for word in keywords)


def _filter_local_extended(
    pois: List[Dict[str, Any]],
    query: str,
    strategies: List[str],
) -> List[Dict[str, Any]]:
    query_text = str(query or "").strip().lower()
    lowered = [str(x).strip().lower() for x in strategies if str(x).strip()]
    if not query_text and not lowered:
        return pois

    result: List[Dict[str, Any]] = []
    for poi in pois:
        text = " ".join(
            [
                str(poi.get("name", "")),
                str(poi.get("category", "")),
                " ".join(str(tag) for tag in poi.get("tags", [])),
                str(poi.get("district_cluster", "")),
                str(poi.get("area_name", "")),
            ]
        ).lower()
        keep = False
        if query_text and query_text in text:
            keep = True
        if not keep and "food" in lowered and poi.get("kind") == "restaurant":
            keep = True
        if not keep and "night" in lowered and _contains_any(text, _NIGHT_KEYWORDS):
            keep = True
        if not keep and "indoor" in lowered and poi.get("indoor_or_outdoor") == "indoor":
            keep = True
        if not keep and "museum" in lowered and _contains_any(text, _MUSEUM_KEYWORDS):
            keep = True
        if not keep and "classic" in lowered and _contains_any(text, _CLASSIC_KEYWORDS):
            keep = True
        if not keep and "park" in lowered and _contains_any(text, _PARK_KEYWORDS):
            keep = True
        if keep:
            result.append(poi)

    return result or pois


def _guess_kind_from_keyword(keyword: str) -> str:
    text = str(keyword or "").lower()
    if _contains_any(text, _RESTAURANT_KEYWORDS):
        return "restaurant"
    return "sight"


def _build_amap_keywords(
    query: str,
    strategies: List[str],
    need_meal: bool,
    extra_keywords: List[str] | None = None,
) -> List[str]:
    ordered: List[str] = []

    def _append_many(values: Iterable[str]) -> None:
        for value in values:
            text = str(value or "").strip()
            if text and text not in ordered:
                ordered.append(text)

    lowered = {str(x).strip().lower() for x in strategies}
    park_mode = "park" in lowered

    if query:
        _append_many([query])
    _append_many(extra_keywords or [])

    if "night" in lowered:
        _append_many(_NIGHT_QUERY_HINTS)
    if park_mode:
        _append_many(_PARK_QUERY_HINTS)

    if "food" in lowered:
        _append_many(_FOOD_QUERY_HINTS)
    elif need_meal and not park_mode:
        _append_many(_FOOD_QUERY_HINTS[:2])

    if "museum" in lowered:
        _append_many(["陕西历史博物馆", "博物馆", "文博"])
    if "classic" in lowered or "landmark" in lowered:
        _append_many(_SIGHT_QUERY_HINTS)
    if "indoor" in lowered:
        _append_many(_INDOOR_QUERY_HINTS)

    if _contains_any(" ".join(extra_keywords or []), {"烧烤", "烤串", "bbq"}):
        _append_many(_BBQ_QUERY_HINTS)

    if park_mode and need_meal:
        _append_many(["公园 附近 餐厅"])

    if not ordered:
        _append_many(_SIGHT_QUERY_HINTS + _FOOD_QUERY_HINTS)
    return ordered[:10]


def _simplify_query(text: str) -> str:
    simplified = str(text or "").strip()
    for word in ("附近", "周边", "这边", "出发", "开始", "安排", "路线"):
        simplified = simplified.replace(word, "")
    for src, tgt in _QUERY_ALIAS_MAP.items():
        if src in simplified:
            simplified = simplified.replace(src, tgt)
    return simplified.strip()


def _ascii_alias_query(text: str) -> str:
    aliased = str(text or "").strip()
    for src, tgt in _ASCII_ALIAS_MAP.items():
        if src in aliased:
            aliased = aliased.replace(src, tgt)
    return aliased.strip()


def _parse_amap_location(text: str | None) -> tuple[float, float] | None:
    if not isinstance(text, str):
        return None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) != 2:
        return None
    try:
        lng = float(parts[0])
        lat = float(parts[1])
    except (TypeError, ValueError):
        return None
    return lat, lng


def _extract_geocode_lat_lng(geo_debug: Dict[str, Any]) -> tuple[float, float] | None:
    if not isinstance(geo_debug, dict) or not geo_debug.get("ok"):
        return None
    payload = geo_debug.get("result")
    if not isinstance(payload, dict):
        return None
    geocodes = payload.get("geocodes") or []
    if not isinstance(geocodes, list) or not geocodes:
        return None
    first = geocodes[0] if isinstance(geocodes[0], dict) else {}
    return _parse_amap_location(first.get("location"))


def _build_query_levels(
    query: str,
    strategies: List[str],
    need_meal: bool,
    extra_keywords: List[str] | None = None,
) -> List[List[str]]:
    level1 = _build_amap_keywords(query, strategies, need_meal, extra_keywords=extra_keywords)
    level2: List[str] = []
    for item in level1:
        simplified = _simplify_query(item)
        if simplified and simplified not in level2:
            level2.append(simplified)
    level3: List[str] = []
    for item in level2 or level1:
        aliased = _ascii_alias_query(item)
        if aliased and aliased not in level3:
            level3.append(aliased)

    levels: List[List[str]] = []
    for level in (level1, level2, level3):
        if level:
            levels.append(level)
    return levels


def _map_amap_raw(raw: Dict[str, Any], preferred_kind: str) -> Dict[str, Any] | None:
    mapped = _map_raw_poi(raw, kind=preferred_kind)
    if mapped is not None:
        return mapped
    alt_kind = "restaurant" if preferred_kind == "sight" else "sight"
    return _map_raw_poi(raw, kind=alt_kind)


def _load_from_amap_web_search(
    query: str,
    context: Dict[str, Any],
    source_meta: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    key, key_error = load_valid_amap_api_key("AMAP_API_KEY")
    if not key:
        if source_meta is not None:
            source_meta["fallback_reason"] = key_error or "missing_api_key"
            source_meta["query_count"] = 0
            source_meta["search_mode"] = "fallback_local"
        return []

    request_context = context.get("request_context") or context.get("parsed_request")
    strategies = list(context.get("primary_strategies") or []) + list(context.get("secondary_strategies") or [])
    area_scope = context.get("area_scope")
    if area_scope is None:
        area_scope_info = context.get("area_scope_info")
        if isinstance(area_scope_info, dict):
            area_scope = area_scope_info.get("areas") or []

    need_meal = bool(getattr(request_context, "need_meal", False))
    extra_keywords = [str(x) for x in (context.get("demand_keywords") or []) if str(x)]
    keyword_levels = _build_query_levels(str(query or ""), strategies, need_meal, extra_keywords=extra_keywords)

    if source_meta is not None:
        source_meta["query_count"] = sum(len(level) for level in keyword_levels)
        source_meta["search_mode"] = "keyword"
        source_meta["fallback_reason"] = None
        source_meta["query_level"] = None
        source_meta["amap_status"] = None
        source_meta["amap_info"] = None
        source_meta["amap_infocode"] = None

    merged_raw: List[Dict[str, Any]] = []
    nearby_used = False
    origin_mode = str(getattr(request_context, "origin_preference_mode", "") or "")
    origin_text = str(getattr(request_context, "origin", "") or "")

    if origin_mode == "nearby" and origin_text:
        try:
            lat_value = getattr(request_context, "origin_latitude", None)
            lng_value = getattr(request_context, "origin_longitude", None)
            if isinstance(lat_value, (int, float)) and isinstance(lng_value, (int, float)):
                lat = float(lat_value)
                lng = float(lng_value)
            else:
                geo_debug = geocode_address(origin_text, debug=True)
                if not geo_debug.get("ok"):
                    raise RuntimeError("geocode_failed")
                coords = _extract_geocode_lat_lng(geo_debug)
                if coords is None:
                    raise RuntimeError("geocode_invalid_location")
                lat, lng = coords

            nearby_used = True
            if source_meta is not None:
                source_meta["search_mode"] = "nearby"

            for level_index, keywords in enumerate(keyword_levels or [[]], start=1):
                for keyword in keywords[:3]:
                    debug_payload = search_poi_nearby(
                        lat=lat,
                        lng=lng,
                        keyword=keyword,
                        radius=3500,
                        limit=12,
                        debug=True,
                    )
                    if isinstance(debug_payload, list):
                        merged_raw.extend([item for item in debug_payload if isinstance(item, dict)])
                    else:
                        merged_raw.extend(debug_payload.get("result") or [])
                        if source_meta is not None:
                            source_meta["amap_status"] = debug_payload.get("amap_status")
                            source_meta["amap_info"] = debug_payload.get("amap_info")
                            source_meta["amap_infocode"] = debug_payload.get("amap_infocode")
                            source_meta["exception_type"] = debug_payload.get("exception_type")
                            source_meta["exception_message"] = debug_payload.get("exception_message")
                            source_meta["request_url"] = debug_payload.get("request_url")
                            source_meta["timeout_seconds"] = debug_payload.get("timeout_seconds")
                            source_meta["proxy_mode"] = debug_payload.get("proxy_mode")
                            source_meta["env_proxy_snapshot"] = debug_payload.get("env_proxy_snapshot")
                if merged_raw:
                    if source_meta is not None:
                        source_meta["query_level"] = f"level{level_index}"
                    break
        except Exception as exc:
            nearby_used = False
            if source_meta is not None:
                source_meta["fallback_reason"] = f"nearby_failed:{exc}"

    if not nearby_used:
        for level_index, keywords in enumerate(keyword_levels or [[]], start=1):
            for keyword in keywords:
                try:
                    debug_payload = search_poi_by_keyword(
                        keyword=keyword,
                        area_scope=area_scope,
                        limit=20,
                        debug=True,
                    )
                    merged_raw.extend(debug_payload.get("result") or [])
                    if source_meta is not None:
                        source_meta["amap_status"] = debug_payload.get("amap_status")
                        source_meta["amap_info"] = debug_payload.get("amap_info")
                        source_meta["amap_infocode"] = debug_payload.get("amap_infocode")
                        source_meta["exception_type"] = debug_payload.get("exception_type")
                        source_meta["exception_message"] = debug_payload.get("exception_message")
                        source_meta["request_url"] = debug_payload.get("request_url")
                        source_meta["timeout_seconds"] = debug_payload.get("timeout_seconds")
                        source_meta["proxy_mode"] = debug_payload.get("proxy_mode")
                        source_meta["env_proxy_snapshot"] = debug_payload.get("env_proxy_snapshot")
                except Exception as exc:
                    if source_meta is not None:
                        source_meta["fallback_reason"] = f"keyword_failed:{exc}"
                    continue
            if merged_raw:
                if source_meta is not None:
                    source_meta["query_level"] = f"level{level_index}"
                break

    mapped: List[Dict[str, Any]] = []
    seen_ids = set()
    for raw in merged_raw:
        if not isinstance(raw, dict):
            continue
        raw_id = str(raw.get("id") or "")
        if raw_id and raw_id in seen_ids:
            continue
        if raw_id:
            seen_ids.add(raw_id)
        preferred_kind = _guess_kind_from_keyword(str(raw.get("type") or raw.get("name") or ""))
        mapped_item = _map_amap_raw(raw, preferred_kind=preferred_kind)
        if mapped_item is None:
            continue
        mapped.append(mapped_item)

    if source_meta is not None:
        source_meta["raw_result_count"] = len(merged_raw)
        source_meta["mapped_result_count"] = len(mapped)

    return mapped


def load_candidates_from_source(
    source_name: str,
    query: str,
    context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    context = context or {}
    request_context = context.get("request_context") or context.get("parsed_request")
    strategies = list(context.get("primary_strategies") or []) + list(context.get("secondary_strategies") or [])

    if source_name == SOURCE_EXISTING:
        base_pois = context.get("base_pois")
        pois = list(base_pois) if base_pois else load_pois(request_context=request_context)
        return _annotate_source(pois, SOURCE_EXISTING)

    if source_name == SOURCE_LOCAL_EXTENDED:
        pois = _load_local_extended_corpus()
        pois = _filter_local_extended(pois, query=query, strategies=strategies)
        return _annotate_source(pois, SOURCE_LOCAL_EXTENDED)

    if source_name == SOURCE_AMAP_WEB:
        source_meta = context.setdefault("source_meta", {})
        amap_meta = source_meta.setdefault(SOURCE_AMAP_WEB, {})
        if not isinstance(amap_meta, dict):
            amap_meta = {}
            source_meta[SOURCE_AMAP_WEB] = amap_meta
        pois = _load_from_amap_web_search(query=query, context=context, source_meta=amap_meta)
        return _annotate_source(pois, SOURCE_AMAP_WEB)

    return []


def merge_discovery_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    source_counts: Dict[str, int] = {}
    deduped: Dict[tuple[str, str, str], Dict[str, Any]] = {}

    for item in results:
        source_name = str(item.get("source") or "")
        pois = item.get("pois") or []
        if not isinstance(pois, list):
            continue
        source_counts[source_name] = source_counts.get(source_name, 0) + len(pois)

        for poi in pois:
            if not isinstance(poi, dict):
                continue
            key = _dedupe_key(poi)
            existed = deduped.get(key)
            if existed is None:
                deduped[key] = dict(poi)
                continue

            incoming_source = str(poi.get("discovery_primary_source") or source_name)
            existing_source = str(existed.get("discovery_primary_source") or "")
            incoming_priority = SOURCE_PRIORITY.get(incoming_source, 0)
            existing_priority = SOURCE_PRIORITY.get(existing_source, 0)

            merged_tags = list(
                dict.fromkeys(
                    [str(x) for x in (existed.get("discovery_source_tags") or []) if str(x)]
                    + [str(x) for x in (poi.get("discovery_source_tags") or []) if str(x)]
                )
            )

            if incoming_priority > existing_priority:
                replacement = dict(poi)
                replacement["discovery_source_tags"] = merged_tags
                deduped[key] = replacement
            else:
                existed["discovery_source_tags"] = merged_tags

    merged_pois = list(deduped.values())
    total_before = sum(source_counts.values())
    return {
        "merged_pois": merged_pois,
        "source_counts": source_counts,
        "total_before_merge": total_before,
        "total_after_merge": len(merged_pois),
        "duplicates_removed": max(0, total_before - len(merged_pois)),
    }
