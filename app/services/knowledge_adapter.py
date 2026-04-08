from __future__ import annotations

from typing import Any, Dict, Iterable, List

_BIAS_KEYS = [
    "prefer_indoor",
    "prefer_single_cluster",
    "prefer_low_walk",
    "prefer_meal_experience",
    "prefer_night_view",
    "prefer_budget_friendly",
    "avoid_too_many_stops",
    "prefer_lively_places",
]

_TAG_TO_BIAS = {
    "rainy": [("prefer_indoor", 2), ("prefer_low_walk", 1), ("prefer_single_cluster", 1)],
    "indoor": [("prefer_indoor", 2)],
    "single_cluster": [("prefer_single_cluster", 2)],
    "short_transfer": [("prefer_single_cluster", 1), ("avoid_too_many_stops", 1)],
    "low_walk": [("prefer_low_walk", 2), ("avoid_too_many_stops", 1)],
    "avoid_many_stops": [("avoid_too_many_stops", 2)],
    "meal": [("prefer_meal_experience", 2)],
    "meal_experience": [("prefer_meal_experience", 2)],
    "food_friendly": [("prefer_meal_experience", 1)],
    "night": [("prefer_night_view", 2)],
    "photo": [("prefer_night_view", 1)],
    "dating": [("prefer_night_view", 1), ("prefer_meal_experience", 1)],
    "budget_low": [("prefer_budget_friendly", 2)],
    "budget_friendly": [("prefer_budget_friendly", 2)],
    "friends": [("prefer_lively_places", 1)],
    "lively": [("prefer_lively_places", 2)],
    "multi_spots": [("prefer_lively_places", 1)],
    "parents": [("prefer_low_walk", 1), ("prefer_single_cluster", 1)],
}


def _iter_tags(snippets: List[Dict[str, Any]]) -> Iterable[str]:
    for snippet in snippets:
        for tag in snippet.get("tags") or []:
            norm = str(tag).strip().lower()
            if norm:
                yield norm


def build_knowledge_bias(snippets: List[Dict[str, Any]]) -> Dict[str, Any]:
    weights = {key: 0 for key in _BIAS_KEYS}
    knowledge_ids: List[str] = []
    explanation_basis: List[str] = []

    for snippet in snippets or []:
        snippet_id = str(snippet.get("id") or "").strip()
        if snippet_id:
            knowledge_ids.append(snippet_id)
        title = str(snippet.get("title") or "").strip()
        content = str(snippet.get("content") or "").strip()
        if title and content:
            explanation_basis.append(f"{title}：{content}")

    for tag in _iter_tags(snippets or []):
        for key, delta in _TAG_TO_BIAS.get(tag, []):
            weights[key] += int(delta)

    bias = {key: bool(weights[key] > 0) for key in _BIAS_KEYS}
    bias["weights"] = weights
    bias["knowledge_ids"] = list(dict.fromkeys(knowledge_ids))
    bias["explanation_basis"] = list(dict.fromkeys(explanation_basis))[:4]
    return bias
