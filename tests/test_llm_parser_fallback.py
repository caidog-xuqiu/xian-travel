from __future__ import annotations

import json

import app.services.llm_parser as llm_parser
from app.models.schemas import TextPlanRequest
from app.routes.plan import plan_trip_from_text_readable
from app.services.request_parser import parse_free_text_to_plan_request


def test_no_llm_enabled_uses_rule_parser(monkeypatch) -> None:
    monkeypatch.delenv(llm_parser.LLM_ENABLED_ENV, raising=False)
    req = parse_free_text_to_plan_request("\u966a\u7236\u6bcd\u534a\u5929\uff0c\u522b\u592a\u7d2f")
    assert req.parsed_by == "rule"


def test_llm_invalid_json_falls_back_to_rule(monkeypatch) -> None:
    monkeypatch.setenv(llm_parser.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_parser.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_parser.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_parser.LLM_BASE_URL_ENV, "http://example.invalid")

    monkeypatch.setattr(llm_parser, "_call_llm_provider", lambda prompt: "not-json")

    req = parse_free_text_to_plan_request("\u548c\u670b\u53cb\u4e00\u5929\uff0c\u60f3\u901b\u666f\u70b9")
    assert req.parsed_by == "rule"


def test_llm_valid_payload_overrides_rule(monkeypatch) -> None:
    monkeypatch.setenv(llm_parser.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_parser.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_parser.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_parser.LLM_BASE_URL_ENV, "http://example.invalid")

    payload = {
        "companion_type": "friends",
        "available_hours": 6,
        "budget_level": "low",
        "purpose": "tourism",
        "need_meal": True,
        "walking_tolerance": "high",
        "weather": "sunny",
        "origin": "\u949f\u697c",
        "preferred_period": None,
        "origin_preference_mode": "nearby",
    }
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(payload)
                }
            }
        ]
    }
    monkeypatch.setattr(llm_parser, "_call_llm_provider", lambda prompt: json.dumps(response))

    req = parse_free_text_to_plan_request("\u968f\u4fbf\u8bf4\u4e00\u53e5")
    assert req.parsed_by == "llm"
    assert req.budget_level.value == "low"
    assert req.origin_preference_mode == "nearby"


def test_plan_from_text_readable_works_without_llm(monkeypatch) -> None:
    monkeypatch.delenv(llm_parser.LLM_ENABLED_ENV, raising=False)

    response = plan_trip_from_text_readable(TextPlanRequest(text="\u966a\u7236\u6bcd\u534a\u5929"))
    assert response.parsed_request.parsed_by == "rule"


def test_custom_provider_uses_messages_format(monkeypatch) -> None:
    monkeypatch.setenv(llm_parser.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_parser.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_parser.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_parser.LLM_BASE_URL_ENV, "http://example.invalid")
    monkeypatch.setenv(llm_parser.LLM_MODEL_ENV, "dummy-model")

    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def _fake_urlopen(req, timeout=6):
        captured["body"] = req.data.decode("utf-8")
        return _FakeResponse()

    monkeypatch.setattr(llm_parser, "urlopen", _fake_urlopen)

    _ = llm_parser._call_llm_provider("test prompt")
    assert "\"messages\"" in captured["body"]


def test_llm_dating_overrides_food_when_rule_detects_dating(monkeypatch) -> None:
    monkeypatch.setenv(llm_parser.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_parser.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_parser.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_parser.LLM_BASE_URL_ENV, "http://example.invalid")

    payload = {
        "companion_type": "partner",
        "available_hours": 4,
        "budget_level": "medium",
        "purpose": "food",
        "need_meal": True,
        "walking_tolerance": "medium",
        "weather": "sunny",
        "origin": "钟楼",
        "preferred_period": "evening",
        "origin_preference_mode": None,
    }
    response = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    monkeypatch.setattr(llm_parser, "_call_llm_provider", lambda prompt: json.dumps(response))

    req = parse_free_text_to_plan_request("和对象晚上出去，想吃饭、拍照、逛夜景")
    assert req.parsed_by == "llm"
    assert req.purpose.value == "dating"
    assert req.need_meal is True
    assert req.preferred_period == "evening"


def test_llm_origin_nearby_is_normalized(monkeypatch) -> None:
    monkeypatch.setenv(llm_parser.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_parser.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_parser.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_parser.LLM_BASE_URL_ENV, "http://example.invalid")

    payload = {
        "companion_type": "solo",
        "available_hours": 3,
        "budget_level": "medium",
        "purpose": "relax",
        "need_meal": True,
        "walking_tolerance": "low",
        "weather": "sunny",
        "origin": "钟楼附近",
        "preferred_period": None,
        "origin_preference_mode": None,
    }
    response = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    monkeypatch.setattr(llm_parser, "_call_llm_provider", lambda prompt: json.dumps(response))

    req = parse_free_text_to_plan_request("我在钟楼附近，只有3小时，想轻松一点")
    assert req.parsed_by == "llm"
    assert req.origin == "钟楼"
    assert req.origin_preference_mode == "nearby"


def test_llm_need_meal_false_is_corrected_by_rule(monkeypatch) -> None:
    monkeypatch.setenv(llm_parser.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_parser.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_parser.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_parser.LLM_BASE_URL_ENV, "http://example.invalid")

    payload = {
        "companion_type": "solo",
        "available_hours": 3,
        "budget_level": "medium",
        "purpose": "relax",
        "need_meal": False,
        "walking_tolerance": "low",
        "weather": "sunny",
        "origin": "钟楼附近",
        "preferred_period": None,
        "origin_preference_mode": "nearby",
    }
    response = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    monkeypatch.setattr(llm_parser, "_call_llm_provider", lambda prompt: json.dumps(response))

    req = parse_free_text_to_plan_request("我在钟楼附近，只有3小时，想轻松一点")
    assert req.parsed_by == "llm"
    assert req.need_meal is True
