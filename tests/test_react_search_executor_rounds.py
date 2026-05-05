from __future__ import annotations

from app.services import react_search_executor as executor


def _context() -> dict:
    return {
        "companion_type": "partner",
        "available_hours": 4,
        "budget_level": "medium",
        "purpose": "dating",
        "need_meal": False,
        "walking_tolerance": "low",
        "weather": "sunny",
        "origin": "??",
    }


def test_react_executor_runs_multiple_rounds_until_finish(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REACT_SEARCH", "1")

    actions = iter(
        [
            (
                {
                    "decision": "search_poi",
                    "reason": "find scenic anchor",
                    "tool": "amap_search",
                    "tool_input": {"query": "?? ??"},
                    "constraints": {"need_meal": False, "low_walk": True, "max_hours": 4},
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
                    "reason": "enough data",
                    "tool": "none",
                    "tool_input": {},
                    "constraints": {"need_meal": False, "low_walk": True, "max_hours": 4},
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
        return {"success": True, "result_count": 0, "finished": True}

    monkeypatch.setattr(executor, "build_next_action", _next_action)
    monkeypatch.setattr(executor, "execute_search_action", _tool)
    monkeypatch.setattr(
        executor,
        "evaluate_constraints",
        lambda *_args, **_kwargs: {"status": "ok", "violations": [], "has_meal": False, "low_walk_ratio": 1.0, "indoor_ratio": 0.0},
    )

    result = executor.run_react_search(
        user_query="??????",
        request_context=_context(),
        initial_search_plan={"search_plan_summary": "???????"},
        runtime_context={},
        max_rounds=4,
    )

    assert result["success"] is True
    assert len(result["react_steps"]) == 2
    assert result["search_rounds_debug"]
    assert result["llm_search_planner_called"] is True
    assert result["final_search_queries"] == ["?? ??"]
