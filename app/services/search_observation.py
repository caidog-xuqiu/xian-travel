from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _top_names(items: Iterable[Dict[str, Any]], limit: int = 5) -> List[str]:
    names: List[str] = []
    for item in items:
        name = str((item or {}).get("name") or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _kind_counts(items: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        kind = str((item or {}).get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def build_observation(
    *,
    round_index: int,
    action: Dict[str, Any],
    tool_result: Dict[str, Any] | None,
    discovered_pois: List[Dict[str, Any]] | None,
    react_steps: List[Dict[str, Any]] | None,
) -> Dict[str, Any]:
    result = dict(tool_result or {})
    pois = list(discovered_pois or [])
    steps = list(react_steps or [])
    result_pois = list(result.get("pois") or [])
    summary = {
        "round_index": int(round_index),
        "decision": str(action.get("decision") or ""),
        "tool": str(action.get("tool") or "none"),
        "tool_success": bool(result.get("success", False)),
        "result_count": int(result.get("result_count") or len(result_pois)),
        "discovered_total": len(pois),
        "discovered_kind_counts": _kind_counts(pois),
        "top_candidates": _top_names(pois),
        "last_result_names": _top_names(result_pois),
        "last_fallback_reason": result.get("fallback_reason"),
        "history_size": len(steps),
    }
    if result.get("clarification_needed"):
        summary["clarification_needed"] = True
        summary["clarification_question"] = result.get("clarification_question")
    return summary
