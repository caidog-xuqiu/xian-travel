from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _has_meal(pois: Iterable[Dict[str, Any]]) -> bool:
    for poi in pois:
        if str((poi or {}).get("kind") or "") == "restaurant":
            return True
    return False


def _low_walk_ratio(pois: Iterable[Dict[str, Any]]) -> float:
    total = 0
    low = 0
    for poi in pois:
        total += 1
        level = str((poi or {}).get("walking_level") or "")
        if level in {"low", "medium"}:
            low += 1
    if total == 0:
        return 0.0
    return low / total


def _indoor_ratio(pois: Iterable[Dict[str, Any]]) -> float:
    total = 0
    indoor = 0
    for poi in pois:
        total += 1
        if str((poi or {}).get("indoor_or_outdoor") or "") == "indoor":
            indoor += 1
    if total == 0:
        return 0.0
    return indoor / total


def evaluate_constraints(
    request_context: Dict[str, Any] | Any,
    discovered_pois: List[Dict[str, Any]] | None,
    *,
    constraints_hint: Dict[str, Any] | None = None,
    react_steps: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    context = request_context.model_dump() if hasattr(request_context, "model_dump") else dict(request_context or {})
    pois = list(discovered_pois or [])
    constraints = dict(constraints_hint or {})
    violations: List[str] = []

    need_meal = bool(constraints.get("need_meal", _value(context.get("need_meal"))))
    low_walk = bool(constraints.get("low_walk", str(_value(context.get("walking_tolerance")) or "") == "low"))
    rainy = bool(constraints.get("rainy", str(_value(context.get("weather")) or "") == "rainy"))
    budget_low = bool(constraints.get("budget_low", str(_value(context.get("budget_level")) or "") == "low"))

    try:
        max_hours = float(constraints.get("max_hours", _value(context.get("available_hours")) or 0) or 0)
    except (TypeError, ValueError):
        max_hours = 0

    if need_meal and not _has_meal(pois):
        violations.append("need_meal_unmet")
    if low_walk and _low_walk_ratio(pois) < 0.5:
        violations.append("low_walk_risk")
    if rainy and (_indoor_ratio(pois) < 0.34 and _low_walk_ratio(pois) < 0.5):
        violations.append("rainy_exposure_risk")

    if budget_low:
        high_price_hits = 0
        for poi in pois:
            tags = " ".join(str(x) for x in (poi.get("tags") or []))
            text = (str(poi.get("name") or "") + " " + str(poi.get("category") or "") + " " + tags).lower()
            if any(token in text for token in ["fine dining", "奢", "高端", "米其林", "black pearl"]):
                high_price_hits += 1
        if high_price_hits >= 2:
            violations.append("budget_low_risk")

    if max_hours > 0:
        route_minutes = []
        for step in react_steps or []:
            estimate = (step.get("tool_result") or {}).get("route_duration_minutes")
            if estimate is not None:
                try:
                    route_minutes.append(float(estimate))
                except (TypeError, ValueError):
                    pass
        if route_minutes and max(route_minutes) > max_hours * 60 * 1.2:
            violations.append("time_budget_risk")

    if not violations:
        status = "ok"
    elif len(violations) <= 2:
        status = "needs_revise"
    else:
        status = "hard_fail"

    return {
        "status": status,
        "violations": violations,
        "low_walk_ratio": round(_low_walk_ratio(pois), 4),
        "indoor_ratio": round(_indoor_ratio(pois), 4),
        "has_meal": _has_meal(pois),
    }
