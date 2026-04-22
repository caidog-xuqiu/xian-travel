from __future__ import annotations

from app.models.schemas import PlanRequest
from app.services.demand_intent import extract_demand_profile


def _sample_request(**overrides) -> PlanRequest:
    payload = {
        "companion_type": "solo",
        "available_hours": 4,
        "budget_level": "medium",
        "purpose": "tourism",
        "need_meal": True,
        "walking_tolerance": "medium",
        "weather": "sunny",
        "origin": "钟楼",
    }
    payload.update(overrides)
    return PlanRequest(**payload)


def test_extract_demand_profile_handles_multi_intent() -> None:
    profile = extract_demand_profile("想去公园散步，再吃烧烤，晚上看看夜景", request=_sample_request())
    tags = set(profile["demand_tags"])
    assert {"park", "bbq", "night_view"}.issubset(tags)
    assert "park" in profile["primary_strategies"]
    assert "food" in profile["primary_strategies"]
    assert "prioritize_night_view" in profile["candidate_biases"]
    assert profile["query_keywords"]


def test_extract_demand_profile_marks_budget_bias() -> None:
    profile = extract_demand_profile("预算低一点，想吃点便宜的")
    assert "budget" in profile["demand_tags"]
    assert "prefer_budget_friendly" in profile["candidate_biases"]
