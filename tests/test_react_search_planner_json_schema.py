from __future__ import annotations

from app.services import llm_search_planner as planner


def _context() -> dict:
    return {
        "companion_type": "partner",
        "available_hours": 4,
        "budget_level": "medium",
        "purpose": "dating",
        "need_meal": True,
        "walking_tolerance": "low",
        "weather": "rainy",
        "origin": "??",
    }


def test_react_planner_accepts_valid_action_json(monkeypatch) -> None:
    monkeypatch.setenv("LLM_REACT_PLANNER_ENABLED", "1")
    monkeypatch.setattr(
        planner,
        "_call_llm_provider",
        lambda _prompt: (
            '{"decision":"search_poi","reason":"find anchor first",'
            '"tool":"amap_search","tool_input":{"query":"?? ??","top_k":5},'
            '"constraints":{"low_walk":true,"need_meal":true,"max_hours":4}}'
        ),
    )

    action, debug = planner.build_next_action("???????", _context(), observation={}, react_history=[])

    assert action["decision"] == "search_poi"
    assert action["tool"] == "amap_search"
    assert action["tool_input"]["query"] == "?? ??"
    assert action["constraints"]["need_meal"] is True
    assert debug["llm_search_planner_called"] is True
    assert debug["llm_search_planner_success"] is True


def test_react_planner_invalid_json_triggers_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LLM_REACT_PLANNER_ENABLED", "1")
    monkeypatch.setattr(planner, "_call_llm_provider", lambda _prompt: "not-json")

    action, debug = planner.build_next_action("test", _context(), observation={}, react_history=[])

    assert action["decision"] == "fallback"
    assert action["reason"] == "invalid_json"
    assert debug["llm_search_planner_success"] is False
    assert debug["llm_search_planner_error_type"] == "invalid_json"


def test_react_planner_schema_violation_triggers_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LLM_REACT_PLANNER_ENABLED", "1")
    monkeypatch.setattr(
        planner,
        "_call_llm_provider",
        lambda _prompt: '{"decision":"hack_tool","reason":"x","tool":"none","tool_input":{},"constraints":{}}',
    )

    action, debug = planner.build_next_action("test", _context(), observation={}, react_history=[])

    assert action["decision"] == "fallback"
    assert action["reason"] == "schema_validation_failed"
    assert debug["llm_search_planner_error_type"] == "schema_validation_failed"
