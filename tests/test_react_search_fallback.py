from __future__ import annotations

from app.services import react_search_executor as executor


def _context() -> dict:
    return {
        "companion_type": "solo",
        "available_hours": 3,
        "budget_level": "medium",
        "purpose": "tourism",
        "need_meal": False,
        "walking_tolerance": "medium",
        "weather": "sunny",
        "origin": "??",
    }


def test_react_executor_fallback_when_llm_returns_fallback(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REACT_SEARCH", "1")

    monkeypatch.setattr(
        executor,
        "build_next_action",
        lambda *_args, **_kwargs: (
            {
                "decision": "fallback",
                "reason": "llm_timeout",
                "tool": "none",
                "tool_input": {},
                "constraints": {"max_hours": 3},
            },
            {
                "llm_search_planner_called": True,
                "llm_search_planner_success": False,
                "llm_search_planner_error_type": "TimeoutError",
                "llm_search_planner_error_message": "planner timeout",
            },
        ),
    )

    result = executor.run_react_search(
        user_query="test",
        request_context=_context(),
        initial_search_plan={},
        runtime_context={},
        max_rounds=4,
    )

    assert result["success"] is False
    assert result["react_fallback_reason"] == "llm_timeout"
    assert result["llm_search_planner_called"] is True
