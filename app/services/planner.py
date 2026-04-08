from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from app.models.schemas import ItineraryResponse, PlanRequest, RouteItem
from app.services.data_loader import load_pois
from app.services.routing import RouteInfo, get_route_info
from app.services.scoring import sort_by_score
from app.services.weather_service import get_weather_context

CROSS_CLUSTER_DURATION_THRESHOLD_MIN = 45
NIGHT_TOUR_CLUSTER = "曲江夜游簇"
PERIOD_START_TIME = {
    "morning": "09:00",
    "midday": "11:30",
    "afternoon": "14:00",
    "evening": "18:00",
}

ORIGIN_CLUSTER_HINT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("城墙钟鼓楼簇", ("钟楼", "鼓楼", "回民街", "城墙", "南门")),
    ("小寨文博簇", ("小寨", "陕历博", "陕西历史博物馆")),
    ("大雁塔簇", ("大雁塔", "大慈恩寺", "慈恩寺")),
    ("曲江夜游簇", ("曲江", "大唐不夜城", "大唐芙蓉园")),
]


def _targets_by_time(request: PlanRequest) -> tuple[int, int]:
    """根据可用时长确定景点与餐饮数量。"""
    if request.available_hours <= 6:
        sights_count = 1 if request.available_hours <= 4 else 2
        meal_count = 1 if request.need_meal else 0
    else:
        sights_count = 3 if request.available_hours < 10 else 4
        if request.need_meal:
            meal_count = 1 if request.available_hours < 11 else 2
        else:
            meal_count = 0
    return sights_count, meal_count


def _group_by_cluster(pois: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for poi in pois:
        grouped[poi["district_cluster"]].append(poi)
    return grouped


def _rank_clusters_by_sights(cluster_to_sights: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    """按簇内景点评分强度排序，用于优先选主簇。"""
    scored_clusters: List[tuple[str, float]] = []
    for cluster, sights in cluster_to_sights.items():
        if not sights:
            continue
        top_scores = [s["_score"] for s in sights[:2]]
        cluster_strength = sum(top_scores) / len(top_scores)
        scored_clusters.append((cluster, cluster_strength))

    scored_clusters.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in scored_clusters]


def _start_time_for_period(period: str | None) -> str:
    if not period:
        return "09:00"
    return PERIOD_START_TIME.get(period, "09:00")


def _preferred_clusters_for_period(period: str | None) -> List[str]:
    if period == "morning":
        return ["小寨文博簇", "城墙钟鼓楼簇"]
    if period == "afternoon":
        return ["大雁塔簇", "城墙钟鼓楼簇"]
    return []


def _apply_period_first_stop_bias(
    ordered_sights: List[Dict[str, Any]],
    preferred_clusters: List[str],
) -> List[Dict[str, Any]]:
    """轻量时间语义偏好：仅调整首站倾向，不做全局重排。"""
    if not ordered_sights or not preferred_clusters:
        return ordered_sights

    for cluster in preferred_clusters:
        for idx, sight in enumerate(ordered_sights):
            if sight.get("district_cluster") == cluster:
                if idx == 0:
                    return ordered_sights
                adjusted = list(ordered_sights)
                adjusted.insert(0, adjusted.pop(idx))
                return adjusted
    return ordered_sights


def _pick_sights(cluster_to_sights: Dict[str, List[Dict[str, Any]]], target: int) -> List[Dict[str, Any]]:
    """景点选择策略:
    - 半天: 尽量单簇
    - 全天: 最多 2 簇，减少跨簇次数
    """
    if target <= 0:
        return []

    for cluster in cluster_to_sights:
        cluster_to_sights[cluster].sort(key=lambda x: x["_score"], reverse=True)

    ranked_clusters = _rank_clusters_by_sights(cluster_to_sights)
    if not ranked_clusters:
        return []

    max_clusters = 1 if target <= 2 else 2
    selected_clusters = ranked_clusters[:max_clusters]

    selected: List[Dict[str, Any]] = []

    # 先从主簇拿
    primary = selected_clusters[0]
    selected.extend(cluster_to_sights.get(primary, [])[:target])

    # 再按次簇补齐
    if len(selected) < target and len(selected_clusters) > 1:
        secondary = selected_clusters[1]
        need = target - len(selected)
        selected.extend(cluster_to_sights.get(secondary, [])[:need])

    # 簇内连续：按簇顺序 + 分数排序
    cluster_index = {c: i for i, c in enumerate(selected_clusters)}
    selected.sort(key=lambda x: (cluster_index.get(x["district_cluster"], 99), -x["_score"]))

    return selected[:target]


def _apply_evening_preference(
    selected_sights: List[Dict[str, Any]],
    cluster_to_sights: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """夜游偏好最小修正。

    - 优先确保曲江夜游簇至少有一个景点进入候选
    - 不做复杂重排，仅用于避免夜游场景候选全是白天点位
    """
    if not selected_sights:
        return selected_sights

    night_candidates = sorted(
        cluster_to_sights.get(NIGHT_TOUR_CLUSTER, []),
        key=lambda x: x.get("_score", 0),
        reverse=True,
    )
    if not night_candidates:
        return selected_sights

    adjusted = list(selected_sights)
    has_night_sight = any(item.get("district_cluster") == NIGHT_TOUR_CLUSTER for item in adjusted)

    if not has_night_sight:
        selected_ids = {item.get("id") for item in adjusted}
        night_pick = next((item for item in night_candidates if item.get("id") not in selected_ids), None)
        if night_pick is None:
            night_pick = night_candidates[0]
        adjusted[-1] = night_pick

    adjusted.sort(
        key=lambda x: (0 if x.get("district_cluster") == NIGHT_TOUR_CLUSTER else 1, -x.get("_score", 0))
    )
    return adjusted


def _infer_origin_cluster_hint(origin_text: str) -> str | None:
    for cluster, keywords in ORIGIN_CLUSTER_HINT_RULES:
        if any(keyword in origin_text for keyword in keywords):
            return cluster
    return None


def _apply_origin_nearby_preference(
    selected_sights: List[Dict[str, Any]],
    cluster_to_sights: Dict[str, List[Dict[str, Any]]],
    origin_cluster_hint: str | None,
) -> List[Dict[str, Any]]:
    """nearby 轻量首站偏好。

    仅用于确保候选里至少有一个起点邻近簇点位，避免首站直接跳远簇。
    """
    if not selected_sights or not origin_cluster_hint:
        return selected_sights

    adjusted = list(selected_sights)
    if any(item.get("district_cluster") == origin_cluster_hint for item in adjusted):
        adjusted.sort(
            key=lambda x: (0 if x.get("district_cluster") == origin_cluster_hint else 1, -x.get("_score", 0))
        )
        return adjusted

    nearby_candidates = sorted(
        cluster_to_sights.get(origin_cluster_hint, []),
        key=lambda x: x.get("_score", 0),
        reverse=True,
    )
    if not nearby_candidates:
        return adjusted

    selected_ids = {item.get("id") for item in adjusted}
    nearby_pick = next((item for item in nearby_candidates if item.get("id") not in selected_ids), None)
    if nearby_pick is None:
        nearby_pick = nearby_candidates[0]

    adjusted[-1] = nearby_pick
    adjusted.sort(
        key=lambda x: (0 if x.get("district_cluster") == origin_cluster_hint else 1, -x.get("_score", 0))
    )
    return adjusted


def _resolve_leg_mode(
    request: PlanRequest,
    prev_cluster: str | None,
    current_cluster: str,
    is_first_leg: bool,
) -> str:
    """选择当前路段交通模式。

    轻量策略:
    - 同簇优先步行
    - 跨簇优先 transport_preference
    - has_car=True 且偏好仍为 public_transit 时，跨簇轻量转为 drive
    """
    preferred = request.transport_preference.value

    if not is_first_leg and prev_cluster == current_cluster:
        return "walking"

    if preferred == "public_transit" and request.has_car and prev_cluster != current_cluster:
        return "drive"

    return preferred


def _apply_cross_cluster_cost_guard(
    selected_sights: List[Dict[str, Any]],
    cluster_to_sights: Dict[str, List[Dict[str, Any]]],
    request: PlanRequest,
) -> List[Dict[str, Any]]:
    """跨簇成本防护: 若跨簇代价明显偏高，优先回填主簇景点减少跨簇。"""
    if len(selected_sights) <= 1:
        return selected_sights

    primary_cluster = selected_sights[0]["district_cluster"]
    secondary_indexes = [
        idx for idx, sight in enumerate(selected_sights) if sight["district_cluster"] != primary_cluster
    ]
    if not secondary_indexes:
        return selected_sights

    anchor = next((s for s in selected_sights if s["district_cluster"] == primary_cluster), selected_sights[0])

    durations: List[int] = []
    for idx in secondary_indexes:
        target = selected_sights[idx]
        mode = _resolve_leg_mode(request, primary_cluster, target["district_cluster"], is_first_leg=False)
        route = get_route_info(
            anchor,
            target,
            mode,
            origin_cluster=primary_cluster,
            destination_cluster=target["district_cluster"],
        )
        durations.append(route.duration_minutes)

    avg_cross_duration = sum(durations) / len(durations)
    if avg_cross_duration < CROSS_CLUSTER_DURATION_THRESHOLD_MIN:
        return selected_sights

    selected_ids = {s["id"] for s in selected_sights}
    extra_primary = [
        s for s in cluster_to_sights.get(primary_cluster, []) if s["id"] not in selected_ids
    ]
    if not extra_primary:
        return selected_sights

    replacement = max(extra_primary, key=lambda x: x["_score"])
    last_secondary_idx = secondary_indexes[-1]

    adjusted = list(selected_sights)
    adjusted[last_secondary_idx] = replacement
    adjusted.sort(
        key=lambda x: (0 if x["district_cluster"] == primary_cluster else 1, -x["_score"])
    )
    return adjusted


def _estimate_leg(
    origin_point: Any,
    destination_poi: Dict[str, Any],
    request: PlanRequest,
    prev_cluster: str | None,
    is_first_leg: bool,
) -> RouteInfo:
    current_cluster = destination_poi["district_cluster"]
    mode = _resolve_leg_mode(request, prev_cluster, current_cluster, is_first_leg)
    return get_route_info(
        origin=origin_point,
        destination=destination_poi,
        mode=mode,
        origin_cluster=prev_cluster,
        destination_cluster=current_cluster,
    )


def _order_sights_by_route_cost(
    sights: List[Dict[str, Any]],
    request: PlanRequest,
    origin_cluster_hint: str | None = None,
) -> List[Dict[str, Any]]:
    """按路段真实代价(或降级估算)做近似贪心排序。"""
    if len(sights) <= 1:
        return sights

    remaining = list(sights)
    ordered: List[Dict[str, Any]] = []

    current_point: Any = request.origin
    current_cluster: str | None = origin_cluster_hint

    while remaining:
        best_idx = 0
        best_route: RouteInfo | None = None
        best_key: tuple[int, int, int] | None = None

        for idx, candidate in enumerate(remaining):
            route = _estimate_leg(
                origin_point=current_point,
                destination_poi=candidate,
                request=request,
                prev_cluster=current_cluster,
                is_first_leg=(len(ordered) == 0),
            )

            first_leg_nearby_bias = (
                len(ordered) == 0
                and request.origin_preference_mode == "nearby"
                and origin_cluster_hint is not None
            )
            same_cluster_rank = (
                0 if candidate.get("district_cluster") == origin_cluster_hint else 1
            ) if first_leg_nearby_bias else 0
            candidate_key = (same_cluster_rank, route.duration_minutes, route.distance_meters)

            if best_route is None or best_key is None:
                best_idx = idx
                best_route = route
                best_key = candidate_key
                continue

            if candidate_key < best_key:
                best_idx = idx
                best_route = route
                best_key = candidate_key

        picked = remaining.pop(best_idx)
        ordered.append(picked)
        current_point = picked
        current_cluster = picked["district_cluster"]

    return ordered


def _pick_restaurants(
    cluster_to_restaurants: Dict[str, List[Dict[str, Any]]],
    selected_sights: List[Dict[str, Any]],
    meal_count: int,
    preferred_clusters: List[str] | None = None,
) -> List[Dict[str, Any]]:
    if meal_count <= 0:
        return []

    for cluster in cluster_to_restaurants:
        cluster_to_restaurants[cluster].sort(key=lambda x: x["_score"], reverse=True)

    preferred_cluster_order: List[str] = []
    for cluster in preferred_clusters or []:
        if cluster in cluster_to_restaurants and cluster not in preferred_cluster_order:
            preferred_cluster_order.append(cluster)

    for sight in selected_sights:
        cluster = sight["district_cluster"]
        if cluster not in preferred_cluster_order:
            preferred_cluster_order.append(cluster)

    chosen: List[Dict[str, Any]] = []
    used_ids = set()

    # 优先在景点所在簇选餐，确保顺路
    for cluster in preferred_cluster_order:
        for restaurant in cluster_to_restaurants.get(cluster, []):
            if restaurant["id"] in used_ids:
                continue
            chosen.append(restaurant)
            used_ids.add(restaurant["id"])
            break
        if len(chosen) >= meal_count:
            return chosen[:meal_count]

    # 不足时，全局补齐
    all_restaurants = [item for items in cluster_to_restaurants.values() for item in items]
    all_restaurants.sort(key=lambda x: x["_score"], reverse=True)
    for restaurant in all_restaurants:
        if restaurant["id"] in used_ids:
            continue
        chosen.append(restaurant)
        used_ids.add(restaurant["id"])
        if len(chosen) >= meal_count:
            break

    return chosen[:meal_count]


def _insert_meals_among_sights(sights: List[Dict[str, Any]], meals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把餐饮插在景点之间，避免明显绕路。"""
    if not meals:
        return list(sights)

    if not sights:
        return list(meals)

    result: List[Dict[str, Any]] = []

    if len(meals) == 1:
        insert_after = 1 if len(sights) >= 2 else len(sights)
        result.extend(sights[:insert_after])
        result.append(meals[0])
        result.extend(sights[insert_after:])
        return result

    # 两顿饭时，尽量分布在前半段与后半段
    for idx, sight in enumerate(sights):
        result.append(sight)
        if idx == 0 and len(meals) > 0:
            result.append(meals[0])
        elif idx == 1 and len(meals) > 1:
            result.append(meals[1])

    # 理论上不会触发，作为兜底
    if len(meals) > 2:
        result.extend(meals[2:])

    return result


def _dedupe_stops_by_id(stops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen_ids = set()
    for stop in stops:
        stop_id = stop.get("id")
        if stop_id in seen_ids:
            continue
        seen_ids.add(stop_id)
        deduped.append(stop)
    return deduped


def _build_evening_stop_candidates(
    selected_sights: List[Dict[str, Any]],
    selected_meals: List[Dict[str, Any]],
    grouped_sights: Dict[str, List[Dict[str, Any]]],
    grouped_restaurants: Dict[str, List[Dict[str, Any]]],
    request: PlanRequest,
) -> List[List[Dict[str, Any]]]:
    """夜游最小闭环候选（仅 evening 分支使用）。

    目标：
    - 优先形成“夜游点 + 顺路餐饮”的 2 站闭环
    - 若候选不足，再尝试补一个夜游相关点
    """
    plans: List[List[Dict[str, Any]]] = []

    # baseline: 原有插餐策略
    plans.append(_insert_meals_among_sights(selected_sights, selected_meals))

    # evening + need_meal: 优先尝试“先餐后游”，降低晚间餐馆闭店导致的单点风险
    if request.need_meal and selected_sights and selected_meals:
        anchor_sight = selected_sights[0]
        same_cluster_meal = next(
            (m for m in selected_meals if m.get("district_cluster") == anchor_sight.get("district_cluster")),
            selected_meals[0],
        )
        meal_first_plan = _dedupe_stops_by_id(
            [same_cluster_meal, anchor_sight]
            + [s for s in selected_sights if s.get("id") != anchor_sight.get("id")]
            + [m for m in selected_meals if m.get("id") != same_cluster_meal.get("id")]
        )
        plans.insert(0, meal_first_plan)

    # 若没选到餐，补一个夜游簇优先的餐饮候选
    if request.need_meal and not selected_meals and selected_sights:
        supplemental_meals = _pick_restaurants(
            grouped_restaurants,
            selected_sights,
            meal_count=1,
            preferred_clusters=[NIGHT_TOUR_CLUSTER],
        )
        if supplemental_meals:
            plans.append(_insert_meals_among_sights(selected_sights, supplemental_meals))

    # 若仅 1 个景点，尝试补一个夜游相关景点
    if len(selected_sights) == 1:
        base_sight = selected_sights[0]
        extra_night_sights = [
            s for s in grouped_sights.get(NIGHT_TOUR_CLUSTER, []) if s.get("id") != base_sight.get("id")
        ]
        if extra_night_sights:
            augmented_sights = [base_sight, extra_night_sights[0]]
            plans.append(_insert_meals_among_sights(augmented_sights, selected_meals))

    unique_plans: List[List[Dict[str, Any]]] = []
    seen_plan_keys = set()
    for plan in plans:
        key = tuple(stop.get("id") for stop in plan)
        if key in seen_plan_keys:
            continue
        seen_plan_keys.add(key)
        unique_plans.append(plan)
    return unique_plans


def _evening_result_rank(
    route_items: List[RouteItem],
    skipped_closed_count: int,
    need_meal: bool,
) -> tuple[int, int, int, int]:
    has_meal = any(item.type == "restaurant" for item in route_items)
    return (
        1 if len(route_items) >= 2 else 0,
        1 if (has_meal or not need_meal) else 0,
        len(route_items),
        -skipped_closed_count,
    )


def _resolve_weather_flags(
    request: PlanRequest,
    weather_context: Dict[str, Any] | None = None,
) -> tuple[bool, bool]:
    if weather_context:
        return bool(weather_context.get("is_rainy")), bool(weather_context.get("is_hot"))
    return request.weather.value == "rainy", request.weather.value == "hot"


def _reason_for_stop(
    stop: Dict[str, Any],
    request: PlanRequest,
    weather_context: Dict[str, Any] | None = None,
) -> str:
    parts: List[str] = []
    is_rainy, is_hot = _resolve_weather_flags(request, weather_context)

    if (is_rainy or is_hot) and stop.get("indoor_or_outdoor") == "indoor":
        parts.append("室内友好")
    if request.companion_type == "parents" and stop.get("parent_friendly"):
        parts.append("对陪父母更轻松")
    if request.companion_type == "friends" and stop.get("friend_friendly"):
        parts.append("氛围热闹")
    if request.companion_type == "partner" and stop.get("couple_friendly"):
        parts.append("更有氛围感")
    if stop["kind"] == "restaurant":
        parts.append("位于顺路簇内，减少绕行")
    else:
        parts.append("同簇连续，减少折返")

    return "，".join(parts[:3])


def _mode_to_text(mode: str) -> str:
    mapping = {
        "walking": "步行",
        "public_transit": "地铁/打车",
        "drive": "驾车",
        "taxi": "打车",
    }
    return mapping.get(mode, mode)


def _transport_text(route: RouteInfo, is_first_leg: bool) -> str:
    estimate_tag = "" if route.source == "real_api" else "（估算）"
    text = f"{_mode_to_text(route.mode)}{estimate_tag} 约{route.duration_minutes}分钟"
    if is_first_leg:
        return f"从起点前往（{text}）"
    return text


def _weather_source_text(source: str) -> str:
    if source == "amap_weather":
        return "高德实时天气"
    return "请求参数 weather 兜底"


def _build_weather_tip(
    request: PlanRequest,
    weather_context: Dict[str, Any] | None,
) -> str:
    source = str((weather_context or {}).get("source") or "fallback_request")
    condition = str((weather_context or {}).get("weather_condition") or request.weather.value)
    temperature = (weather_context or {}).get("temperature_c")
    feels_like = (weather_context or {}).get("feels_like_c")
    obs_time = (weather_context or {}).get("obs_time")

    detail_parts: List[str] = [f"天气来源：{_weather_source_text(source)}", f"当前天气：{condition}"]
    if temperature is not None:
        detail_parts.append(f"气温约 {temperature}°C")
    if feels_like is not None:
        detail_parts.append(f"体感约 {feels_like}°C")
    if obs_time:
        detail_parts.append(f"观测时间 {obs_time}")
    return "；".join(detail_parts) + "。"


def _build_weather_impact_tip(
    request: PlanRequest,
    weather_context: Dict[str, Any] | None,
) -> str:
    is_rainy, is_hot = _resolve_weather_flags(request, weather_context)
    if is_rainy and is_hot:
        return "当前有雨且偏热，已优先室内点位，并下调长时间户外/夜游点权重。"
    if is_rainy:
        return "今日有雨，已优先室内点位并适度下调夜游点。"
    if is_hot:
        return "当前高温，已优先室内点位并下调长时间户外停留。"
    return "当前天气对路线影响较小，仍按顺路与少折返为主。"


def _parse_hhmm_minutes(hhmm: Any) -> int | None:
    if not isinstance(hhmm, str) or ":" not in hhmm:
        return None
    hh_text, mm_text = hhmm.split(":", 1)
    try:
        hh = int(hh_text)
        mm = int(mm_text)
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh * 60 + mm


def _dt_to_minutes(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _is_stop_open_for_timeslot(stop: Dict[str, Any], arrival_time: datetime, end_time: datetime) -> bool:
    """最小营业时间约束。

    - 优先使用 POI 自带 open_time/close_time/is_all_day
    - 缺失或无法解析时，保守放行，避免误杀点位
    """
    if stop.get("is_all_day"):
        return True

    open_minutes = _parse_hhmm_minutes(stop.get("open_time"))
    close_minutes = _parse_hhmm_minutes(stop.get("close_time"))
    if open_minutes is None or close_minutes is None:
        return True

    arrival_minutes = _dt_to_minutes(arrival_time)
    end_minutes = _dt_to_minutes(end_time)

    # 跨午夜营业时段，如 18:00-02:00
    if close_minutes < open_minutes:
        in_open_window = arrival_minutes >= open_minutes or arrival_minutes <= close_minutes
        in_close_window = end_minutes >= open_minutes or end_minutes <= close_minutes
        return in_open_window and in_close_window

    return arrival_minutes >= open_minutes and end_minutes <= close_minutes


def _build_route_items(
    stops: List[Dict[str, Any]],
    request: PlanRequest,
    total_budget_minutes: int,
    start_time_hhmm: str = "09:00",
    origin_cluster_hint: str | None = None,
) -> tuple[List[RouteItem], List[RouteInfo], int, bool, int, int]:
    """构建路线明细。

    V1.5: 交通耗时已并入时间轴。每一站先计算到达时间，再计算停留结束时间。
    """
    route: List[RouteItem] = []
    leg_infos: List[RouteInfo] = []

    cursor = datetime.strptime(start_time_hhmm, "%H:%M")
    prev_cluster: str | None = origin_cluster_hint if request.origin_preference_mode == "nearby" else None
    prev_point: Any = request.origin
    used_minutes = 0
    truncated_by_budget = False
    budget_trimmed_count = 0
    skipped_closed_count = 0

    for index, stop in enumerate(stops):
        route_info = _estimate_leg(
            origin_point=prev_point,
            destination_poi=stop,
            request=request,
            prev_cluster=prev_cluster,
            is_first_leg=(index == 0),
        )

        minutes = int(stop.get("estimated_visit_minutes", 60))
        if stop["kind"] == "restaurant":
            minutes = max(minutes, 60)

        arrival_time = cursor + timedelta(minutes=route_info.duration_minutes)
        end_time = arrival_time + timedelta(minutes=minutes)

        # 最小开闭店约束: 当前时段明显不可用则跳过，继续尝试后续点位。
        if not _is_stop_open_for_timeslot(stop, arrival_time, end_time):
            skipped_closed_count += 1
            continue

        candidate_total = used_minutes + route_info.duration_minutes + minutes
        # V1.6 最小预算裁剪: 超预算即停止，不做回溯替换。
        if candidate_total > total_budget_minutes:
            truncated_by_budget = True
            budget_trimmed_count = len(stops) - index
            break

        time_slot = f"{arrival_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"

        leg_infos.append(route_info)
        route.append(
            RouteItem(
                time_slot=time_slot,
                type=stop["kind"],
                name=stop["name"],
                district_cluster=stop["district_cluster"],
                transport_from_prev=_transport_text(route_info, is_first_leg=(index == 0)),
                reason=stop["_reason"],
                estimated_distance_meters=route_info.distance_meters,
                estimated_duration_minutes=route_info.duration_minutes,
            )
        )

        cursor = end_time
        used_minutes = candidate_total
        prev_cluster = stop["district_cluster"]
        prev_point = stop

    return route, leg_infos, used_minutes, truncated_by_budget, budget_trimmed_count, skipped_closed_count


def generate_itinerary(request: PlanRequest, candidate_pois: List[Dict[str, Any]] | None = None) -> ItineraryResponse:
    """生成 V1.9 行程。

    范围说明:
    - 当前为西安市区核心路线版，只使用碑林区、莲湖区、雁塔区对应 POI
    - 固定地理簇: 城墙钟鼓楼簇 / 小寨文博簇 / 大雁塔簇 / 曲江夜游簇
    - POI 来源: 真实高德 POI（可选）或本地 mock（自动回退）

    字段生效说明:
    - 打分: 由 scoring.py 使用 V1 生效字段（含 purpose 的粗粒度加权）
    - 天气: 优先 weather_service 实时天气（高德），失败时回退 request.weather
    - 排程: 主要使用 available_hours、need_meal、簇规则，以及路线时长/距离代价
    - origin: 用于首段路线代价查询；调用失败时会自动回退简化规则
    - origin_preference_mode=nearby: 轻量影响首站候选优先级
    - has_car / transport_preference: 轻量影响路段模式选择（真实路线可用时优先生效）
    - preferred_period: 轻量时段信号，调整起步时间并做轻量候选偏好
    - 其余 V2 预留字段: 当前仅接收并校验，不进入复杂策略系统

    注:
    - 当前版本已把交通耗时并入时间轴。
    - available_hours 通过预算分钟控制，采用“超预算即停止”的近似裁剪。
    - 新增最小营业时间约束: 明显不在营业时段的点位会被跳过。
    """
    pois = list(candidate_pois) if candidate_pois else load_pois(request_context=request)
    weather_context = get_weather_context(request=request, candidate_pois=pois)
    total_budget_minutes = int(request.available_hours * 60)

    sights = [p for p in pois if p.get("kind") == "sight"]
    restaurants = [p for p in pois if p.get("kind") == "restaurant"]

    ranked_sights = sort_by_score(sights, request, weather_context=weather_context)
    ranked_restaurants = sort_by_score(restaurants, request, weather_context=weather_context)

    grouped_sights = _group_by_cluster(ranked_sights)
    grouped_restaurants = _group_by_cluster(ranked_restaurants)

    target_sights, target_meals = _targets_by_time(request)
    selected_sights = _pick_sights(grouped_sights, target_sights)
    is_evening_request = request.preferred_period == "evening"
    nearby_origin_enabled = request.origin_preference_mode == "nearby"
    origin_cluster_hint = _infer_origin_cluster_hint(request.origin) if nearby_origin_enabled else None
    period_start_time = _start_time_for_period(request.preferred_period)

    if is_evening_request:
        selected_sights = _apply_evening_preference(selected_sights, grouped_sights)
    elif nearby_origin_enabled:
        selected_sights = _apply_origin_nearby_preference(
            selected_sights=selected_sights,
            cluster_to_sights=grouped_sights,
            origin_cluster_hint=origin_cluster_hint,
        )

    if not is_evening_request:
        selected_sights = _apply_cross_cluster_cost_guard(selected_sights, grouped_sights, request)
    selected_sights = _order_sights_by_route_cost(
        selected_sights,
        request,
        origin_cluster_hint=origin_cluster_hint if nearby_origin_enabled else None,
    )
    selected_sights = _apply_period_first_stop_bias(
        selected_sights,
        _preferred_clusters_for_period(request.preferred_period),
    )

    evening_meal_priority = [NIGHT_TOUR_CLUSTER] if (is_evening_request and request.need_meal) else None
    selected_meals = _pick_restaurants(
        grouped_restaurants,
        selected_sights,
        target_meals,
        preferred_clusters=evening_meal_priority,
    )

    if is_evening_request:
        stop_plans = _build_evening_stop_candidates(
            selected_sights=selected_sights,
            selected_meals=selected_meals,
            grouped_sights=grouped_sights,
            grouped_restaurants=grouped_restaurants,
            request=request,
        )

        best_rank: tuple[int, int, int, int] | None = None
        best_route_bundle: tuple[
            List[Dict[str, Any]],
            List[RouteItem],
            List[RouteInfo],
            int,
            bool,
            int,
            int,
        ] | None = None

        for plan in stop_plans:
            candidate_stops = [dict(stop) for stop in plan]
            for stop in candidate_stops:
                stop["_reason"] = _reason_for_stop(stop, request, weather_context=weather_context)

            (
                candidate_route_items,
                candidate_leg_infos,
                candidate_used_minutes,
                candidate_truncated_by_budget,
                candidate_budget_trimmed_count,
                candidate_skipped_closed_count,
            ) = _build_route_items(
                stops=candidate_stops,
                request=request,
                total_budget_minutes=total_budget_minutes,
                start_time_hhmm=period_start_time,
                origin_cluster_hint=origin_cluster_hint if nearby_origin_enabled else None,
            )

            rank = _evening_result_rank(
                route_items=candidate_route_items,
                skipped_closed_count=candidate_skipped_closed_count,
                need_meal=request.need_meal,
            )
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_route_bundle = (
                    candidate_stops,
                    candidate_route_items,
                    candidate_leg_infos,
                    candidate_used_minutes,
                    candidate_truncated_by_budget,
                    candidate_budget_trimmed_count,
                    candidate_skipped_closed_count,
                )

        if best_route_bundle is None:
            stops = []
            route_items = []
            leg_infos = []
            used_minutes = 0
            truncated_by_budget = False
            budget_trimmed_count = 0
            skipped_closed_count = 0
        else:
            (
                stops,
                route_items,
                leg_infos,
                used_minutes,
                truncated_by_budget,
                budget_trimmed_count,
                skipped_closed_count,
            ) = best_route_bundle
    else:
        stops = _insert_meals_among_sights(selected_sights, selected_meals)
        for stop in stops:
            stop["_reason"] = _reason_for_stop(stop, request, weather_context=weather_context)

        (
            route_items,
            leg_infos,
            used_minutes,
            truncated_by_budget,
            budget_trimmed_count,
            skipped_closed_count,
        ) = _build_route_items(
            stops=stops,
            request=request,
            total_budget_minutes=total_budget_minutes,
            start_time_hhmm=period_start_time,
            origin_cluster_hint=origin_cluster_hint if nearby_origin_enabled else None,
        )

    clusters_in_route: List[str] = []
    for item in route_items:
        cluster = item.district_cluster
        if cluster not in clusters_in_route:
            clusters_in_route.append(cluster)

    real_leg_count = sum(1 for leg in leg_infos if leg.source == "real_api")
    fallback_leg_count = len(leg_infos) - real_leg_count

    route_source_text = "路线代价基于真实路线时长/距离" if real_leg_count > 0 else "路线代价基于降级估算"
    route_cluster_text = " -> ".join(clusters_in_route) if clusters_in_route else "时间预算过紧，未排入点位"
    weather_source = str((weather_context or {}).get("source") or "fallback_request")
    weather_condition = str((weather_context or {}).get("weather_condition") or request.weather.value)
    summary = (
        f"基于 {request.available_hours:.1f} 小时与 {request.companion_type.value} 同行，"
        f"当前天气（{_weather_source_text(weather_source)}）为 {weather_condition}，"
        f"推荐以 {route_cluster_text} 为主的顺路行程，优先减少折返并安排正餐。"
        f"（{route_source_text}）"
    )

    total_distance = sum(leg.distance_meters for leg in leg_infos)
    total_travel_minutes = sum(leg.duration_minutes for leg in leg_infos)
    # V1.5: origin 会用于首段路线代价查询；若调用失败仍回退可用。
    tips = [
        _build_weather_tip(request, weather_context),
        _build_weather_impact_tip(request, weather_context),
        f"起点为 {request.origin}，预计路程约 {total_distance} 米，交通耗时约 {total_travel_minutes} 分钟。",
        "同簇段优先步行，跨簇段优先参考真实路线时长后再决定交通方式。",
        f"时间预算 {total_budget_minutes} 分钟，当前已排入 {used_minutes} 分钟。",
    ]
    if is_evening_request:
        tips.append("检测到夜游偏好，已按晚间起步（18:00）并优先尝试夜游簇点位。")
        has_meal_in_route = any(item.type == "restaurant" for item in route_items)
        if request.need_meal and len(route_items) >= 2 and has_meal_in_route:
            tips.append("夜游场景已优先保证晚间用餐与夜游点衔接。")
        elif len(route_items) < 2:
            if request.need_meal and not has_meal_in_route:
                tips.append("夜游场景下已优先尝试补入晚间用餐，但当前时段可营业且顺路的餐饮候选有限。")
            else:
                tips.append("夜游场景下当前时段可用候选有限，暂未形成完整多站行程。")
    elif nearby_origin_enabled:
        if origin_cluster_hint:
            tips.append(f"已优先考虑起点“{request.origin}”附近的首站候选。")
        else:
            tips.append("已尝试按起点附近偏好优化首站候选。")

    if truncated_by_budget:
        tips.append(f"已因时间限制裁剪 {budget_trimmed_count} 个后续点位（超预算即停止）。")
    else:
        tips.append("当前路线未触发时间裁剪。")

    if skipped_closed_count > 0:
        tips.append(f"已跳过 {skipped_closed_count} 个明显不在营业时段内的点位。")

    if real_leg_count > 0:
        tips.append(f"本次有 {real_leg_count} 段使用高德真实路线估算。")
    if fallback_leg_count > 0:
        tips.append(f"本次有 {fallback_leg_count} 段已回退到简化交通规则（接口仍可用）。")

    if request.walking_tolerance == "low":
        tips.append("已降低高步行强度点位，现场可继续按体力缩短停留。")

    return ItineraryResponse(summary=summary, route=route_items, tips=tips)
