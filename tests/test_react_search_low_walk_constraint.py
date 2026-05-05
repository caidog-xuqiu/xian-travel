from __future__ import annotations

from app.services import react_search_executor as executor


def _context() -> dict:
    return {
        "companion_type": "parents",
        "available_hours": 3,
        "budget_level": "medium",
        "purpose": "tourism",
        "need_meal": False,
        "walking_tolerance": "low",
        "weather": "sunny",
        "origin": "??",
    }


def test_react_low_walk_constraint_not_overridden_by_retrieved_cases(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REACT_SEARCH", "1")

    actions = iter(
        [
            (
                {
                    "decision": "retrieve_cases",
                    "reason": "retrieve similar routes",
                    "tool": "pinecone_cases",
                    "tool_input": {"query": "?? ???", "top_k": 3},
                    "constraints": {"low_walk": True, "max_hours": 3},
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
                    "reason": "try finish once",
                    "tool": "none",
                    "tool_input": {},
                    "constraints": {"low_walk": True, "max_hours": 3},
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
                    "reason": "second finish",
                    "tool": "none",
                    "tool_input": {},
                    "constraints": {"low_walk": True, "max_hours": 3},
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
        if action["decision"] == "retrieve_cases":
            return {
                "success": True,
                "result_count": 1,
                "cases": [{"id": "case-1", "score": 0.91, "summary": "???"}],
            }
        return {"success": True, "result_count": 0, "finished": True}

    monkeypatch.setattr(executor, "build_next_action", _next_action)
    monkeypatch.setattr(executor, "execute_search_action", _tool)

    result = executor.run_react_search(
        user_query="?????????",
        request_context=_context(),
        initial_search_plan={},
        runtime_context={
            "discovered_pois": [
                {
                    "id": "s1",
                    "name": "??????",
                    "kind": "sight",
                    "walking_level": "high",
                    "indoor_or_outdoor": "outdoor",
                }
            ]
        },
        max_rounds=4,
    )

    assert result["success"] is False
    assert result["react_fallback_reason"].startswith("constraint_guard_")
