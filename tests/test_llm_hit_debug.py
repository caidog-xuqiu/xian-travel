from __future__ import annotations

import json

import app.services.llm_parser as llm_parser
from app.services.request_parser import parse_free_text_to_plan_request_with_debug


def _enable_custom_llm(monkeypatch) -> None:
    monkeypatch.setenv(llm_parser.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_parser.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_parser.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_parser.LLM_BASE_URL_ENV, "http://example.invalid")


def test_llm_parser_debug_success(monkeypatch) -> None:
    _enable_custom_llm(monkeypatch)
    payload = {
        "companion_type": "parents",
        "available_hours": 4,
        "budget_level": "medium",
        "purpose": "tourism",
        "need_meal": True,
        "walking_tolerance": "low",
        "weather": "rainy",
        "origin": "钟楼",
        "preferred_period": "midday",
        "origin_preference_mode": None,
    }
    raw = {
        "choices": [
            {
                "message": {
                    "content": f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```",
                }
            }
        ]
    }
    monkeypatch.setattr(llm_parser, "_call_llm_provider", lambda prompt: json.dumps(raw, ensure_ascii=False))

    request, debug = parse_free_text_to_plan_request_with_debug("陪父母半天，不想太累，中午想吃饭")
    assert request.parsed_by == "llm"
    assert debug["llm_called"] is True
    assert debug["llm_json_parse_ok"] is True
    assert debug["llm_schema_ok"] is True
    assert debug["fallback_reason"] is None


def test_llm_parser_debug_fallback_reason(monkeypatch) -> None:
    _enable_custom_llm(monkeypatch)
    monkeypatch.setattr(llm_parser, "_call_llm_provider", lambda prompt: "not-a-json-response")

    request, debug = parse_free_text_to_plan_request_with_debug("陪父母半天，不想太累，中午想吃饭")
    assert request.parsed_by == "rule"
    assert debug["llm_called"] is True
    assert debug["llm_raw_response_exists"] is True
    assert debug["llm_json_parse_ok"] is False
    assert debug["fallback_reason"] == "llm_json_parse_failed"
