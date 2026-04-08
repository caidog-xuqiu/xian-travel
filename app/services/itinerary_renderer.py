from __future__ import annotations

from typing import Dict, Iterable, List

from app.models.schemas import ItineraryResponse, PlanRequest, RouteItem
from app.services.knowledge_layer import bundle_to_notes, bundle_to_tags, retrieve_place_knowledge


def _unique_in_order(items: Iterable[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _collect_knowledge_hints(
    itinerary: ItineraryResponse,
    request: PlanRequest | None,
) -> Dict[str, List[str]]:
    names = [item.name for item in itinerary.route]
    clusters = _unique_in_order(item.district_cluster for item in itinerary.route)
    query = " ".join(names + clusters)
    context = {
        "preferred_period": request.preferred_period if request else None,
        "purpose": request.purpose.value if request else "",
        "cluster": clusters[0] if clusters else "",
        "tags": [item.type for item in itinerary.route],
    }
    bundle = retrieve_place_knowledge(query=query, context=context)
    return {
        "tags": bundle_to_tags(bundle),
        "notes": bundle_to_notes(bundle, limit=2),
        "sources": list(bundle.source_tags),
    }


def _pick_knowledge_highlight(tags: List[str]) -> str | None:
    if not tags:
        return None
    priority = [
        "文博密度高",
        "晚间氛围更强",
        "适合拍照打卡",
        "雨天室内友好",
        "区域风格鲜明",
        "餐饮选择更丰富",
        "动线更顺",
    ]
    for tag in priority:
        if tag in tags:
            return tag
    return tags[0]


def _title_from_request(request: PlanRequest | None) -> str:
    if request is None:
        return "西安市区路线建议"

    day_text = "半日" if request.available_hours <= 6 else "一日"
    companion_prefix = {
        "parents": "陪父母",
        "friends": "朋友同行",
        "partner": "约会",
        "solo": "西安市区",
    }.get(request.companion_type.value, "西安市区")

    if request.purpose.value == "dating" or request.companion_type.value == "partner":
        return f"西安约会型{day_text}路线"
    if request.companion_type.value == "parents":
        return f"陪父母的西安{day_text}路线"
    if companion_prefix == "西安市区":
        return f"西安市区{day_text}路线"
    return f"{companion_prefix}西安{day_text}路线"


def _period_label(period: str | None) -> str | None:
    return {
        "morning": "上午",
        "midday": "午间",
        "afternoon": "午后",
        "evening": "晚间",
    }.get(period or "")


def _period_start_time(period: str | None) -> str | None:
    return {
        "morning": "09:00",
        "midday": "11:30",
        "afternoon": "14:00",
        "evening": "18:00",
    }.get(period or "")


def _period_overview_hint(request: PlanRequest, itinerary: ItineraryResponse) -> str:
    period = request.preferred_period
    if not period:
        return ""

    has_meal = any(item.type == "restaurant" for item in itinerary.route)
    start_time = _period_start_time(period)
    start_text = f"{start_time} 左右" if start_time else ""
    if period == "morning":
        return f"按上午节奏安排，起步约 {start_text}，更适合先完成经典或文博类点位。"
    if period == "midday":
        if request.need_meal or has_meal:
            return f"按午间节奏安排，起步约 {start_text}，更利于正餐与游览顺路衔接。"
        return f"按午间节奏安排，起步约 {start_text}，顺路衔接会更自然。"
    if period == "afternoon":
        return f"按午后节奏安排，起步约 {start_text}，整体会更偏轻松。"
    if period == "evening":
        return f"按晚间节奏安排，{start_text} 后开始更合适，利于夜游与晚餐衔接。"
    return ""


def _period_schedule_hint(request: PlanRequest) -> str:
    period = request.preferred_period
    if not period:
        return ""

    if period == "morning":
        return "时段说明：上午先走经典/文博点更稳，核心内容尽量放在前半程。"
    if period == "midday":
        if request.need_meal:
            return "时段说明：午间更顺路安排用餐，已优先保留正餐位置。"
        return "时段说明：午间出行以顺路衔接为主。"
    if period == "afternoon":
        return "时段说明：午后节奏更轻松，停留以短时为主。"
    if period == "evening":
        return "时段说明：晚间更偏夜游节奏，已优先衔接夜景与晚餐。"
    return ""


def _period_first_stop_hint(request: PlanRequest) -> str:
    period = request.preferred_period
    if not period:
        return ""
    if period == "morning":
        return "放在上午先走，更利于把核心点位尽早完成。"
    if period == "midday":
        return "安排在午间更顺路，也方便正餐自然插入中段。"
    if period == "afternoon":
        return "放在午后更合适，整体节奏会更轻松一些。"
    if period == "evening":
        return "放在晚间更符合夜游节奏，也更适合衔接拍照和晚餐安排。"
    return ""


def _period_tip(request: PlanRequest, itinerary: ItineraryResponse) -> str | None:
    period = request.preferred_period
    if not period:
        return None
    start_time = _period_start_time(period)
    start_hint = f"{start_time} 左右" if start_time else ""

    if period == "morning":
        return f"已按上午偏好安排，建议在 {start_hint} 开始更顺。"
    if period == "midday":
        if request.need_meal:
            return f"已按午间偏好安排，建议 {start_hint} 开始并按时用餐更顺。"
        return f"已按午间偏好安排，建议 {start_hint} 开始更顺路。"
    if period == "afternoon":
        return f"已按午后偏好安排，建议 {start_hint} 开始更贴合节奏。"
    if period == "evening":
        if not itinerary.route:
            return f"已按晚间偏好安排，建议 {start_hint} 后开始更合适，晚间候选也相对有限。"
        return f"已按晚间偏好安排，建议 {start_hint} 后开始更顺，也便于衔接夜游与晚餐。"
    return None


def _overview_text(
    itinerary: ItineraryResponse,
    request: PlanRequest | None,
    knowledge_hints: Dict[str, List[str]] | None = None,
) -> str:
    clusters = _unique_in_order(item.district_cluster for item in itinerary.route)
    cluster_text = "、".join(clusters) if clusters else "核心簇内"
    meal_count = sum(1 for item in itinerary.route if item.type == "restaurant")
    stop_count = len(itinerary.route)

    if stop_count == 0:
        period_hint = ""
        if request is not None and request.preferred_period:
            label = _period_label(request.preferred_period)
            if label:
                period_hint = f"当前为{label}时段出行，"
        return (
            f"{period_hint}未生成可执行站点，通常是时段与营业时间不匹配，"
            "或在既定条件下可用候选不足。建议调整出发时段或适度放宽条件后重试。"
        )

    if request is None:
        return (
            f"本次行程共安排 {stop_count} 站，主要覆盖 {cluster_text}，整体按顺路衔接组织，"
            "优先减少折返并兼顾游览与休息。"
        )

    rhythm = "轻松" if request.walking_tolerance.value == "low" or request.available_hours <= 6 else "均衡"
    weather_hint = ""
    if request.weather.value in {"rainy", "hot"}:
        weather_hint = "考虑到当前天气，路线更偏向室内或短时户外停留。"

    meal_hint = "并保留正餐安排" if meal_count > 0 else "行程以连续游览为主"
    period_hint = _period_overview_hint(request, itinerary)

    overview = (
        f"这次路线以 {cluster_text} 为主，共 {stop_count} 站，整体节奏偏{rhythm}，"
        "优先顺路衔接、少折返。"
    )
    if period_hint:
        overview += period_hint
    if weather_hint:
        overview += weather_hint
    overview += f"{meal_hint}。"
    if knowledge_hints:
        tags = knowledge_hints.get("tags") or []
        highlight = _pick_knowledge_highlight(tags)
        if highlight:
            overview += f"本次还参考了点位知识：{highlight}。"
    overview += f"适合本次 {request.available_hours:.1f} 小时出行。"
    return overview


def _stop_type_text(item: RouteItem) -> str:
    return "景点" if item.type == "sight" else "餐饮点"


def _schedule_text(
    itinerary: ItineraryResponse,
    request: PlanRequest | None = None,
    knowledge_hints: Dict[str, List[str]] | None = None,
) -> str:
    if not itinerary.route:
        base = (
            "当前未排入可执行点位。可能原因是时段与营业时间不匹配，"
            "或在现有条件下可用候选不足。建议调整到更匹配的时段"
            "（如夜游场景改为傍晚后出发）或适度放宽条件后重试。"
        )
        if request is not None and request.preferred_period == "evening":
            return base + "晚间候选通常更少，适当推迟开始时间更合适。"
        return base

    lines: List[str] = []
    period_hint = _period_schedule_hint(request) if request is not None else ""
    if period_hint:
        lines.append(period_hint)
    if knowledge_hints:
        notes = knowledge_hints.get("notes") or []
        if notes:
            lines.append(f"场景补充：{notes[0]}")

    for idx, item in enumerate(itinerary.route):
        next_leg_text = ""
        if idx + 1 < len(itinerary.route):
            next_leg_text = f" 下一段建议：{itinerary.route[idx + 1].transport_from_prev}。"

        period_stop_hint = ""
        if idx == 0 and request is not None:
            period_stop_hint = _period_first_stop_hint(request)

        line = (
            f"{item.time_slot} 前往 {item.name}（{item.district_cluster}，{_stop_type_text(item)}）。"
            f"这样安排主要因为：{item.reason}。"
            f"{item.transport_from_prev}。{period_stop_hint}{next_leg_text}"
        )
        lines.append(line.strip())

    if request is not None and request.preferred_period == "evening":
        has_meal = any(item.type == "restaurant" for item in itinerary.route)
        if len(itinerary.route) == 1:
            lines.append("当前时段可用候选有限，本次先保留单站夜游点位。")
        elif has_meal:
            lines.insert(1 if period_hint else 0, "本次已优先保证晚间用餐与夜游点顺路衔接。")

    return "\n".join(lines)


def _transport_text(itinerary: ItineraryResponse) -> str:
    distances = [item.estimated_distance_meters or 0 for item in itinerary.route]
    durations = [item.estimated_duration_minutes or 0 for item in itinerary.route]
    total_distance = sum(distances)
    total_duration = sum(durations)

    walk_count = sum(1 for item in itinerary.route if "步行" in item.transport_from_prev)
    transit_like_count = sum(
        1
        for item in itinerary.route
        if any(key in item.transport_from_prev for key in ["地铁", "打车", "驾车"])
    )

    distance_km = round(total_distance / 1000.0, 1)
    return (
        f"全程预计约 {distance_km} 公里，交通耗时约 {total_duration} 分钟。"
        f"同簇段优先步行，跨簇段优先参考地铁/打车；本次约 {walk_count} 段以步行为主，"
        f"{transit_like_count} 段为跨簇通行。"
    )


def _tips_text(
    itinerary: ItineraryResponse,
    request: PlanRequest | None = None,
    knowledge_hints: Dict[str, List[str]] | None = None,
) -> str:
    if request is not None:
        period_tip = _period_tip(request, itinerary)
    else:
        period_tip = None

    if not itinerary.tips:
        return period_tip or "建议出发前再确认天气与营业时段，并预留机动时间。"

    weather_tips = [tip for tip in itinerary.tips if any(k in tip for k in ["天气", "有雨", "高温"])]
    time_tips = [tip for tip in itinerary.tips if any(k in tip for k in ["时间预算", "裁剪", "预算"])]
    opening_tips = [tip for tip in itinerary.tips if any(k in tip for k in ["营业", "时段", "跳过"])]
    other_tips = [tip for tip in itinerary.tips if tip not in weather_tips + time_tips + opening_tips]

    parts: List[str] = []
    if period_tip:
        parts.append(period_tip)
    if weather_tips:
        parts.append("天气方面：" + "；".join(weather_tips))
    if time_tips:
        parts.append("时间方面：" + "；".join(time_tips))
    if opening_tips:
        parts.append("营业时间方面：" + "；".join(opening_tips))
    if other_tips:
        parts.append("补充建议：" + "；".join(other_tips))
    if knowledge_hints:
        notes = knowledge_hints.get("notes") or []
        if notes:
            parts.append("场景信息：" + "；".join(notes[:1]))
    return "\n".join(parts)


def render_itinerary_text(
    itinerary: ItineraryResponse,
    request: PlanRequest | None = None,
    parsed_request: PlanRequest | None = None,
) -> Dict[str, str]:
    """Render structured itinerary into readable Chinese copy.

    Pure rule-template renderer (no LLM).
    """
    effective_request = parsed_request or request
    knowledge_hints = _collect_knowledge_hints(itinerary=itinerary, request=effective_request)
    return {
        "title": _title_from_request(effective_request),
        "overview": _overview_text(itinerary, effective_request, knowledge_hints),
        "schedule_text": _schedule_text(itinerary, effective_request, knowledge_hints),
        "transport_text": _transport_text(itinerary),
        "tips_text": _tips_text(itinerary, effective_request, knowledge_hints),
    }
