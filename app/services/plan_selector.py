from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, List, Tuple

from app.models.schemas import ItineraryResponse, PlanRequest, PlanSummary
from app.services.itinerary_renderer import render_itinerary_text
from app.services.knowledge_layer import bundle_to_notes, bundle_to_tags, retrieve_place_knowledge
from app.services.area_registry import map_place_to_area
from app.services.llm_planner import (
    enrich_selection_reason_with_knowledge,
    infer_reason_tags,
    post_check_selected_plan,
    rank_plans_with_constraints,
    select_plan_with_llm,
)
from app.services.planner import generate_itinerary

_ORIGIN_CLUSTER_HINT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("城墙钟鼓楼簇", ("钟楼", "鼓楼", "回民街", "城墙", "南门")),
    ("小寨文博簇", ("小寨", "陕西历史博物馆", "陕历博", "文博")),
    ("大雁塔簇", ("大雁塔", "大慈恩寺", "慈恩寺")),
    ("曲江夜游簇", ("曲江", "大唐不夜城", "大唐芙蓉园", "夜游")),
]

_VARIANT_RETRY_LIMIT = 2
_DEFAULT_AREA_SEQUENCE = ["城墙钟鼓楼", "小寨文博", "大雁塔", "曲江夜游", "回民街", "高新", "电视塔会展", "浐灞未央"]
_CLASSIC_AREA_PREFERENCE = ["城墙钟鼓楼", "小寨文博", "大雁塔", "曲江夜游"]
_NIGHT_AREA_PREFERENCE = ["曲江夜游", "大雁塔", "城墙钟鼓楼"]
_LIVELY_CLUSTER_HINTS = {"城墙钟鼓楼簇", "曲江夜游簇"}
_LOW_COST_LEVELS = {"low"}


def _kb_flag(knowledge_bias: Dict[str, Any] | None, key: str) -> bool:
    return bool((knowledge_bias or {}).get(key))


def _poi_is_lively(poi: Dict[str, Any]) -> bool:
    cluster = str(poi.get("district_cluster") or "")
    name = str(poi.get("name") or "")
    category = str(poi.get("category") or "")
    tags = [str(t) for t in (poi.get("tags") or [])]
    text = f"{name}{category}{' '.join(tags)}"
    return cluster in _LIVELY_CLUSTER_HINTS or any(k in text for k in ("热闹", "夜游", "回民街", "钟楼", "鼓楼", "不夜城", "lively"))


def _unique_in_order(items: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _infer_origin_cluster_hint(origin_text: str) -> str | None:
    for cluster, keywords in _ORIGIN_CLUSTER_HINT_RULES:
        if any(keyword in origin_text for keyword in keywords):
            return cluster
    return None


def _poi_with_area(poi: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(poi)
    area_name = str(item.get("area_name") or "").strip() or map_place_to_area(item) or "unknown"
    item["area_name"] = area_name
    return item


def _infer_origin_area_hint(origin_text: str) -> str | None:
    temp_place = {"name": origin_text, "district_cluster": _infer_origin_cluster_hint(origin_text) or ""}
    mapped = map_place_to_area(temp_place)
    return mapped if mapped and mapped != "unknown" else None


def _route_area_sequence(itinerary: ItineraryResponse) -> List[str]:
    areas: List[str] = []
    for item in itinerary.route:
        area = map_place_to_area({"name": item.name, "district_cluster": item.district_cluster}) or "unknown"
        areas.append(area)
    return _unique_in_order(areas)


def _resolve_area_priority_order(request: PlanRequest, area_context: Dict[str, Any] | None) -> List[str]:
    if area_context is None:
        area_context = {}
    order = [str(x) for x in (area_context.get("area_priority_order") or []) if str(x)]
    scope = [str(x) for x in (area_context.get("area_scope_used") or []) if str(x)]
    origin_area = str(area_context.get("origin_area") or "").strip() or _infer_origin_area_hint(request.origin)

    merged = _unique_in_order(order + scope + _DEFAULT_AREA_SEQUENCE)
    if origin_area and origin_area in merged:
        merged.remove(origin_area)
        merged.insert(0, origin_area)
    return merged


def _build_area_bias_note(
    plan_id: str,
    *,
    areas: List[str],
    area_priority_order: List[str],
    request: PlanRequest,
) -> str:
    if not areas:
        return "区域信息不足，按默认候选生成。"
    if plan_id == "classic_first":
        return "经典优先：优先覆盖文博/地标区域，允许适度跨区域。"
    if plan_id == "relaxed_first":
        return "轻松优先：尽量保持区域集中，减少跨区域移动负担。"
    if plan_id == "food_friendly":
        if request.preferred_period == "evening":
            return "餐饮友好：优先夜游区域与餐饮衔接，餐位尽量放在前中段。"
        return "餐饮友好：优先餐饮密度更高区域，确保顺路用餐衔接。"
    top_area = area_priority_order[0] if area_priority_order else areas[0]
    return f"优先围绕 {top_area} 区域组织路线。"


def _area_fit_score(summary: PlanSummary, request: PlanRequest, plan_id: str) -> int:
    score = 0
    if plan_id == "classic_first":
        # classic can accept moderate cross-area for coverage
        score += 4 if summary.cross_area_count <= 2 else -2
        score += 2 if any(area in _CLASSIC_AREA_PREFERENCE for area in summary.area_transition_summary.split(" -> ")) else 0
    elif plan_id == "relaxed_first":
        score += 8 if summary.cross_area_count == 0 else 0
        score += 4 if summary.cross_area_count <= 1 else -4
    elif plan_id == "food_friendly":
        score += 6 if summary.has_meal else -8
        score += 3 if summary.cross_area_count <= 1 else -3
        if request.preferred_period == "evening":
            score += 4 if "曲江夜游" in summary.area_transition_summary else 0
    return score


def _route_signature(itinerary: ItineraryResponse) -> Tuple[str, ...]:
    return tuple(item.name for item in itinerary.route)


def _dedupe_key(summary: PlanSummary, itinerary: ItineraryResponse) -> Tuple[Any, ...]:
    return (
        _route_signature(itinerary),
        tuple(summary.clusters),
        tuple((summary.area_transition_summary or "").split(" -> ")) if summary.area_transition_summary else (),
        summary.has_meal,
        summary.cross_cluster_count,
        summary.cross_area_count,
        summary.rhythm,
        summary.stop_count,
    )


def _meal_position_tag(itinerary: ItineraryResponse) -> str:
    route = itinerary.route
    if not route:
        return "none"
    meal_index = next((idx for idx, item in enumerate(route) if item.type == "restaurant"), None)
    if meal_index is None:
        return "none"
    if meal_index == 0:
        return "front"
    if meal_index >= len(route) - 1:
        return "end"
    if meal_index <= max(1, len(route) // 2):
        return "mid_front"
    return "mid_end"


def _candidate_strength(summary: PlanSummary, request: PlanRequest) -> int:
    score = summary.stop_count
    if summary.has_meal:
        score += 1
    if summary.purpose == request.purpose.value:
        score += 1
    if summary.walking_tolerance == request.walking_tolerance.value:
        score += 1
    if summary.stop_count == 0:
        score -= 3

    if request.companion_type.value == "parents" or request.walking_tolerance.value == "low":
        score += max(0, 2 - int(summary.cross_cluster_count))
        if summary.rhythm in {"轻松", "relaxed"}:
            score += 1

    if request.need_meal and summary.has_meal:
        score += 1

    return score


def _plan_variant_bias(plan_id: str, summary: PlanSummary) -> int:
    """候选结构偏置，让 classic / relaxed / food 的角色更稳定。"""
    score = 0
    if plan_id == "classic_first":
        if "classic" in summary.bias_tags:
            score += 2
        if summary.purpose == "tourism":
            score += 2
        if summary.stop_count >= 3:
            score += 1
    elif plan_id == "relaxed_first":
        if summary.rhythm in {"轻松", "relaxed"}:
            score += 2
        score += max(0, 2 - int(summary.cross_cluster_count))
        if not summary.is_cross_cluster:
            score += 1
    elif plan_id == "food_friendly":
        if summary.has_meal:
            score += 2
        if "food" in summary.bias_tags:
            score += 2
        if "餐位:前段" in summary.diff_points or "餐位:中前段" in summary.diff_points:
            score += 1
    return score


def _build_diff_points(
    itinerary: ItineraryResponse,
    clusters: List[str],
    areas: List[str],
    has_meal: bool,
    rhythm: str,
    cross_cluster_count: int,
    cross_area_count: int,
) -> List[str]:
    diff_points: List[str] = []
    if has_meal:
        meal_pos = _meal_position_tag(itinerary)
        meal_pos_text = {
            "front": "前段",
            "mid_front": "中前段",
            "mid_end": "中后段",
            "end": "后段",
            "none": "未安排",
        }.get(meal_pos, "中段")
        diff_points.append(f"餐位:{meal_pos_text}")
    else:
        diff_points.append("无餐饮站")
    if clusters:
        diff_points.append("簇分布:" + " / ".join(clusters))
    if areas:
        diff_points.append("区域分布:" + " / ".join(areas))
    diff_points.append(f"跨簇:{cross_cluster_count}")
    diff_points.append(f"跨区域:{cross_area_count}")
    if cross_area_count == 0:
        diff_points.append("单区域更轻松")
    elif cross_area_count <= 2:
        diff_points.append("区域覆盖与移动负担均衡")
    else:
        diff_points.append("区域跨度较大")
    diff_points.append(f"节奏:{rhythm}")
    diff_points.append(f"站点:{len(itinerary.route)}")
    return diff_points


def _build_summary_knowledge(
    itinerary: ItineraryResponse,
    request: PlanRequest,
    clusters: List[str],
) -> tuple[List[str], List[str], str | None]:
    query = " ".join([item.name for item in itinerary.route] + clusters)
    context = {
        "preferred_period": request.preferred_period,
        "purpose": request.purpose.value if hasattr(request.purpose, "value") else str(request.purpose),
        "cluster": clusters[0] if clusters else "",
        "tags": [item.type for item in itinerary.route],
    }
    bundle = retrieve_place_knowledge(query=query, context=context)
    knowledge_tags = bundle_to_tags(bundle)
    knowledge_notes = bundle_to_notes(bundle, limit=2)
    place_context_note = knowledge_notes[0] if knowledge_notes else None
    return knowledge_tags, knowledge_notes, place_context_note


def _summarize_plan(
    plan_id: str,
    itinerary: ItineraryResponse,
    request: PlanRequest,
    note: str,
    area_context: Dict[str, Any] | None = None,
) -> PlanSummary:
    clusters = _unique_in_order([item.district_cluster for item in itinerary.route])
    areas = _route_area_sequence(itinerary)
    has_meal = any(item.type == "restaurant" for item in itinerary.route)
    total_distance = sum(item.estimated_distance_meters or 0 for item in itinerary.route)
    total_duration = sum(item.estimated_duration_minutes or 0 for item in itinerary.route)
    stop_count = len(itinerary.route)

    walking_value = request.walking_tolerance.value if hasattr(request.walking_tolerance, "value") else str(
        request.walking_tolerance
    )
    rhythm = "轻松" if walking_value == "low" else "均衡"
    if stop_count >= 4 and walking_value != "low":
        rhythm = "紧凑"

    cross_cluster_count = max(len(clusters) - 1, 0)
    is_cross_cluster = cross_cluster_count > 0
    cluster_transition_summary = " -> ".join(clusters) if clusters else "无有效簇"
    cross_area_count = max(len(areas) - 1, 0)
    is_cross_area = cross_area_count > 0
    area_transition_summary = " -> ".join(areas) if areas else "无有效区域"
    area_priority_order = _resolve_area_priority_order(request, area_context)
    area_bias_note = _build_area_bias_note(
        plan_id,
        areas=areas,
        area_priority_order=area_priority_order,
        request=request,
    )

    diff_points = _build_diff_points(
        itinerary=itinerary,
        clusters=clusters,
        areas=areas,
        has_meal=has_meal,
        rhythm=rhythm,
        cross_cluster_count=cross_cluster_count,
        cross_area_count=cross_area_count,
    )
    knowledge_tags, knowledge_notes, place_context_note = _build_summary_knowledge(
        itinerary=itinerary,
        request=request,
        clusters=clusters,
    )

    if plan_id == "classic_first":
        bias_tags = ["classic", "museum", "landmark"]
        variant_label = "经典优先"
    elif plan_id == "relaxed_first":
        bias_tags = ["relaxed", "low_walk", "fewer_cross_cluster"]
        variant_label = "轻松优先"
    elif plan_id == "food_friendly":
        bias_tags = ["food", "meal_link", "meal_position", "area_food_link"]
        variant_label = "餐饮友好"
    else:
        bias_tags = []
        variant_label = "默认方案"

    return PlanSummary(
        plan_id=plan_id,
        variant_label=variant_label,
        stop_count=stop_count,
        clusters=clusters,
        is_cross_cluster=is_cross_cluster,
        cross_cluster_count=cross_cluster_count,
        cluster_transition_summary=cluster_transition_summary,
        is_cross_area=is_cross_area,
        cross_area_count=cross_area_count,
        area_transition_summary=area_transition_summary,
        area_bias_note=area_bias_note,
        has_meal=has_meal,
        total_distance_meters=int(total_distance),
        total_duration_minutes=int(total_duration),
        rhythm=rhythm,
        budget_level=request.budget_level.value if hasattr(request.budget_level, "value") else str(request.budget_level),
        walking_tolerance=walking_value,
        purpose=request.purpose.value if hasattr(request.purpose, "value") else str(request.purpose),
        diff_points=diff_points,
        bias_tags=bias_tags,
        knowledge_tags=knowledge_tags,
        knowledge_notes=knowledge_notes,
        place_context_note=place_context_note,
        note=note,
    )


def _build_request_variant(
    request: PlanRequest,
    plan_id: str,
    intensity: int = 0,
    knowledge_bias: Dict[str, Any] | None = None,
) -> PlanRequest:
    avoid_too_many_stops = _kb_flag(knowledge_bias, "avoid_too_many_stops")
    prefer_night_view = _kb_flag(knowledge_bias, "prefer_night_view")
    prefer_low_walk = _kb_flag(knowledge_bias, "prefer_low_walk")
    prefer_meal_experience = _kb_flag(knowledge_bias, "prefer_meal_experience")

    if plan_id == "classic_first":
        walking = request.walking_tolerance.value if hasattr(request.walking_tolerance, "value") else str(
            request.walking_tolerance
        )
        if walking == "low" and not prefer_low_walk:
            walking = "medium"
        style = "dense" if request.available_hours >= 8 else "balanced"
        classic_hours = min(max(request.available_hours + 1.5 + 0.5 * float(intensity), 6.5), 9.0)
        if avoid_too_many_stops:
            classic_hours = min(classic_hours, 6.0)
            style = "balanced"
        return request.model_copy(
            update={
                "purpose": "tourism",
                "walking_tolerance": walking,
                "preferred_trip_style": style,
                "preferred_period": "evening" if prefer_night_view and not request.preferred_period else request.preferred_period,
                "available_hours": classic_hours,
            }
        )

    if plan_id == "relaxed_first":
        reduce_hours = 2.8 + 0.8 * float(intensity)
        relaxed_hours = max(2.0, request.available_hours - reduce_hours)
        relaxed_hours = min(relaxed_hours, 3.8 if request.need_meal else 3.0)
        if avoid_too_many_stops:
            relaxed_hours = min(relaxed_hours, 3.2)
        return request.model_copy(
            update={
                "purpose": "relax",
                "walking_tolerance": "low",
                "preferred_trip_style": "relaxed",
                "available_hours": relaxed_hours,
            }
        )

    if plan_id == "food_friendly":
        preferred_period = request.preferred_period or "midday"
        if prefer_night_view:
            preferred_period = "evening"
        food_hours = min(request.available_hours, 4.0 if intensity == 0 else 3.5)
        if prefer_meal_experience:
            food_hours = min(max(food_hours + 0.5, 3.0), 4.5)
        if avoid_too_many_stops:
            food_hours = min(food_hours, 3.3)
        return request.model_copy(
            update={
                "purpose": "food",
                "need_meal": True,
                "preferred_trip_style": "balanced",
                "preferred_period": preferred_period,
                "available_hours": food_hours,
            }
        )
    return request


def _variant_candidate_pois(
    request: PlanRequest,
    plan_id: str,
    candidate_pois: List[Dict[str, Any]] | None,
    area_context: Dict[str, Any] | None = None,
    intensity: int = 0,
    knowledge_bias: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]] | None:
    """按候选类型做轻量 POI 偏置，拉开方案差异。"""
    if not candidate_pois:
        return None

    items = [_poi_with_area(p) for p in candidate_pois]
    prefer_indoor = _kb_flag(knowledge_bias, "prefer_indoor")
    prefer_low_walk = _kb_flag(knowledge_bias, "prefer_low_walk")
    prefer_budget_friendly = _kb_flag(knowledge_bias, "prefer_budget_friendly")
    prefer_lively_places = _kb_flag(knowledge_bias, "prefer_lively_places")
    prefer_single_cluster = _kb_flag(knowledge_bias, "prefer_single_cluster")
    prefer_night_view = _kb_flag(knowledge_bias, "prefer_night_view")

    if prefer_low_walk:
        low_walk_items = [p for p in items if p.get("walking_level") != "high"]
        if low_walk_items:
            items = low_walk_items
    if prefer_indoor:
        indoor_items = [p for p in items if p.get("indoor_or_outdoor") == "indoor"]
        if len(indoor_items) >= 4:
            items = indoor_items + [p for p in items if p.get("indoor_or_outdoor") != "indoor"]
    if prefer_budget_friendly:
        items.sort(
            key=lambda p: (
                0 if str(p.get("cost_level") or "").lower() in _LOW_COST_LEVELS else 1,
                -float(p.get("_score") or 0),
            )
        )
    if prefer_lively_places:
        items.sort(key=lambda p: (1 if _poi_is_lively(p) else 0, float(p.get("_score") or 0)), reverse=True)

    origin_cluster_hint = _infer_origin_cluster_hint(request.origin)
    origin_area_hint = _infer_origin_area_hint(request.origin)
    area_priority_order = _resolve_area_priority_order(request, area_context)

    if plan_id == "classic_first":
        classic_keywords = ("博物馆", "城墙", "钟楼", "鼓楼", "大雁塔", "地标", "文博", "landmark", "museum")
        preferred_classic_areas = _unique_in_order(
            (_NIGHT_AREA_PREFERENCE if prefer_night_view else _CLASSIC_AREA_PREFERENCE) + area_priority_order
        )
        area_rank = {area: idx for idx, area in enumerate(preferred_classic_areas)}
        classic_sights = [
            p
            for p in items
            if p.get("kind") == "sight"
            and (
                any(k in f"{p.get('name', '')}{p.get('category', '')}" for k in classic_keywords)
                or p.get("area_name") in preferred_classic_areas[:4]
            )
        ]
        allowed_areas = set(preferred_classic_areas[: (5 if intensity == 0 else 4)])
        classic_sights = [p for p in classic_sights if p.get("area_name") in allowed_areas] or classic_sights
        classic_sights.sort(
            key=lambda p: (
                area_rank.get(str(p.get("area_name") or ""), 99),
                0 if p.get("district_cluster") == origin_cluster_hint else 1,
                -float(p.get("_score") or 0),
            )
        )
        by_cluster: Dict[str, List[Dict[str, Any]]] = {}
        cluster_order: List[str] = []
        for sight in classic_sights:
            cluster = str(sight.get("district_cluster") or "")
            if not cluster:
                continue
            if cluster not in by_cluster:
                by_cluster[cluster] = []
                cluster_order.append(cluster)
            by_cluster[cluster].append(sight)

        if origin_cluster_hint and origin_cluster_hint in by_cluster and origin_cluster_hint in cluster_order:
            cluster_order.remove(origin_cluster_hint)
            cluster_order.insert(0, origin_cluster_hint)

        per_cluster_cap = 2 if intensity == 0 else 1
        if prefer_single_cluster:
            per_cluster_cap = 1
        curated_sights: List[Dict[str, Any]] = []
        for cluster in cluster_order:
            cluster_items = sorted(by_cluster.get(cluster, []), key=lambda x: float(x.get("_score") or 0), reverse=True)
            curated_sights.extend(cluster_items[:per_cluster_cap])
            if prefer_single_cluster and len(curated_sights) >= 2:
                break

        classic_sights = curated_sights or classic_sights
        clusters = {p.get("district_cluster") for p in classic_sights if p.get("district_cluster")}
        classic_restaurants = [
            p for p in items if p.get("kind") == "restaurant" and p.get("district_cluster") in clusters
        ]
        classic_restaurants = classic_restaurants[:2]
        biased = classic_sights + classic_restaurants
        return biased if len(biased) >= 6 else items

    if plan_id == "relaxed_first":
        relaxed_pool = [p for p in items if p.get("walking_level") != "high"]
        preferred_areas: List[str] = []
        if origin_area_hint:
            preferred_areas.append(origin_area_hint)
        preferred_areas.extend(area_priority_order)
        preferred_areas = _unique_in_order(preferred_areas)
        keep_area_count = 1
        if not prefer_single_cluster and not _kb_flag(knowledge_bias, "avoid_too_many_stops"):
            keep_area_count = 2
        keep_areas = set(preferred_areas[:keep_area_count])
        area_filtered = [p for p in relaxed_pool if p.get("area_name") in keep_areas]
        if area_filtered:
            relaxed_pool = area_filtered
        if origin_cluster_hint:
            same_cluster = [p for p in relaxed_pool if p.get("district_cluster") == origin_cluster_hint]
            if same_cluster:
                relaxed_pool = same_cluster
        relaxed_sights = [p for p in relaxed_pool if p.get("kind") == "sight"][:2]
        relaxed_restaurants = [p for p in relaxed_pool if p.get("kind") == "restaurant"][:1]
        if request.need_meal and relaxed_restaurants:
            relaxed_curated = relaxed_sights[:1] + relaxed_restaurants[:1]
        else:
            relaxed_curated = relaxed_sights[:2]
        return relaxed_curated if relaxed_curated else (relaxed_pool if relaxed_pool else items)

    if plan_id == "food_friendly":
        restaurants = [p for p in items if p.get("kind") == "restaurant"]
        if not restaurants:
            return items
        area_rest_count: Dict[str, int] = {}
        for p in restaurants:
            area = str(p.get("area_name") or "unknown")
            area_rest_count[area] = area_rest_count.get(area, 0) + 1

        area_order = list(area_priority_order)
        if request.preferred_period == "evening" or prefer_night_view:
            area_order = _unique_in_order(_NIGHT_AREA_PREFERENCE + area_order)
        area_order.sort(key=lambda a: area_rest_count.get(a, 0), reverse=True)
        keep_n = 1 if intensity > 0 or prefer_single_cluster else 2
        keep_areas = set(area_order[:keep_n]) if area_order else set()

        restaurant_clusters = _unique_in_order(
            [p.get("district_cluster") for p in restaurants if p.get("district_cluster") and (not keep_areas or p.get("area_name") in keep_areas)]
        )
        if not restaurant_clusters:
            restaurant_clusters = _unique_in_order([p.get("district_cluster") for p in restaurants if p.get("district_cluster")])
        keep_clusters = set(restaurant_clusters[:keep_n]) if restaurant_clusters else set()
        sights_same_clusters = [
            p for p in items if p.get("kind") == "sight" and p.get("district_cluster") in keep_clusters
        ]
        max_sights = 1
        if sights_same_clusters:
            sights_same_clusters = sights_same_clusters[:max_sights]
        restaurants.sort(
            key=lambda p: (
                0 if (not keep_areas or p.get("area_name") in keep_areas) else 1,
                0 if p.get("district_cluster") == origin_cluster_hint else 1,
            )
        )
        restaurant_target = 3 if intensity == 0 else 4
        biased = restaurants[:restaurant_target] + sights_same_clusters
        if len(biased) >= 4:
            return biased
        return restaurants + sights_same_clusters

    return items


def _apply_variant_route_constraints(
    request: PlanRequest,
    plan_id: str,
    itinerary: ItineraryResponse,
    intensity: int,
    knowledge_bias: Dict[str, Any] | None = None,
) -> ItineraryResponse:
    route = list(itinerary.route)
    if not route:
        return itinerary
    avoid_too_many_stops = _kb_flag(knowledge_bias, "avoid_too_many_stops")
    prefer_single_cluster = _kb_flag(knowledge_bias, "prefer_single_cluster")
    prefer_meal_experience = _kb_flag(knowledge_bias, "prefer_meal_experience")

    if prefer_single_cluster and route:
        first_cluster = route[0].district_cluster
        same_cluster_route = [item for item in route if item.district_cluster == first_cluster]
        if len(same_cluster_route) >= 2:
            route = same_cluster_route

    if plan_id == "relaxed_first":
        max_stops = 2
        if avoid_too_many_stops:
            max_stops = 2
        if len(route) <= max_stops:
            return itinerary.model_copy(update={"route": route})
        keep = route[:max_stops]
        if request.need_meal:
            first_sight = next((item for item in route if item.type == "sight"), None)
            first_meal = next((item for item in route if item.type == "restaurant"), None)
            keep = []
            if first_sight is not None:
                keep.append(first_sight)
            if first_meal is not None and first_meal not in keep:
                keep.append(first_meal)
            for item in route:
                if item not in keep and len(keep) < max_stops:
                    keep.append(item)
        order = {id(item): idx for idx, item in enumerate(route)}
        keep.sort(key=lambda item: order.get(id(item), 99))
        return itinerary.model_copy(update={"route": keep})

    if plan_id == "food_friendly":
        meals = [item for item in route if item.type == "restaurant"]
        sights = [item for item in route if item.type == "sight"]
        if not meals:
            return itinerary
        max_sights = 1
        keep = meals[:1] + sights[:max_sights]
        if intensity > 0 and len(meals) > 1:
            keep = meals[:2] + sights[:max_sights]
        if prefer_meal_experience and len(meals) > 1:
            keep = meals[:2] + sights[:max_sights]
        order = {id(item): idx for idx, item in enumerate(route)}
        dedup_keep: List[Any] = []
        for item in keep:
            if item not in dedup_keep:
                dedup_keep.append(item)
        keep = dedup_keep
        keep.sort(key=lambda item: (0 if item.type == "restaurant" else 1, order.get(id(item), 99)))
        return itinerary.model_copy(update={"route": keep})

    if avoid_too_many_stops and len(route) > 3:
        clipped = route[:3]
        return itinerary.model_copy(update={"route": clipped})

    if route != list(itinerary.route):
        return itinerary.model_copy(update={"route": route})

    return itinerary


def _build_candidate_item(
    request: PlanRequest,
    plan_id: str,
    note: str,
    candidate_pois: List[Dict[str, Any]] | None,
    area_context: Dict[str, Any] | None = None,
    intensity: int = 0,
    knowledge_bias: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    variant_request = _build_request_variant(
        request,
        plan_id,
        intensity=intensity,
        knowledge_bias=knowledge_bias,
    )
    variant_pois = _variant_candidate_pois(
        request,
        plan_id,
        candidate_pois,
        area_context=area_context,
        intensity=intensity,
        knowledge_bias=knowledge_bias,
    )
    itinerary = generate_itinerary(variant_request, candidate_pois=variant_pois)
    itinerary = _apply_variant_route_constraints(
        variant_request,
        plan_id,
        itinerary,
        intensity,
        knowledge_bias=knowledge_bias,
    )
    summary = _summarize_plan(plan_id, itinerary, variant_request, note, area_context=area_context)
    return {
        "plan_id": plan_id,
        "request": variant_request,
        "itinerary": itinerary,
        "summary": summary,
    }


def _rebuild_deduped_candidates(all_plans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen_keys = set()
    for plan_item in all_plans:
        summary = plan_item["summary"]
        itinerary = plan_item["itinerary"]
        dedupe_key = _dedupe_key(summary, itinerary)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        candidates.append(plan_item)
    return candidates


def _jaccard_similarity(a: Tuple[str, ...], b: Tuple[str, ...]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def _pair_too_similar(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    sa: PlanSummary = a["summary"]
    sb: PlanSummary = b["summary"]
    route_a = _route_signature(a["itinerary"])
    route_b = _route_signature(b["itinerary"])
    route_close = route_a == route_b or _jaccard_similarity(route_a, route_b) >= 0.8
    cluster_close = tuple(sa.clusters) == tuple(sb.clusters)
    meal_close = sa.has_meal == sb.has_meal
    cross_close = sa.cross_cluster_count == sb.cross_cluster_count
    cross_area_close = sa.cross_area_count == sb.cross_area_count
    rhythm_close = sa.rhythm == sb.rhythm
    area_transition_close = (sa.area_transition_summary or "") == (sb.area_transition_summary or "")
    close_dims = sum([route_close, cluster_close, meal_close, cross_close, cross_area_close, rhythm_close, area_transition_close])
    return close_dims >= 5


def _assess_diversity(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(candidates) < 2:
        return {
            "candidate_count": len(candidates),
            "diversity_insufficient": True,
            "too_similar_pairs": [],
            "reason": "candidate_count_lt_2",
        }

    too_similar_pairs: List[Tuple[str, str]] = []
    for first, second in combinations(candidates, 2):
        if _pair_too_similar(first, second):
            too_similar_pairs.append((first["plan_id"], second["plan_id"]))

    return {
        "candidate_count": len(candidates),
        "diversity_insufficient": len(too_similar_pairs) > 0,
        "too_similar_pairs": too_similar_pairs,
        "reason": "too_similar" if too_similar_pairs else "ok",
    }


def _pick_retry_plan_id(too_similar_pairs: List[Tuple[str, str]]) -> str | None:
    if not too_similar_pairs:
        return None
    first, second = too_similar_pairs[0]
    if "food_friendly" in (first, second):
        return "food_friendly"
    if "relaxed_first" in (first, second):
        return "relaxed_first"
    return second


def generate_candidate_plans(
    request: PlanRequest,
    candidate_pois: List[Dict[str, Any]] | None = None,
    area_context: Dict[str, Any] | None = None,
    quality_feedback: Dict[str, Any] | None = None,
    knowledge_bias: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    plan_defs: List[Tuple[str, str]] = [
        ("classic_first", "偏经典/文博，优先核心点位"),
        ("relaxed_first", "偏轻松少走路，节奏更舒缓"),
    ]
    if request.need_meal:
        plan_defs.append(("food_friendly", "偏顺路餐饮，强调用餐衔接"))

    all_plans: List[Dict[str, Any]] = []
    for plan_id, note in plan_defs:
        all_plans.append(
            _build_candidate_item(
                request=request,
                plan_id=plan_id,
                note=note,
                candidate_pois=candidate_pois,
                area_context=area_context,
                intensity=0,
                knowledge_bias=knowledge_bias,
            )
        )

    retry_count = 0
    last_assessment: Dict[str, Any] = {}
    candidates = _rebuild_deduped_candidates(all_plans)

    for attempt in range(_VARIANT_RETRY_LIMIT + 1):
        candidates = _rebuild_deduped_candidates(all_plans)
        last_assessment = _assess_diversity(candidates)
        if not last_assessment.get("diversity_insufficient"):
            break
        if attempt >= _VARIANT_RETRY_LIMIT:
            break

        retry_plan_id = _pick_retry_plan_id(last_assessment.get("too_similar_pairs", []))
        if not retry_plan_id:
            break

        retry_note = dict(plan_defs).get(retry_plan_id, "重试增强候选")
        for idx, item in enumerate(all_plans):
            if item["plan_id"] == retry_plan_id:
                all_plans[idx] = _build_candidate_item(
                    request=request,
                    plan_id=retry_plan_id,
                    note=retry_note,
                    candidate_pois=candidate_pois,
                    area_context=area_context,
                    intensity=attempt + 1,
                    knowledge_bias=knowledge_bias,
                )
                retry_count += 1
                break

    if len(candidates) < 2:
        # 至少保留 2 条不同 plan_id（哪怕相似），避免选优链直接失效。
        by_plan_id = {item["plan_id"]: item for item in all_plans}
        candidates = [by_plan_id[plan_id] for plan_id, _ in plan_defs if plan_id in by_plan_id]

    # Filter overly weak candidates (no stops), keep at least one.
    filtered = [item for item in candidates if item["summary"].stop_count > 0]
    if filtered:
        candidates = filtered

    # 候选排序：对齐用户需求，同时保留三类候选角色差异。
    candidates.sort(
        key=lambda item: (
            _candidate_strength(item["summary"], request)
            + _plan_variant_bias(item["plan_id"], item["summary"])
            + _area_fit_score(item["summary"], request, item["plan_id"]),
            item["summary"].stop_count,
            -(item["summary"].total_distance_meters or 0),
        ),
        reverse=True,
    )

    if quality_feedback is not None:
        quality_feedback.update(
            {
                "candidate_count": len(candidates),
                "diversity_insufficient": bool(last_assessment.get("diversity_insufficient")),
                "too_similar_pairs": last_assessment.get("too_similar_pairs", []),
                "diversity_retry_count": retry_count,
                "reason": last_assessment.get("reason"),
                "area_scope_used": list((area_context or {}).get("area_scope_used") or []),
            }
        )
    return candidates


def select_best_plan(
    request: PlanRequest,
    candidate_pois: List[Dict[str, Any]] | None = None,
    area_context: Dict[str, Any] | None = None,
    knowledge_bias: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    candidates = generate_candidate_plans(
        request,
        candidate_pois=candidate_pois,
        area_context=area_context,
        knowledge_bias=knowledge_bias,
    )
    if not candidates:
        fallback_itinerary = generate_itinerary(request, candidate_pois=candidate_pois)
        readable = render_itinerary_text(itinerary=fallback_itinerary, request=request)
        return {
            "selected_plan": fallback_itinerary,
            "alternative_plans_summary": [],
            "selection_reason": "无可用候选，已回退默认方案。",
            "reason_tags": ["候选不足", "默认回退"],
            "selected_by": "fallback_rule",
            "readable_output": readable,
        }

    summaries = [item["summary"] for item in candidates]
    llm_choice = select_plan_with_llm(request=request, plan_summaries=summaries)
    ranked_summaries = rank_plans_with_constraints(request, summaries)
    ranked_top_plan_id = ranked_summaries[0].plan_id if ranked_summaries else candidates[0]["plan_id"]

    selected_by = "fallback_rule"
    selection_reason = "LLM 未启用或返回无效结果，已按本地约束排序选择方案。"
    reason_tags = ["本地约束回退"]
    selected_plan_id = ranked_top_plan_id

    if llm_choice:
        selected_plan_id = llm_choice.get("selected_plan_id", selected_plan_id)
        selection_reason = llm_choice.get("selection_reason") or selection_reason
        reason_tags = llm_choice.get("reason_tags") or reason_tags
        selected_by = "llm"
        if selected_plan_id not in {summary.plan_id for summary in summaries}:
            selected_by = "fallback_rule"
            selected_plan_id = ranked_top_plan_id
            selection_reason = "LLM 选择无效编号，已回退到本地约束最优方案。"
            reason_tags = ["编号无效", "本地约束回退"]

    # 选优后复核：保证关键约束不被弱候选破坏。
    post_check = post_check_selected_plan(
        request=request,
        plan_summaries=summaries,
        proposed_plan_id=selected_plan_id,
    )
    final_plan_id = post_check.get("final_plan_id") or selected_plan_id
    if final_plan_id != selected_plan_id and selected_by == "llm":
        selection_reason = f"{selection_reason}；系统复核后改选为 {final_plan_id}，以更贴合核心约束。"
        reason_tags = ["后置复核改选"] + reason_tags
    selected_plan_id = final_plan_id

    selected_candidate = next(
        (item for item in candidates if item["plan_id"] == selected_plan_id),
        None,
    )
    if selected_candidate is None:
        selected_candidate = next((item for item in candidates if item["plan_id"] == ranked_top_plan_id), None)
        if selected_candidate is None:
            selected_candidate = candidates[0]
        selected_by = "fallback_rule"
        selection_reason = "LLM 选择无效编号，已回退到本地约束最优方案。"
        reason_tags = ["编号无效", "本地约束回退"]

    if not reason_tags:
        reason_tags = infer_reason_tags(request, selected_candidate["summary"])

    selection_reason = enrich_selection_reason_with_knowledge(
        selection_reason=selection_reason,
        summary=selected_candidate.get("summary"),
    )

    selected_itinerary = selected_candidate["itinerary"]
    readable = render_itinerary_text(
        itinerary=selected_itinerary,
        request=selected_candidate["request"],
        parsed_request=request,
    )

    return {
        "selected_plan": selected_itinerary,
        "alternative_plans_summary": summaries,
        "selection_reason": selection_reason,
        "reason_tags": list(dict.fromkeys(reason_tags)),
        "selected_by": selected_by,
        "readable_output": readable,
    }
