from __future__ import annotations

import re
from typing import Any, Dict, List

from app.models.schemas import PlanRequest


def _normalize(text: str) -> str:
    normalized = str(text or "").lower().strip()
    return re.sub(r"\s+", "", normalized)


def _append_unique(target: List[str], values: List[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


_RULES: List[Dict[str, Any]] = [
    {
        "tag": "park",
        "keywords": ["公园", "湿地", "植物园", "森林公园", "曲江池", "芙蓉园", "绿道", "草坪", "遛弯", "散步"],
        "primary": ["park"],
        "secondary": ["relaxed"],
        "biases": ["prefer_park_scene", "prefer_single_cluster"],
        "query_keywords": ["西安 公园", "城市公园", "湿地公园"],
        "note": "识别到公园/绿地诉求，优先自然休闲点位。",
    },
    {
        "tag": "bbq",
        "keywords": ["烧烤", "烤串", "bbq", "烤肉", "夜宵"],
        "primary": ["food"],
        "secondary": ["night"],
        "biases": ["include_meal_stop", "prefer_lively_places"],
        "query_keywords": ["西安 烧烤", "西安 烤串", "西安 夜宵"],
        "note": "识别到烧烤诉求，优先餐饮与夜间氛围。",
    },
    {
        "tag": "night_view",
        "keywords": ["夜景", "夜游", "不夜城", "夜拍", "夜里"],
        "primary": ["night"],
        "secondary": [],
        "biases": ["prioritize_night_view"],
        "query_keywords": ["西安 夜景", "西安 夜游"],
        "note": "识别到夜景诉求，优先夜游簇候选。",
    },
    {
        "tag": "museum",
        "keywords": ["博物馆", "展馆", "文博", "美术馆"],
        "primary": ["museum"],
        "secondary": ["indoor", "classic"],
        "biases": ["prioritize_indoor", "prioritize_landmarks"],
        "query_keywords": ["西安 博物馆", "西安 展馆"],
        "note": "识别到文博诉求，优先室内文博点位。",
    },
    {
        "tag": "photo",
        "keywords": ["拍照", "出片", "打卡", "拍照好看"],
        "primary": [],
        "secondary": ["classic"],
        "biases": ["prefer_lively_places"],
        "query_keywords": ["西安 拍照 打卡"],
        "note": "识别到拍照诉求，优先景观与氛围点位。",
    },
    {
        "tag": "indoor",
        "keywords": ["室内", "避雨", "避晒", "不想晒", "下雨也能玩"],
        "primary": ["indoor"],
        "secondary": [],
        "biases": ["prioritize_indoor"],
        "query_keywords": ["西安 室内 景点"],
        "note": "识别到室内诉求，优先室内候选。",
    },
    {
        "tag": "lively",
        "keywords": ["热闹", "人多", "烟火气", "商圈"],
        "primary": [],
        "secondary": [],
        "biases": ["prefer_lively_places"],
        "query_keywords": ["西安 热闹 商圈"],
        "note": "识别到热闹诉求，优先人气区域。",
    },
    {
        "tag": "budget",
        "keywords": ["省钱", "预算低", "低预算", "性价比", "便宜"],
        "primary": [],
        "secondary": [],
        "biases": ["prefer_budget_friendly"],
        "query_keywords": ["西安 性价比 美食", "西安 免费 公园"],
        "note": "识别到预算诉求，优先性价比候选。",
    },
]


def extract_demand_profile(text: str, request: PlanRequest | None = None) -> Dict[str, Any]:
    """Extract lightweight demand tags from free text.

    This is a generic demand-to-strategy adapter layer:
    - demand_tags: semantic tags detected from user expression
    - primary_strategies / secondary_strategies: search strategy boosts
    - candidate_biases: ranking constraints for candidate plans
    - query_keywords: retrieval hints for amap keyword search
    - notes: short explanation for debug/logging
    """

    normalized = _normalize(text)
    demand_tags: List[str] = []
    primary: List[str] = []
    secondary: List[str] = []
    biases: List[str] = []
    query_keywords: List[str] = []
    notes: List[str] = []

    for rule in _RULES:
        keywords = rule.get("keywords") or []
        if not any(str(k).lower() in normalized for k in keywords):
            continue
        _append_unique(demand_tags, [str(rule.get("tag") or "")])
        _append_unique(primary, list(rule.get("primary") or []))
        _append_unique(secondary, list(rule.get("secondary") or []))
        _append_unique(biases, list(rule.get("biases") or []))
        _append_unique(query_keywords, list(rule.get("query_keywords") or []))
        note = str(rule.get("note") or "").strip()
        if note:
            _append_unique(notes, [note])

    if request is not None:
        if request.walking_tolerance.value == "low":
            _append_unique(biases, ["prioritize_relaxed_pacing", "fewer_cross_cluster"])
        if request.need_meal:
            _append_unique(biases, ["include_meal_stop"])
        if request.preferred_period == "evening":
            _append_unique(secondary, ["night"])

    return {
        "demand_tags": demand_tags,
        "primary_strategies": primary,
        "secondary_strategies": secondary,
        "candidate_biases": biases,
        "query_keywords": query_keywords,
        "notes": notes,
    }
