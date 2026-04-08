from __future__ import annotations

from app.services.knowledge_adapter import build_knowledge_bias


def test_build_knowledge_bias_generates_expected_flags() -> None:
    snippets = [
        {
            "id": "k1",
            "title": "雨天优先室内点位",
            "content": "雨天优先室内，减少步行。",
            "tags": ["rainy", "indoor", "low_walk"],
        },
        {
            "id": "k2",
            "title": "约会夜游",
            "content": "保留夜景与晚餐衔接。",
            "tags": ["dating", "night", "meal"],
        },
        {
            "id": "k3",
            "title": "预算控制",
            "content": "低预算优先性价比。",
            "tags": ["budget_low", "budget_friendly"],
        },
    ]
    bias = build_knowledge_bias(snippets)

    assert bias["prefer_indoor"] is True
    assert bias["prefer_low_walk"] is True
    assert bias["prefer_night_view"] is True
    assert bias["prefer_meal_experience"] is True
    assert bias["prefer_budget_friendly"] is True
    assert "k1" in bias["knowledge_ids"] and "k2" in bias["knowledge_ids"]
    assert bias["explanation_basis"]
