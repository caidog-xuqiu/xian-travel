from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "travel_knowledge.json"
_HIGH_SIGNAL_TAGS = {
    "rainy",
    "parents",
    "dating",
    "friends",
    "meal",
    "low_walk",
    "short_time",
    "budget_low",
    "night",
}


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value).strip().lower()
    return str(value).strip().lower()


@lru_cache(maxsize=1)
def _load_knowledge_items() -> List[Dict[str, Any]]:
    if not _DATA_PATH.exists():
        return []
    payload = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else []
    normalized: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        snippet_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not snippet_id or not title or not content:
            continue
        tags = [str(tag).strip().lower() for tag in (item.get("tags") or []) if str(tag).strip()]
        normalized.append(
            {
                "id": snippet_id,
                "category": str(item.get("category") or "").strip().lower(),
                "tags": tags,
                "title": title,
                "content": content,
            }
        )
    return normalized


def _context_tags(request_context: Dict[str, Any]) -> Set[str]:
    tags: Set[str] = set()
    weather = _to_text(request_context.get("weather"))
    companion = _to_text(request_context.get("companion_type"))
    purpose = _to_text(request_context.get("purpose"))
    period = _to_text(request_context.get("preferred_period"))
    walking = _to_text(request_context.get("walking_tolerance"))
    budget = _to_text(request_context.get("budget_level"))
    style = _to_text(request_context.get("preferred_trip_style"))

    if weather == "rainy":
        tags.update({"rainy", "indoor", "low_walk", "single_cluster"})
    if companion in {"parents", "friends", "partner"}:
        tags.add(companion)
    if purpose in {"dating", "food", "relax", "tourism"}:
        tags.add(purpose)
    if period == "evening":
        tags.update({"night"})
    if request_context.get("need_meal") is True:
        tags.update({"meal", "meal_experience", "food_friendly"})
    if walking == "low":
        tags.update({"low_walk", "avoid_many_stops"})
    if budget == "low":
        tags.update({"budget_low", "budget_friendly"})
    if style == "relaxed":
        tags.update({"avoid_many_stops", "single_cluster"})
    if style == "dense":
        tags.update({"multi_spots"})

    available_hours = request_context.get("available_hours")
    try:
        if available_hours is not None and float(available_hours) <= 3.5:
            tags.update({"short_time", "avoid_many_stops", "single_cluster"})
    except (TypeError, ValueError):
        pass

    if purpose == "dating":
        tags.update({"photo", "night", "meal"})
    if companion == "friends":
        tags.update({"lively", "multi_spots"})
    if companion == "parents":
        tags.update({"low_walk", "single_cluster"})

    return tags


def _score_tags(context_tags: Set[str], snippet_tags: Iterable[str]) -> int:
    score = 0
    for tag in snippet_tags:
        if tag not in context_tags:
            continue
        score += 2
        if tag in _HIGH_SIGNAL_TAGS:
            score += 2
    return score


def retrieve_knowledge(request_context: Dict[str, Any], top_k: int = 3) -> List[Dict[str, Any]]:
    """Retrieve top-k local rule snippets by simple tag matching."""

    if top_k <= 0:
        return []

    context_tags = _context_tags(request_context or {})
    if not context_tags:
        return []

    scored: List[Dict[str, Any]] = []
    for snippet in _load_knowledge_items():
        tags = [str(tag).lower() for tag in (snippet.get("tags") or [])]
        score = _score_tags(context_tags, tags)
        if score <= 0:
            continue
        item = dict(snippet)
        item["score"] = score
        scored.append(item)

    scored.sort(key=lambda x: (int(x.get("score") or 0), len(x.get("tags") or []), x.get("id", "")), reverse=True)
    return scored[:top_k]
