from typing import Any, Dict, List

from app.models.schemas import PlanRequest


def _is_inferred(poi: Dict[str, Any], field: str) -> bool:
    inferred = poi.get("inferred_fields") or []
    return isinstance(inferred, list) and field in inferred


def _field_weight(
    poi: Dict[str, Any],
    field: str,
    inferred_scale: float = 0.5,
    mock_scale: float = 0.75,
) -> float:
    """Field reliability weight.

    - Real fields (amap and not inferred): 1.0
    - Inferred fields: inferred_scale
    - Mock fields: mock_scale
    """
    if _is_inferred(poi, field):
        return inferred_scale
    if poi.get("poi_source") == "mock":
        return mock_scale
    return 1.0


def _resolve_weather_flags(
    request: PlanRequest,
    weather_context: Dict[str, Any] | None = None,
) -> tuple[str, bool, bool]:
    """Normalize weather source.

    - If real weather available: use weather_service flags.
    - Else fallback to request.weather.
    """
    if weather_context:
        condition = str(weather_context.get("weather_condition") or request.weather.value)
        is_rainy = bool(weather_context.get("is_rainy"))
        is_hot = bool(weather_context.get("is_hot"))
        return condition, is_rainy, is_hot

    manual = request.weather.value
    return manual, manual == "rainy", manual == "hot"


def score_poi(
    poi: Dict[str, Any],
    request: PlanRequest,
    weather_context: Dict[str, Any] | None = None,
) -> float:
    """Score a single POI.

    Notes:
    - POI may come from amap or mock; inferred fields get conservative weights.
    - Weather uses real weather when available, otherwise request.weather.
    - V1 scope: Xi'an core districts with 4 fixed clusters.
    - Fields not used here: origin/has_car/transport_preference (planner only).
    """
    score = 50.0
    tags = poi.get("tags", [])
    kind = poi.get("kind")
    weather_condition, is_rainy, is_hot = _resolve_weather_flags(request, weather_context)
    severe_weather = is_rainy or is_hot

    # 1) Weather bias
    indoor_weight = _field_weight(poi, "indoor_or_outdoor", inferred_scale=0.55, mock_scale=0.8)
    if severe_weather:
        if poi.get("indoor_or_outdoor") == "indoor":
            score += 12 * indoor_weight
        elif poi.get("indoor_or_outdoor") == "outdoor":
            score -= 6 * indoor_weight
    elif weather_condition in {"cold", "寒冷"} and poi.get("indoor_or_outdoor") == "indoor":
        score += 4 * indoor_weight

    visit_weight = _field_weight(poi, "estimated_visit_minutes", inferred_scale=0.6, mock_scale=0.85)
    if is_hot and poi.get("indoor_or_outdoor") == "outdoor":
        visit_minutes = int(poi.get("estimated_visit_minutes", 60))
        if visit_minutes >= 80:
            score -= 5 * visit_weight
        elif visit_minutes >= 60:
            score -= 2 * visit_weight

    # 2) Companion preference
    parent_weight = _field_weight(poi, "parent_friendly", inferred_scale=0.45, mock_scale=0.75)
    friend_weight = _field_weight(poi, "friend_friendly", inferred_scale=0.45, mock_scale=0.75)
    couple_weight = _field_weight(poi, "couple_friendly", inferred_scale=0.45, mock_scale=0.75)
    walking_weight = _field_weight(poi, "walking_level", inferred_scale=0.6, mock_scale=0.85)
    tags_weight = _field_weight(poi, "tags", inferred_scale=0.5, mock_scale=0.8)

    if severe_weather and ("night_view" in tags or "night_tour" in tags):
        score -= 4 * tags_weight

    if request.companion_type == "parents":
        if poi.get("parent_friendly"):
            score += 14 * parent_weight
        if poi.get("walking_level") == "low":
            score += 8 * walking_weight
        if poi.get("indoor_or_outdoor") == "indoor":
            score += 6 * indoor_weight
        if kind == "restaurant" and "proper_meal" in tags:
            score += 6 * tags_weight

    elif request.companion_type == "friends":
        if poi.get("friend_friendly"):
            score += 10 * friend_weight
        if any(tag in tags for tag in ["lively", "food", "night_view"]):
            score += 6 * tags_weight

    elif request.companion_type == "partner":
        if poi.get("couple_friendly"):
            score += 10 * couple_weight
        if poi.get("photo_friendly"):
            score += 6 * _field_weight(poi, "photo_friendly", inferred_scale=0.6, mock_scale=0.85)
        if any(tag in tags for tag in ["night_view", "cafe", "dessert", "photo"]):
            score += 6 * tags_weight

    # 3) Purpose (coarse)
    if request.purpose == "tourism":
        if kind == "sight" and any(tag in tags for tag in ["classic", "history", "landmark"]):
            score += 10 * tags_weight
    elif request.purpose == "food":
        if kind == "restaurant":
            score += 12
        if "food" in tags:
            score += 5 * tags_weight
    elif request.purpose == "dating":
        if poi.get("couple_friendly"):
            score += 8 * couple_weight
        if any(tag in tags for tag in ["night_view", "photo", "cafe", "dessert"]):
            score += 6 * tags_weight
    elif request.purpose == "relax":
        if poi.get("walking_level") == "low":
            score += 8 * walking_weight
        if poi.get("indoor_or_outdoor") == "indoor":
            score += 4 * indoor_weight

    # 4) walking_tolerance
    if request.walking_tolerance == "low":
        walking_level = poi.get("walking_level")
        if walking_level == "high":
            score -= 15 * walking_weight
        elif walking_level == "medium":
            score -= 8 * walking_weight
        else:
            score += 4 * walking_weight

    # 5) budget_level
    cost_weight = _field_weight(poi, "cost_level", inferred_scale=0.7, mock_scale=0.9)
    if request.budget_level == "low":
        cost_level = poi.get("cost_level")
        if cost_level == "high":
            score -= 12 * cost_weight
        elif cost_level == "medium":
            score -= 4 * cost_weight
        else:
            score += 4 * cost_weight

    # 6) need_meal -> light boost for restaurants
    if request.need_meal and kind == "restaurant":
        score += 3

    # 7) preferred_period -> light bias (no major re-ranking)
    period = request.preferred_period
    if period:
        period_weight = _field_weight(poi, "tags", inferred_scale=0.4, mock_scale=0.7)
        if period == "morning":
            if any(tag in tags for tag in ["classic", "history", "museum", "landmark"]):
                score += 4 * period_weight
            if any(tag in tags for tag in ["night_view", "night_tour"]):
                score -= 4 * period_weight
        elif period == "midday":
            if request.need_meal and kind == "restaurant":
                score += 8
            if any(tag in tags for tag in ["night_view", "night_tour"]):
                score -= 2 * period_weight
        elif period == "afternoon":
            if any(tag in tags for tag in ["park", "relax", "photo"]):
                score += 3 * period_weight
            if any(tag in tags for tag in ["night_view", "night_tour"]):
                score -= 2 * period_weight
        elif period == "evening":
            if any(tag in tags for tag in ["night_view", "night_tour"]):
                score += 6 * period_weight
            if any(tag in tags for tag in ["museum", "history"]):
                score -= 2 * period_weight

    # V2 placeholders kept out of main scoring chain.
    # TODO(V2): has_car + parking_tolerance -> parking friendliness
    # TODO(V2): transport_preference + traffic_sensitivity -> traffic penalty
    # TODO(V2): taste_preferences -> flavor match
    # TODO(V2): restaurant_rating_preference -> rating reweight
    # TODO(V2): avoid_queue -> queue penalty
    # TODO(V2): preferred_trip_style -> density & dwell time

    return round(score, 2)


def sort_by_score(
    pois: List[Dict[str, Any]],
    request: PlanRequest,
    weather_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Sort by score desc and attach _score."""
    scored: List[Dict[str, Any]] = []
    for poi in pois:
        item = dict(poi)
        item["_score"] = score_poi(item, request, weather_context=weather_context)
        scored.append(item)

    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored


