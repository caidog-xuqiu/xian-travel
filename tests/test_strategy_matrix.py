from __future__ import annotations

from app.models.schemas import PlanRequest
from app.services.strategy_matrix import resolve_strategy_matrix


def _request(**overrides) -> PlanRequest:
    payload = {
        "companion_type": "solo",
        "available_hours": 4,
        "budget_level": "medium",
        "purpose": "tourism",
        "need_meal": True,
        "walking_tolerance": "medium",
        "weather": "sunny",
        "origin": "钟楼",
        "preferred_period": None,
        "origin_preference_mode": None,
    }
    payload.update(overrides)
    return PlanRequest(**payload)


def test_parents_low_walking_matrix() -> None:
    matrix = resolve_strategy_matrix(_request(companion_type="parents", walking_tolerance="low"))
    assert "relaxed" in matrix["primary_strategies"]
    assert "nearby" in matrix["primary_strategies"]
    assert "fewer_cross_cluster" in matrix["candidate_biases"]


def test_rainy_matrix() -> None:
    matrix = resolve_strategy_matrix(_request(weather="rainy"))
    assert "indoor" in matrix["primary_strategies"]
    assert "prioritize_indoor" in matrix["candidate_biases"]


def test_partner_evening_matrix() -> None:
    matrix = resolve_strategy_matrix(_request(companion_type="partner", preferred_period="evening", purpose="dating"))
    assert "night" in matrix["primary_strategies"]
    assert "include_meal_stop" in matrix["candidate_biases"]
    assert "prioritize_night_view" in matrix["candidate_biases"]


def test_tourism_morning_matrix() -> None:
    matrix = resolve_strategy_matrix(_request(purpose="tourism", preferred_period="morning"))
    assert any(item in matrix["primary_strategies"] for item in ["classic", "museum", "landmark"])


def test_need_meal_midday_matrix() -> None:
    matrix = resolve_strategy_matrix(_request(need_meal=True, preferred_period="midday"))
    assert "food" in matrix["primary_strategies"]
    assert "include_meal_stop" in matrix["candidate_biases"]


def test_relax_matrix_includes_relaxed_and_park_bias() -> None:
    matrix = resolve_strategy_matrix(_request(purpose="relax"))
    assert "relaxed" in matrix["primary_strategies"]
    assert "park" in matrix["secondary_strategies"]
    assert "prefer_park_scene" in matrix["candidate_biases"]
