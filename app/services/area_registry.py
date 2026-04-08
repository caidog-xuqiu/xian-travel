from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class AreaSpec:
    area_name: str
    aliases: List[str]
    rough_center: tuple[float, float] | None
    cluster_hints: List[str]
    notes: str


AREA_SPECS: Dict[str, AreaSpec] = {
    "城墙钟鼓楼": AreaSpec(
        area_name="城墙钟鼓楼",
        aliases=["钟楼", "鼓楼", "城墙", "南门", "钟鼓楼", "老城墙"],
        rough_center=(34.2590, 108.9470),
        cluster_hints=["城墙钟鼓楼簇"],
        notes="老城核心地标带，经典打卡密集。",
    ),
    "小寨文博": AreaSpec(
        area_name="小寨文博",
        aliases=["小寨", "陕历博", "陕西历史博物馆", "文博", "赛格"],
        rough_center=(34.2255, 108.9555),
        cluster_hints=["小寨文博簇"],
        notes="文博与商业复合区，午间和室内点较丰富。",
    ),
    "大雁塔": AreaSpec(
        area_name="大雁塔",
        aliases=["大雁塔", "大慈恩寺", "雁塔"],
        rough_center=(34.2186, 108.9633),
        cluster_hints=["大雁塔簇"],
        notes="地标与拍照场景集中，夜间氛围较好。",
    ),
    "曲江夜游": AreaSpec(
        area_name="曲江夜游",
        aliases=["曲江", "不夜城", "芙蓉园", "大唐不夜城", "夜游"],
        rough_center=(34.2143, 108.9752),
        cluster_hints=["曲江夜游簇"],
        notes="夜游氛围区，适合晚间场景。",
    ),
    "回民街": AreaSpec(
        area_name="回民街",
        aliases=["回民街", "北院门", "清真街"],
        rough_center=(34.2634, 108.9447),
        cluster_hints=["城墙钟鼓楼簇"],
        notes="餐饮密度高，偏美食导向。",
    ),
    "高新": AreaSpec(
        area_name="高新",
        aliases=["高新", "高新区", "科技路", "唐延路", "锦业路"],
        rough_center=(34.2320, 108.8900),
        cluster_hints=["小寨文博簇"],
        notes="商务与商圈混合区，室内候选相对充足。",
    ),
    "电视塔会展": AreaSpec(
        area_name="电视塔会展",
        aliases=["电视塔", "会展", "会展中心", "电视塔会展"],
        rough_center=(34.2088, 108.9478),
        cluster_hints=["大雁塔簇"],
        notes="南中轴节点，适合向大雁塔与曲江衔接。",
    ),
    "浐灞未央": AreaSpec(
        area_name="浐灞未央",
        aliases=["浐灞", "未央", "世博园", "奥体", "广运潭"],
        rough_center=(34.3210, 109.0300),
        cluster_hints=["曲江夜游簇", "城墙钟鼓楼簇"],
        notes="东部与北部扩展区域，当前作为轻量大区处理。",
    ),
}

_CLUSTER_TO_AREA = {
    "城墙钟鼓楼簇": "城墙钟鼓楼",
    "小寨文博簇": "小寨文博",
    "大雁塔簇": "大雁塔",
    "曲江夜游簇": "曲江夜游",
}


def list_supported_areas() -> List[Dict[str, Any]]:
    return [
        {
            "area_name": spec.area_name,
            "aliases": list(spec.aliases),
            "rough_center": spec.rough_center,
            "cluster_hints": list(spec.cluster_hints),
            "notes": spec.notes,
        }
        for spec in AREA_SPECS.values()
    ]


def get_default_area_scope() -> List[str]:
    return list(AREA_SPECS.keys())


def _contains_alias(text: str, aliases: List[str]) -> bool:
    lowered = text.lower()
    for alias in aliases:
        if alias and alias.lower() in lowered:
            return True
    return False


def _match_area_from_text(text: str) -> List[str]:
    if not text:
        return []
    hits: List[str] = []
    for area_name, spec in AREA_SPECS.items():
        if area_name in text or _contains_alias(text, spec.aliases):
            hits.append(area_name)
    return hits


def resolve_area_scope_from_request(parsed_request: Any, user_input: str | None) -> Dict[str, Any]:
    text = str(user_input or "")
    origin_text = str(getattr(parsed_request, "origin", "") or "")
    merged_text = f"{text} {origin_text}".strip()

    explicit_hits = _match_area_from_text(merged_text)
    origin_area = explicit_hits[0] if explicit_hits else None

    if not origin_area and origin_text:
        for area_name, spec in AREA_SPECS.items():
            if _contains_alias(origin_text, spec.aliases):
                origin_area = area_name
                break

    base_scope = get_default_area_scope()
    resolved_scope = list(base_scope)
    resolved_from: List[str] = []
    priority_areas: List[str] = []

    if explicit_hits:
        resolved_from.append("text_alias")
        priority_areas.extend(explicit_hits)

    if origin_area:
        resolved_from.append("origin")
        if origin_area not in priority_areas:
            priority_areas.insert(0, origin_area)

    nearby_mode = str(getattr(parsed_request, "origin_preference_mode", "") or "")
    if nearby_mode == "nearby" and origin_area:
        resolved_from.append("nearby_mode")
        if origin_area in resolved_scope:
            resolved_scope.remove(origin_area)
        resolved_scope.insert(0, origin_area)

    if explicit_hits:
        # Put explicitly mentioned areas at front while still keeping full scope.
        for area in reversed(explicit_hits):
            if area in resolved_scope:
                resolved_scope.remove(area)
            resolved_scope.insert(0, area)

    # Keep order stable and unique.
    unique_scope: List[str] = []
    for area in resolved_scope:
        if area not in unique_scope:
            unique_scope.append(area)

    unique_priority: List[str] = []
    for area in priority_areas:
        if area not in unique_priority:
            unique_priority.append(area)

    return {
        "areas": unique_scope,
        "priority_areas": unique_priority,
        "origin_area": origin_area,
        "resolved_from": list(dict.fromkeys(resolved_from)),
    }


def map_place_to_area(place: Dict[str, Any]) -> str | None:
    explicit_area = str(place.get("area_name") or "").strip()
    if explicit_area in AREA_SPECS:
        return explicit_area

    name_text = str(place.get("name") or "")
    if "回民街" in name_text:
        return "回民街"

    matched = _match_area_from_text(name_text)
    if matched:
        return matched[0]

    cluster = str(place.get("district_cluster") or "").strip()
    if cluster in _CLUSTER_TO_AREA:
        return _CLUSTER_TO_AREA[cluster]
    return None
