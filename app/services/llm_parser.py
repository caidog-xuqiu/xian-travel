from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Tuple

from dotenv import load_dotenv
from urllib.request import Request, ProxyHandler, build_opener, urlopen

LLM_ENABLED_ENV = "LLM_PARSER_ENABLED"
LLM_PROVIDER_ENV = "LLM_PROVIDER"
LLM_API_KEY_ENV = "LLM_API_KEY"
LLM_BASE_URL_ENV = "LLM_BASE_URL"
LLM_MODEL_ENV = "LLM_MODEL"
LLM_HTTP_PROXY_ENV = "LLM_HTTP_PROXY"
LLM_HTTPS_PROXY_ENV = "LLM_HTTPS_PROXY"

SUPPORTED_FIELDS = {
    "companion_type",
    "available_hours",
    "budget_level",
    "purpose",
    "need_meal",
    "walking_tolerance",
    "weather",
    "origin",
    "preferred_period",
    "origin_preference_mode",
}

ALLOWED_ENUMS = {
    "companion_type": {"solo", "parents", "friends", "partner"},
    "budget_level": {"low", "medium", "high"},
    "purpose": {"tourism", "relax", "food", "dating"},
    "walking_tolerance": {"low", "medium", "high"},
    "weather": {"sunny", "rainy", "hot", "cold"},
    "preferred_period": {"morning", "midday", "afternoon", "evening"},
    "origin_preference_mode": {"nearby"},
}

_DOTENV_LOADED = False
_LAST_PARSE_DEBUG: Dict[str, Any] = {}


def _load_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=False)
    _DOTENV_LOADED = True


def _enabled() -> bool:
    _load_dotenv_once()
    return str(os.getenv(LLM_ENABLED_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def _new_debug_state() -> Dict[str, Any]:
    return {
        "llm_called": False,
        "llm_raw_response_exists": False,
        "llm_json_parse_ok": False,
        "llm_schema_ok": False,
        "fallback_reason": None,
    }


def _build_prompt(text: str) -> str:
    return (
        "你是一个旅行需求解析器。请将用户输入解析为严格 JSON。\n"
        "只允许以下字段：companion_type, available_hours, budget_level, purpose, "
        "need_meal, walking_tolerance, weather, origin, preferred_period, origin_preference_mode。\n"
        "枚举范围："
        "companion_type=[solo, parents, friends, partner]; "
        "budget_level=[low, medium, high]; "
        "purpose=[tourism, relax, food, dating]; "
        "walking_tolerance=[low, medium, high]; "
        "weather=[sunny, rainy, hot, cold]; "
        "preferred_period=[morning, midday, afternoon, evening 或 null]; "
        "origin_preference_mode=[nearby 或 null]。\n"
        "目的判定规则："
        "若有“约会/对象/夜景/拍照”，优先输出 purpose=dating。"
        "“想吃饭/顺便吃饭/中午吃饭/晚上吃饭”通常只表示 need_meal=true；"
        "仅在强美食意图（如“美食路线/专门吃饭/吃吃喝喝/想去吃小吃”）时输出 purpose=food。"
        "当 dating 与 meal 同时出现时，保持 purpose=dating 且 need_meal=true。"
        "若未明确说不吃饭且语义包含用餐需求，need_meal 应为 true。"
        "若出现多个时段信号，优先选择更晚的时段。"
        "不确定的字段给 null，不要乱编。仅输出 JSON，不要解释。\n"
        f"用户输入：{text}"
    )


def _extract_json_text(raw_text: str | None) -> str | None:
    if not isinstance(raw_text, str):
        return None
    text = raw_text.strip()
    if not text:
        return None

    if "```" in text:
        text = text.replace("```json", "```").replace("```JSON", "```")
        for chunk in [part.strip() for part in text.split("```") if part.strip()]:
            if chunk.startswith("{") and chunk.endswith("}"):
                return chunk

    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return None


def _load_json_lenient(raw_text: str | None) -> Dict[str, Any] | None:
    json_text = _extract_json_text(raw_text)
    if not json_text:
        return None

    candidates = [json_text]
    # Minimal cleanup for common model formatting noise.
    candidates.append(re.sub(r",\s*([}\]])", r"\1", json_text))
    candidates.append(json_text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'"))

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _content_to_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)
    return None


def _call_llm_provider(prompt: str) -> str | None:
    """Provider-agnostic call wrapper.

    NOTE: This is a lightweight placeholder to keep providers swappable.
    """
    _load_dotenv_once()
    provider = str(os.getenv(LLM_PROVIDER_ENV, "")).strip().lower()
    api_key = str(os.getenv(LLM_API_KEY_ENV, "")).strip()
    base_url = str(os.getenv(LLM_BASE_URL_ENV, "")).strip()
    model = str(os.getenv(LLM_MODEL_ENV, "")).strip() or "default"
    http_proxy = str(os.getenv(LLM_HTTP_PROXY_ENV, "") or os.getenv("HTTP_PROXY", "")).strip()
    https_proxy = str(os.getenv(LLM_HTTPS_PROXY_ENV, "") or os.getenv("HTTPS_PROXY", "")).strip()

    if not provider or not api_key:
        return None

    if provider == "custom" and base_url:
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
        ).encode("utf-8")
        req = Request(
            base_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        if http_proxy or https_proxy:
            proxy_map: Dict[str, str] = {}
            if http_proxy:
                proxy_map["http"] = http_proxy
            if https_proxy:
                proxy_map["https"] = https_proxy
            opener = build_opener(ProxyHandler(proxy_map))
            with opener.open(req, timeout=6) as resp:
                return resp.read().decode("utf-8")
        with urlopen(req, timeout=6) as resp:
            return resp.read().decode("utf-8")

    # Unknown provider: return None to trigger fallback.
    return None


def _validate_payload(payload: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    cleaned: Dict[str, Any] = {}
    for key, value in payload.items():
        if key not in SUPPORTED_FIELDS:
            continue
        if value is None:
            cleaned[key] = None
            continue

        if key in {"companion_type", "budget_level", "purpose", "walking_tolerance", "weather"}:
            if isinstance(value, str) and value in ALLOWED_ENUMS[key]:
                cleaned[key] = value
            continue
        elif key in {"preferred_period", "origin_preference_mode"}:
            if value is None:
                cleaned[key] = None
            elif isinstance(value, str) and value in ALLOWED_ENUMS[key]:
                cleaned[key] = value
            continue
        elif key == "available_hours":
            try:
                cleaned[key] = float(value)
            except (TypeError, ValueError):
                continue
        elif key == "need_meal":
            if isinstance(value, bool):
                cleaned[key] = value
            elif isinstance(value, str) and value.lower() in {"true", "false"}:
                cleaned[key] = value.lower() == "true"
            continue
        elif key == "origin":
            if isinstance(value, str) and value.strip():
                cleaned[key] = value.strip()
            continue
        else:
            cleaned[key] = value

    # Require at least one non-null field to treat this as usable LLM payload.
    has_usable_value = any(value is not None for value in cleaned.values())
    return cleaned if has_usable_value else None


def _parse_payload_from_raw(raw: str) -> Tuple[Dict[str, Any] | None, bool]:
    payload = _load_json_lenient(raw)
    if payload is None:
        return None, False

    if isinstance(payload, dict) and isinstance(payload.get("choices"), list):
        choice = payload.get("choices", [None])[0]
        if isinstance(choice, dict):
            message = choice.get("message") or {}
            content_text = _content_to_text(message.get("content"))
            if content_text is None:
                return None, False
            extracted = _load_json_lenient(content_text)
            if extracted is None:
                return None, False
            return extracted, True
    return payload, True


def parse_free_text_with_llm_debug(text: str) -> tuple[Dict[str, Any] | None, Dict[str, Any]]:
    """Try parsing with LLM and return diagnostics for fallback reasoning."""
    debug = _new_debug_state()

    if not _enabled():
        debug["fallback_reason"] = "llm_parser_disabled"
        return None, debug

    debug["llm_called"] = True
    prompt = _build_prompt(text)
    try:
        raw = _call_llm_provider(prompt)
    except Exception:
        debug["fallback_reason"] = "llm_call_exception"
        return None, debug

    debug["llm_raw_response_exists"] = bool(raw)
    if not raw:
        debug["fallback_reason"] = "llm_empty_response"
        return None, debug

    payload, json_ok = _parse_payload_from_raw(raw)
    debug["llm_json_parse_ok"] = json_ok
    if payload is None:
        debug["fallback_reason"] = "llm_json_parse_failed"
        return None, debug

    validated = _validate_payload(payload)
    if validated is None:
        debug["fallback_reason"] = "llm_schema_validation_failed"
        return None, debug

    debug["llm_schema_ok"] = True
    debug["fallback_reason"] = None
    return validated, debug


def parse_free_text_with_llm(text: str) -> Dict[str, Any] | None:
    """Try to parse text into PlanRequest fields using LLM.

    Returns dict on success, None on failure (caller should fallback).
    """
    global _LAST_PARSE_DEBUG
    parsed, debug = parse_free_text_with_llm_debug(text)
    _LAST_PARSE_DEBUG = debug
    return parsed


def get_last_llm_parse_debug() -> Dict[str, Any]:
    return dict(_LAST_PARSE_DEBUG)
