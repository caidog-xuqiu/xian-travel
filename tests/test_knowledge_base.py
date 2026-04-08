from __future__ import annotations

from app.services.knowledge_base import retrieve_knowledge


def test_retrieve_knowledge_matches_rainy_parents_meal_context() -> None:
    snippets = retrieve_knowledge(
        {
            "weather": "rainy",
            "companion_type": "parents",
            "purpose": "tourism",
            "need_meal": True,
            "available_hours": 3.0,
            "walking_tolerance": "low",
            "budget_level": "medium",
        },
        top_k=6,
    )
    ids = {item["id"] for item in snippets}
    assert "k_rainy_indoor" in ids
    assert "k_parents_relaxed" in ids
    assert "k_need_meal_anchor" in ids


def test_retrieve_knowledge_matches_dating_night_context() -> None:
    snippets = retrieve_knowledge(
        {
            "weather": "sunny",
            "companion_type": "partner",
            "purpose": "dating",
            "need_meal": True,
            "available_hours": 4.0,
            "walking_tolerance": "medium",
            "budget_level": "medium",
            "preferred_period": "evening",
        },
        top_k=4,
    )
    ids = {item["id"] for item in snippets}
    assert "k_dating_night_view" in ids
