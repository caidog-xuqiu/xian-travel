from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from app.services.area_registry import (
    get_default_area_scope,
    map_place_to_area,
    resolve_area_scope_from_request,
)
from app.services.data_quality import govern_candidate_pool
from app.services.discovery_sources import (
    SOURCE_AMAP_WEB,
    SOURCE_EXISTING,
    SOURCE_LOCAL_EXTENDED,
    list_discovery_sources,
    load_candidates_from_source,
    merge_discovery_results,
)

ORIGIN_CLUSTER_HINTS: Dict[str, str] = {
    "钟楼": "城墙钟鼓楼簇",
    "鼓楼": "城墙钟鼓楼簇",
    "回民街": "城墙钟鼓楼簇",
    "小寨": "小寨文博簇",
    "大雁塔": "大雁塔簇",
    "曲江": "曲江夜游簇",
}

DISCOVERY_SOURCE_PRIORITY = {
    SOURCE_EXISTING: 2,
    SOURCE_LOCAL_EXTENDED: 1,
    SOURCE_AMAP_WEB: 3,
    "amap": 2,
    "mock": 1,
    "unknown": 0,
}

_STRATEGY_AREA_HINTS: Dict[str, List[str]] = {
    "night": ["曲江夜游", "大雁塔", "城墙钟鼓楼"],
    "classic": ["城墙钟鼓楼", "小寨文博", "大雁塔"],
    "museum": ["小寨文博", "城墙钟鼓楼", "大雁塔"],
    "landmark": ["城墙钟鼓楼", "大雁塔", "曲江夜游"],
    "food": ["回民街", "小寨文博", "城墙钟鼓楼", "高新"],
    "indoor": ["小寨文博", "高新", "电视塔会展"],
    "relaxed": ["小寨文博", "电视塔会展", "城墙钟鼓楼"],
    "park": ["曲江夜游", "大雁塔", "小寨文博", "浐灞未央"],
    "nearby": [],
}

_PARK_TEXT_HINTS = ("公园", "湿地", "植物园", "森林公园", "曲江池", "芙蓉园", "绿道", "park", "garden")


@dataclass
class DiscoveryResult:
    """Candidate discovery output (discovery layer only)."""

    discovered_pois: List[Dict[str, Any]] = field(default_factory=list)
    discovery_sources: List[str] = field(default_factory=list)
    discovered_source_counts: Dict[str, int] = field(default_factory=dict)
    discovery_notes: List[str] = field(default_factory=list)
    coverage_summary: Dict[str, Any] = field(default_factory=dict)
    area_scope_used: List[str] = field(default_factory=list)
    area_priority_order: List[str] = field(default_factory=list)
    discovered_area_counts: Dict[str, int] = field(default_factory=dict)
    area_coverage_summary: Dict[str, Any] = field(default_factory=dict)


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = str(text or "").lower()
    return any(str(keyword or "").lower() in lowered for keyword in keywords)


def _origin_cluster_hint(origin_text: str) -> str | None:
    text = str(origin_text or "")
    for anchor, cluster in ORIGIN_CLUSTER_HINTS.items():
        if anchor in text:
            return cluster
    return None


def _strategy_score(
    poi: Dict[str, Any],
    query: str,
    strategies: List[str],
    origin_cluster_hint: str | None,
) -> int:
    score = 0
    text = " ".join(
        [
            str(poi.get("name", "")),
            str(poi.get("category", "")),
            " ".join(str(tag) for tag in poi.get("tags", [])),
            str(poi.get("district_cluster", "")),
            query,
        ]
    )
    kind = str(poi.get("kind", ""))
    indoor = str(poi.get("indoor_or_outdoor", ""))
    cluster = str(poi.get("district_cluster", ""))

    if "food" in strategies and kind == "restaurant":
        score += 4
    if "night" in strategies and _contains_any(text, ["夜", "不夜城", "曲江", "夜景"]):
        score += 4
    if "indoor" in strategies and indoor == "indoor":
        score += 3
    if "classic" in strategies and _contains_any(text, ["钟楼", "鼓楼", "城墙", "大雁塔"]):
        score += 3
    if "museum" in strategies and _contains_any(text, ["博物馆", "文博", "展馆"]):
        score += 3
    if "landmark" in strategies and _contains_any(text, ["地标", "塔", "城墙", "钟楼"]):
        score += 2
    if "nearby" in strategies:
        if origin_cluster_hint and cluster == origin_cluster_hint:
            score += 4
        elif _contains_any(text, ["钟楼", "小寨", "大雁塔", "曲江", "回民街"]):
            score += 1
    if "relaxed" in strategies and str(poi.get("walking_level", "")) != "high":
        score += 2
    if "park" in strategies:
        if _contains_any(text, _PARK_TEXT_HINTS):
            score += 4
        elif kind == "sight":
            score += 1

    primary_source = str(poi.get("discovery_primary_source", ""))
    if primary_source == SOURCE_EXISTING:
        score += 1
    return score


def _resolve_area_priority(
    *,
    scope_info: Dict[str, Any],
    strategies: List[str],
) -> List[str]:
    scope = list(scope_info.get("areas") or get_default_area_scope())
    if not scope:
        scope = get_default_area_scope()

    ordered: List[str] = []

    for area in scope_info.get("priority_areas") or []:
        if area in scope and area not in ordered:
            ordered.append(area)

    for strategy in strategies:
        for area in _STRATEGY_AREA_HINTS.get(strategy, []):
            if area in scope and area not in ordered:
                ordered.append(area)

    for area in scope:
        if area not in ordered:
            ordered.append(area)
    return ordered


def _apply_filters(
    pois: List[Dict[str, Any]],
    filters: Dict[str, Any],
    area_scope_used: List[str],
) -> List[Dict[str, Any]]:
    allowed_kinds = set(filters.get("allowed_kinds") or [])
    allowed_clusters = set(filters.get("allowed_clusters") or [])
    allowed_areas = set(filters.get("allowed_areas") or area_scope_used or [])

    filtered: List[Dict[str, Any]] = []
    for poi in pois:
        if allowed_kinds and poi.get("kind") not in allowed_kinds:
            continue
        if allowed_clusters and poi.get("district_cluster") not in allowed_clusters:
            continue
        area_name = map_place_to_area(poi)
        if allowed_areas and area_name and area_name not in allowed_areas:
            continue
        if area_name:
            poi = dict(poi)
            poi["area_name"] = area_name
        filtered.append(poi)
    return filtered


def _build_area_counts(pois: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for poi in pois:
        area = map_place_to_area(poi) or "unknown"
        counts[area] = counts.get(area, 0) + 1
    return counts


def _build_area_coverage_summary(
    area_scope_used: List[str],
    area_priority_order: List[str],
    area_counts: Dict[str, int],
) -> Dict[str, Any]:
    active_areas = [area for area, count in area_counts.items() if count > 0 and area != "unknown"]
    priority_hits = [area for area in area_priority_order if area_counts.get(area, 0) > 0]
    scope_size = max(1, len(area_scope_used))
    coverage_ratio = round(len(active_areas) / scope_size, 4)
    return {
        "scope_size": len(area_scope_used),
        "active_area_count": len(active_areas),
        "coverage_ratio": coverage_ratio,
        "priority_areas_hit": priority_hits[:5],
        "top_areas": sorted(active_areas, key=lambda x: area_counts.get(x, 0), reverse=True)[:5],
        "coverage_ok": len(active_areas) >= 2 or bool(priority_hits),
    }


def _build_coverage_summary(
    pois: List[Dict[str, Any]],
    strategy_list: List[str],
    max_candidates: int,
    source_counts: Dict[str, int],
    merge_result: Dict[str, Any],
    area_scope_used: List[str],
    area_priority_order: List[str],
    discovered_area_counts: Dict[str, int],
) -> Dict[str, Any]:
    kind_counts: Dict[str, int] = {}
    cluster_counts: Dict[str, int] = {}
    for poi in pois:
        kind = str(poi.get("kind", "unknown"))
        cluster = str(poi.get("district_cluster", "unknown"))
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1

    sight_count = int(kind_counts.get("sight", 0))
    restaurant_count = int(kind_counts.get("restaurant", 0))
    coverage_ok = sight_count >= 2 and restaurant_count >= 1

    return {
        "total_discovered": len(pois),
        "max_candidates": max_candidates,
        "kind_counts": kind_counts,
        "cluster_counts": cluster_counts,
        "strategies_applied": strategy_list,
        "coverage_ok": coverage_ok,
        "source_counts": source_counts,
        "sources_called": list(source_counts.keys()),
        "total_before_merge": int(merge_result.get("total_before_merge", 0) or 0),
        "total_after_merge": int(merge_result.get("total_after_merge", 0) or 0),
        "duplicates_removed": int(merge_result.get("duplicates_removed", 0) or 0),
        "area_scope_used": area_scope_used,
        "area_priority_order": area_priority_order,
        "discovered_area_counts": discovered_area_counts,
    }


def _light_quality_governance(merged_pois: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    outcome = govern_candidate_pool(merged_pois, source_priority=DISCOVERY_SOURCE_PRIORITY)
    report = outcome.report.model_dump() if outcome.report and hasattr(outcome.report, "model_dump") else {}
    return list(outcome.usable_pois), report


def discover_candidates(
    query: str,
    context: Dict[str, Any] | None = None,
    limits: Dict[str, Any] | None = None,
    filters: Dict[str, Any] | None = None,
) -> DiscoveryResult:
    """Discover POI candidates from multiple local sources."""

    context = context or {}
    limits = limits or {}
    filters = filters or {}

    request_context = context.get("request_context") or context.get("parsed_request")
    strategy_list = list(
        dict.fromkeys((context.get("primary_strategies") or []) + (context.get("secondary_strategies") or []))
    )
    source_order = context.get("source_order") or list_discovery_sources()
    source_order = [str(x) for x in source_order if str(x)]
    max_candidates = int(limits.get("max_candidates", 24))

    scope_info = context.get("area_scope_info")
    if not isinstance(scope_info, dict):
        scope_info = resolve_area_scope_from_request(request_context, query)
    area_scope_used = list(scope_info.get("areas") or get_default_area_scope())
    area_priority_order = _resolve_area_priority(scope_info=scope_info, strategies=strategy_list)

    source_results: List[Dict[str, Any]] = []
    notes: List[str] = []
    source_counts: Dict[str, int] = {}
    source_meta: Dict[str, Any] = {}
    shared_context = {
        "request_context": request_context,
        "primary_strategies": context.get("primary_strategies") or [],
        "secondary_strategies": context.get("secondary_strategies") or [],
        "base_pois": context.get("base_pois") or [],
        "area_scope_info": scope_info,
        "area_scope": area_scope_used,
        "source_meta": source_meta,
        "user_input": query,
        "demand_keywords": list(context.get("demand_keywords") or []),
    }

    for source_name in source_order:
        try:
            source_pois = load_candidates_from_source(
                source_name=source_name,
                query=query,
                context=shared_context,
            )
        except Exception as exc:
            source_pois = []
            notes.append(f"source={source_name} fallback: {exc}")
        source_results.append({"source": source_name, "pois": list(source_pois)})
        source_counts[source_name] = len(source_pois)

    amap_meta = source_meta.get(SOURCE_AMAP_WEB)
    if isinstance(amap_meta, dict):
        reason = str(amap_meta.get("fallback_reason") or "").strip()
        if reason:
            notes.append(f"source={SOURCE_AMAP_WEB} fallback_reason={reason}")
        mode = str(amap_meta.get("search_mode") or "").strip()
        if mode:
            notes.append(f"source={SOURCE_AMAP_WEB} mode={mode}")
        query_count = int(amap_meta.get("query_count", 0) or 0)
        if query_count:
            notes.append(f"source={SOURCE_AMAP_WEB} query_count={query_count}")

    merge_result = merge_discovery_results(source_results)
    merged = list(merge_result.get("merged_pois") or [])

    governed, quality_report = _light_quality_governance(merged)
    if not governed:
        governed = merged
        notes.append("merged quality weak: fallback to merged raw pool")

    filtered = _apply_filters(governed, filters, area_scope_used=area_scope_used)
    scoped = [poi for poi in filtered if (map_place_to_area(poi) or "unknown") in set(area_scope_used)]
    if scoped:
        filtered = scoped
    else:
        notes.append("area scope produced empty set; fallback to unscoped filtered pool")

    origin_text = ""
    if request_context is not None:
        origin_text = str(getattr(request_context, "origin", ""))
    origin_cluster_hint = _origin_cluster_hint(origin_text)

    area_rank = {area: idx for idx, area in enumerate(area_priority_order)}

    def _sort_key(poi: Dict[str, Any]) -> tuple[int, int]:
        base = _strategy_score(
            poi,
            query=query,
            strategies=strategy_list,
            origin_cluster_hint=origin_cluster_hint,
        )
        area_name = map_place_to_area(poi) or "unknown"
        if area_name in area_rank:
            base += max(0, 8 - area_rank[area_name])
        if area_name == scope_info.get("origin_area"):
            base += 2
        return (base, 0)

    ranked = sorted(filtered, key=_sort_key, reverse=True)
    discovered = ranked[:max_candidates]
    discovered_area_counts = _build_area_counts(discovered)
    area_coverage_summary = _build_area_coverage_summary(
        area_scope_used=area_scope_used,
        area_priority_order=area_priority_order,
        area_counts=discovered_area_counts,
    )

    notes.extend(
        [
            "candidate discovery executed: multi-source merge + light governance + strategy ranking",
            "discovery layer only discovers candidates; final itinerary remains in hard planning chain",
        ]
    )
    if strategy_list:
        notes.append("strategies=" + ", ".join(strategy_list))
    notes.append("area_scope_used=" + ", ".join(area_scope_used))
    notes.append("area_priority_order=" + ", ".join(area_priority_order))
    notes.append(
        "sources="
        + ", ".join([f"{k}:{v}" for k, v in source_counts.items()])
        + f", merged={merge_result.get('total_after_merge', 0)}"
    )
    if quality_report:
        notes.append(
            "merged_quality="
            + f"input:{quality_report.get('total_input', 0)}, "
            + f"quarantined:{quality_report.get('quarantined_count', 0)}"
        )

    coverage = _build_coverage_summary(
        discovered,
        strategy_list,
        max_candidates,
        source_counts,
        merge_result,
        area_scope_used,
        area_priority_order,
        discovered_area_counts,
    )
    coverage["quality_report"] = quality_report
    coverage["area_coverage_summary"] = area_coverage_summary
    coverage["source_meta"] = source_meta

    active_sources = [source for source, count in source_counts.items() if count > 0]
    return DiscoveryResult(
        discovered_pois=discovered,
        discovery_sources=active_sources or source_order,
        discovered_source_counts=source_counts,
        discovery_notes=notes,
        coverage_summary=coverage,
        area_scope_used=area_scope_used,
        area_priority_order=area_priority_order,
        discovered_area_counts=discovered_area_counts,
        area_coverage_summary=area_coverage_summary,
    )
