from __future__ import annotations

from app.services import react_search_executor as executor


def _context() -> dict:
    return {
        "companion_type": "solo",
        "available_hours": 4,
        "budget_level": "medium",
        "purpose": "relax",
        "need_meal": False,
        "walking_tolerance": "medium",
        "weather": "sunny",
        "origin": "??",
    }


def test_react_debug_fields_are_stable(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REACT_SEARCH", "1")

    monkeypatch.setattr(
        executor,
        "build_next_action",
        lambda *_args, **_kwargs: (
            {
                "decision": "finish",
                "reason": "enough",
                "tool": "none",
                "tool_input": {},
                "constraints": {"max_hours": 4},
            },
            {
                "llm_search_planner_called": True,
                "llm_search_planner_success": True,
                "llm_search_planner_error_type": None,
                "llm_search_planner_error_message": None,
            },
        ),
    )
    monkeypatch.setattr(executor, "execute_search_action", lambda *_args, **_kwargs: {"success": True, "result_count": 0, "finished": True})
    monkeypatch.setattr(
        executor,
        "evaluate_constraints",
        lambda *_args, **_kwargs: {"status": "ok", "violations": [], "has_meal": False, "low_walk_ratio": 1.0, "indoor_ratio": 0.0},
    )

    result = executor.run_react_search(
        user_query="????",
        request_context=_context(),
        initial_search_plan={"search_plan_summary": "test"},
        runtime_context={},
    )

    required = {
        "success",
        "react_steps",
        "search_rounds_debug",
        "react_fallback_reason",
        "llm_search_planner_called",
        "llm_search_planner_success",
        "llm_search_planner_error_type",
        "llm_search_planner_error_message",
        "final_search_queries",
    }
    assert required.issubset(result.keys())
    assert isinstance(result["react_steps"], list)
    assert isinstance(result["search_rounds_debug"], list)
