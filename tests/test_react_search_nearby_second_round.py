from __future__ import annotations

from app.services import react_search_executor as executor


def _context() -> dict:
    return {
        "companion_type": "partner",
        "available_hours": 4,
        "budget_level": "medium",
        "purpose": "dating",
        "need_meal": True,
        "walking_tolerance": "low",
        "weather": "sunny",
        "origin": "??",
    }


def test_react_second_round_nearby_keeps_anchor_metadata(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REACT_SEARCH", "1")

    actions = iter(
        [
            (
                {
                    "decision": "search_poi",
                    "reason": "find anchor",
                    "tool": "amap_search",
                    "tool_input": {"query": "?? ??"},
                    "constraints": {"need_meal": True, "low_walk": True, "max_hours": 4},
                },
                {
                    "llm_search_planner_called": True,
                    "llm_search_planner_success": True,
                    "llm_search_planner_error_type": None,
                    "llm_search_planner_error_message": None,
                },
            ),
            (
                {
                    "decision": "search_nearby",
                    "reason": "search meal around anchor",
                    "tool": "amap_nearby",
                    "tool_input": {"query": "??", "anchor": "park-1", "radius_meters": 1800},
                    "constraints": {"need_meal": True, "low_walk": True, "max_hours": 4},
                },
                {
                    "llm_search_planner_called": True,
                    "llm_search_planner_success": True,
                    "llm_search_planner_error_type": None,
                    "llm_search_planner_error_message": None,
                },
            ),
            (
                {
                    "decision": "finish",
                    "reason": "enough",
                    "tool": "none",
                    "tool_input": {},
                    "constraints": {"need_meal": True, "low_walk": True, "max_hours": 4},
                },
                {
                    "llm_search_planner_called": True,
                    "llm_search_planner_success": True,
                    "llm_search_planner_error_type": None,
                    "llm_search_planner_error_message": None,
                },
            ),
        ]
    )

    def _next_action(*_args, **_kwargs):
        return next(actions)

    def _tool(action, _request_context, _runtime_context):
        if action["decision"] == "search_poi":
            return {
                "success": True,
                "result_count": 1,
                "pois": [
                    {
                        "id": "park-1",
                        "name": "???????",
                        "kind": "sight",
                        "walking_level": "low",
                        "indoor_or_outdoor": "outdoor",
                    }
                ],
            }
        if action["decision"] == "search_nearby":
            return {
                "success": True,
                "result_count": 1,
                "pois": [
                    {
                        "id": "food-1",
                        "name": "?????",
                        "kind": "restaurant",
                        "walking_level": "low",
                        "indoor_or_outdoor": "indoor",
                        "anchor_poi_id": "park-1",
                        "anchor_poi_name": "???????",
                        "distance_to_anchor_m": 420,
                        "derived_round": 2,
                    }
                ],
            }
        return {"success": True, "result_count": 0, "finished": True}

    monkeypatch.setattr(executor, "build_next_action", _next_action)
    monkeypatch.setattr(executor, "execute_search_action", _tool)
    monkeypatch.setattr(
        executor,
        "evaluate_constraints",
        lambda *_args, **_kwargs: {"status": "ok", "violations": [], "has_meal": True, "low_walk_ratio": 1.0, "indoor_ratio": 0.5},
    )

    result = executor.run_react_search(
        user_query="??????????????",
        request_context=_context(),
        initial_search_plan={},
        runtime_context={},
        max_rounds=4,
    )

    assert result["success"] is True
    hit = next(item for item in result["discovered_pois"] if item.get("id") == "food-1")
    assert hit["anchor_poi_id"] == "park-1"
    assert hit["anchor_poi_name"] == "???????"
    assert hit["derived_round"] == 2
    assert isinstance(hit["distance_to_anchor_m"], int)
