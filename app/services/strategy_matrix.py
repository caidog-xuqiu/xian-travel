from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from app.models.schemas import PlanRequest


@dataclass
class StrategyContext:
    companion_type: str
    purpose: str
    preferred_period: str | None
    weather: str
    walking_tolerance: str
    need_meal: bool
    origin_preference_mode: str | None


def _extract_context(state_or_request: Any) -> StrategyContext | None:
    request: PlanRequest | None = None
    if isinstance(state_or_request, PlanRequest):
        request = state_or_request
    elif hasattr(state_or_request, "parsed_request"):
        request = getattr(state_or_request, "parsed_request")

    if request is None:
        return None

    return StrategyContext(
        companion_type=request.companion_type.value,
        purpose=request.purpose.value,
        preferred_period=request.preferred_period,
        weather=request.weather.value,
        walking_tolerance=request.walking_tolerance.value,
        need_meal=bool(request.need_meal),
        origin_preference_mode=request.origin_preference_mode,
    )


def _append_unique(target: List[str], values: List[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def resolve_strategy_matrix(state_or_request: Any) -> Dict[str, List[str]]:
    """Resolve lightweight strategy matrix for search and candidate bias.

    Returns:
    - primary_strategies
    - secondary_strategies
    - candidate_biases
    - notes
    """
    context = _extract_context(state_or_request)
    if context is None:
        return {
            "primary_strategies": ["classic"],
            "secondary_strategies": [],
            "candidate_biases": [],
            "notes": ["缺少解析请求，回退经典策略。"],
        }

    primary: List[str] = []
    secondary: List[str] = []
    biases: List[str] = []
    notes: List[str] = []

    # High-value rules:
    if context.companion_type == "parents" and context.walking_tolerance == "low":
        _append_unique(primary, ["relaxed", "nearby"])
        _append_unique(biases, ["fewer_cross_cluster", "prefer_origin_cluster_first"])
        notes.append("陪父母 + 低步行，优先轻松且少跨簇。")

    if context.weather in {"rainy", "hot"}:
        _append_unique(primary, ["indoor"])
        _append_unique(biases, ["prioritize_indoor"])
        notes.append("雨天/高温，优先室内点位。")

    if context.companion_type == "partner" and context.preferred_period == "evening":
        _append_unique(primary, ["night"])
        _append_unique(secondary, ["food"])
        _append_unique(biases, ["prioritize_night_view", "include_meal_stop"])
        notes.append("晚间约会，优先夜游与晚餐衔接。")

    if context.need_meal and context.preferred_period == "midday":
        _append_unique(primary, ["food"])
        _append_unique(biases, ["include_meal_stop"])
        notes.append("午间出行且需要用餐，优先保留正餐。")

    if context.purpose == "tourism" and context.preferred_period == "morning":
        _append_unique(primary, ["classic", "museum", "landmark"])
        _append_unique(biases, ["prioritize_landmarks"])
        notes.append("上午旅游，优先经典地标与文博。")

    if context.origin_preference_mode == "nearby":
        _append_unique(biases, ["prefer_origin_cluster_first"])
        if "nearby" not in primary:
            _append_unique(secondary, ["nearby"])
        notes.append("识别到附近语义，优先起点邻近簇。")

    if context.purpose == "relax" or context.walking_tolerance == "low":
        _append_unique(biases, ["prioritize_relaxed_pacing", "fewer_cross_cluster"])
        if "relaxed" not in primary:
            _append_unique(secondary, ["relaxed"])
        notes.append("放松/低步行诉求，优先轻松节奏。")

    # Base fallback strategy:
    if not primary:
        if context.purpose == "food":
            _append_unique(primary, ["food"])
        elif context.preferred_period == "evening":
            _append_unique(primary, ["night"])
        else:
            _append_unique(primary, ["classic"])
        notes.append("未命中特殊组合，采用基础策略。")

    return {
        "primary_strategies": primary,
        "secondary_strategies": secondary,
        "candidate_biases": biases,
        "notes": notes,
    }
