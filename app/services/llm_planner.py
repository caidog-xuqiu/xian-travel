from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from app.models.schemas import PlanRequest, PlanSummary
from app.services.area_registry import map_place_to_area

LLM_ENABLED_ENV = "LLM_PARSER_ENABLED"
LLM_PROVIDER_ENV = "LLM_PROVIDER"
LLM_API_KEY_ENV = "LLM_API_KEY"
LLM_BASE_URL_ENV = "LLM_BASE_URL"
LLM_MODEL_ENV = "LLM_MODEL"

_DOTENV_LOADED = False
_LAST_SELECTOR_DEBUG: Dict[str, Any] = {}
ORIGIN_CLUSTER_BY_ANCHOR = {
    "钟楼": "城墙钟鼓楼簇",
    "小寨": "小寨文博簇",
    "大雁塔": "大雁塔簇",
    "曲江": "曲江夜游簇",
    "回民街": "城墙钟鼓楼簇",
}


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
        "llm_selector_called": False,
        "llm_selector_raw_response_exists": False,
        "llm_selector_json_parse_ok": False,
        "llm_selector_schema_ok": False,
        "llm_selector_retry_count": 0,
        "llm_selected_plan_valid": False,
        "selector_error_type": None,
        "fallback_reason": None,
        # Backward-compatible aliases.
        "llm_called": False,
        "llm_raw_response_exists": False,
        "llm_json_parse_ok": False,
        "llm_schema_ok": False,
    }


def _sync_debug_alias(debug: Dict[str, Any]) -> None:
    debug["llm_called"] = bool(debug.get("llm_selector_called"))
    debug["llm_raw_response_exists"] = bool(debug.get("llm_selector_raw_response_exists"))
    debug["llm_json_parse_ok"] = bool(debug.get("llm_selector_json_parse_ok"))
    debug["llm_schema_ok"] = bool(debug.get("llm_selector_schema_ok"))


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
    candidates = [json_text, re.sub(r",\s*([}\]])", r"\1", json_text)]
    for candidate in candidates:
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
        parts: List[str] = []
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


def _extract_payload_from_raw(raw: str) -> tuple[Dict[str, Any] | None, bool]:
    payload = _load_json_lenient(raw)
    if payload is None:
        return None, False
    if isinstance(payload, dict) and isinstance(payload.get("choices"), list):
        choice = payload.get("choices", [None])[0]
        if isinstance(choice, dict):
            content_text = _content_to_text((choice.get("message") or {}).get("content"))
            if content_text is None:
                return None, False
            inner = _load_json_lenient(content_text)
            if inner is None:
                return None, False
            return inner, True
    return payload, True


def _build_prompt(request: PlanRequest, summaries: List[PlanSummary]) -> str:
    summary_lines = []
    for summary in summaries:
        summary_lines.append(
            f"- plan_id={summary.plan_id} | 类型={summary.variant_label} | 站点数={summary.stop_count} | 簇={summary.clusters} | "
            f"含餐={summary.has_meal} | 距离={summary.total_distance_meters}m | "
            f"交通时间={summary.total_duration_minutes}min | 节奏={summary.rhythm} | "
            f"预算={summary.budget_level} | 步行={summary.walking_tolerance} | 目的={summary.purpose} | "
            f"跨簇={summary.is_cross_cluster} | 跨簇次数={summary.cross_cluster_count} | 簇迁移={summary.cluster_transition_summary} | "
            f"跨区域={summary.is_cross_area} | 跨区域次数={summary.cross_area_count} | 区域迁移={summary.area_transition_summary} | "
            f"区域说明={summary.area_bias_note} | "
            f"差异={summary.diff_points} | 标签={summary.bias_tags} | 知识标签={summary.knowledge_tags} | "
            f"知识说明={summary.place_context_note or summary.knowledge_notes} | 说明={summary.note}"
        )
    summary_text = "\n".join(summary_lines)

    return (
        "你是行程方案选择助手。请从候选方案中选择最符合用户意图的一条。\n"
        "只输出 JSON：{\"selected_plan_id\": string, \"selection_reason\": string, \"reason_tags\": [string]}。\n"
        "不要输出多余解释。\n"
        "硬约束提示：\n"
        "1) need_meal=true 时，无餐方案优先级应显著降低。\n"
        "2) available_hours>=5 时，仅 1 站方案优先级应显著降低。\n"
        "3) 陪父母/轻松优先/时间紧张时，跨簇更多方案应降级。\n"
        "3.1) 陪父母/轻松优先/时间紧张时，跨区域更多方案也应降级。\n"
        "4) 夜游/约会场景时，缺少夜游氛围（如未覆盖曲江夜游簇）的方案应降级。\n"
        "5) 若候选差异明显，优先选择解释力更强、与用户意图更一致的方案。\n"
        "6) 可参考候选中的知识标签/知识说明增强选择理由，但不能覆盖硬约束。\n"
        f"用户需求：companions={_value(request.companion_type)}, purpose={_value(request.purpose)}, "
        f"need_meal={request.need_meal}, walking_tolerance={_value(request.walking_tolerance)}, "
        f"available_hours={request.available_hours}.\n"
        "提示：陪父母/轻松优先/时间紧张时，少跨簇通常更优。\n"
        f"候选方案：\n{summary_text}\n"
    )


def _value(enum_like: Any) -> str:
    value = getattr(enum_like, "value", enum_like)
    return str(value)


def _is_relaxed_intent(request: PlanRequest) -> bool:
    return (
        _value(request.companion_type) == "parents"
        or _value(request.walking_tolerance) == "low"
        or _value(request.purpose) == "relax"
        or request.available_hours <= 4
    )


def _is_night_intent(request: PlanRequest) -> bool:
    return (
        request.preferred_period == "evening"
        or _value(request.purpose) == "dating"
        or _value(request.companion_type) == "partner"
    )


def _has_night_atmosphere(summary: PlanSummary) -> bool:
    if "曲江夜游簇" in summary.clusters:
        return True
    if any(tag in {"night", "prioritize_night_view"} for tag in summary.bias_tags):
        return True
    return "夜游" in summary.note


def _origin_cluster_hint(origin_text: str) -> str | None:
    if not isinstance(origin_text, str):
        return None
    for anchor, cluster in ORIGIN_CLUSTER_BY_ANCHOR.items():
        if anchor in origin_text:
            return cluster
    return None


def _first_cluster(summary: PlanSummary) -> str | None:
    transition = (summary.cluster_transition_summary or "").strip()
    if "->" in transition:
        return transition.split("->", 1)[0].strip()
    if transition:
        return transition
    if summary.clusters:
        return summary.clusters[0]
    return None


def _first_area(summary: PlanSummary) -> str | None:
    transition = (summary.area_transition_summary or "").strip()
    if "->" in transition:
        return transition.split("->", 1)[0].strip()
    if transition:
        return transition
    return None


def _origin_area_hint(origin_text: str) -> str | None:
    temp = {"name": origin_text, "district_cluster": _origin_cluster_hint(origin_text) or ""}
    area = map_place_to_area(temp)
    if isinstance(area, str) and area and area != "unknown":
        return area
    return None


def _selection_violations(request: PlanRequest, summary: PlanSummary) -> List[str]:
    violations: List[str] = []
    if request.need_meal and not summary.has_meal:
        violations.append("missing_meal")
    if request.available_hours >= 5 and summary.stop_count <= 1:
        violations.append("too_few_stops")
    if _is_relaxed_intent(request) and summary.cross_cluster_count >= 2:
        violations.append("too_many_cross_clusters")
    if _is_relaxed_intent(request) and int(getattr(summary, "cross_area_count", 0) or 0) >= 2:
        violations.append("too_many_cross_areas")
    if _is_night_intent(request) and not _has_night_atmosphere(summary):
        violations.append("weak_night_atmosphere")
    return violations


def _constraint_score(request: PlanRequest, summary: PlanSummary) -> int:
    score = 0
    score += max(0, min(summary.stop_count, 5)) * 8
    score += 6 if summary.has_meal else 0
    score -= int(summary.cross_cluster_count) * 4

    if request.need_meal:
        score += 24 if summary.has_meal else -50

    if request.available_hours >= 5 and summary.stop_count <= 1:
        score -= 30

    if _is_relaxed_intent(request):
        score -= int(summary.cross_cluster_count) * 10
        score -= int(getattr(summary, "cross_area_count", 0) or 0) * 12
        if summary.rhythm in {"轻松", "relaxed"}:
            score += 10

    if _is_night_intent(request):
        score += 16 if _has_night_atmosphere(summary) else -24
        if request.need_meal and summary.has_meal:
            score += 8
        if "曲江夜游" in (summary.area_transition_summary or "") or "大雁塔" in (summary.area_transition_summary or ""):
            score += 6

    if summary.purpose == _value(request.purpose):
        score += 6
    if summary.walking_tolerance == _value(request.walking_tolerance):
        score += 4
    if summary.note:
        score += 2

    # Nearby intent: prefer plans that start from the origin cluster.
    if request.origin_preference_mode == "nearby":
        origin_cluster = _origin_cluster_hint(request.origin)
        origin_area = _origin_area_hint(request.origin)
        first_cluster = _first_cluster(summary)
        first_area = _first_area(summary)
        if origin_cluster:
            if first_cluster == origin_cluster:
                score += 12
            elif origin_cluster in summary.clusters:
                score += 6
            else:
                score -= 8
        if origin_area:
            if first_area == origin_area:
                score += 10
            elif origin_area in (summary.area_transition_summary or ""):
                score += 4
            else:
                score -= 6

    score += min(len(summary.diff_points), 4)
    score += min(len(summary.bias_tags), 3)
    return score


def rank_plans_with_constraints(request: PlanRequest, plan_summaries: List[PlanSummary]) -> List[PlanSummary]:
    return sorted(
        plan_summaries,
        key=lambda s: (
            _constraint_score(request, s),
            -len(_selection_violations(request, s)),
            s.stop_count,
            -s.cross_cluster_count,
        ),
        reverse=True,
    )


def infer_reason_tags(request: PlanRequest, summary: PlanSummary) -> List[str]:
    tags: List[str] = []
    if request.need_meal and summary.has_meal:
        tags.append("更顺路含餐")
    if _is_relaxed_intent(request):
        if summary.cross_cluster_count <= 1:
            tags.append("少跨簇")
        if summary.rhythm in {"轻松", "relaxed"}:
            tags.append("更适合轻松出行")
    if _is_night_intent(request) and _has_night_atmosphere(summary):
        tags.append("更符合夜游偏好")
    if summary.purpose == "tourism" and "classic" in summary.bias_tags:
        tags.append("更符合经典游览")
    if _value(request.weather) in {"hot", "rainy"} and "prioritize_indoor" in summary.bias_tags:
        tags.append("更适合当前天气")
    if int(getattr(summary, "cross_area_count", 0) or 0) == 0:
        tags.append("区域更集中")
    elif int(getattr(summary, "cross_area_count", 0) or 0) >= 2 and _value(request.purpose) == "tourism":
        tags.append("区域覆盖更完整")
    if getattr(summary, "area_bias_note", None):
        tags.append("区域策略更匹配")

    knowledge_tags = list(summary.knowledge_tags or [])
    if "文博密度高" in knowledge_tags:
        tags.append("文博集中")
    if "晚间氛围更强" in knowledge_tags:
        tags.append("夜游氛围更强")
    if "适合拍照打卡" in knowledge_tags:
        tags.append("更适合拍照")
    if "雨天室内友好" in knowledge_tags:
        tags.append("雨天体验更稳")
    if "动线更顺" in knowledge_tags:
        tags.append("动线更顺")
    if "餐饮选择更丰富" in knowledge_tags:
        tags.append("餐饮选择更丰富")
    if "区域风格鲜明" in knowledge_tags:
        tags.append("区域风格更匹配")

    if not tags:
        tags.append("综合约束更稳")
    return list(dict.fromkeys(tags))


def enrich_selection_reason_with_knowledge(selection_reason: str, summary: PlanSummary | None) -> str:
    """Append one short knowledge-backed phrase when available."""
    if summary is None:
        return selection_reason

    tags = list(summary.knowledge_tags or [])
    if not tags and summary.place_context_note:
        tags = [summary.place_context_note]
    if not tags:
        return selection_reason

    knowledge_phrase = tags[0]
    if knowledge_phrase and knowledge_phrase not in selection_reason:
        return f"{selection_reason}；{knowledge_phrase}。"
    return selection_reason


def post_check_selected_plan(
    request: PlanRequest,
    plan_summaries: List[PlanSummary],
    proposed_plan_id: str | None,
) -> Dict[str, Any]:
    if not plan_summaries:
        return {
            "final_plan_id": proposed_plan_id,
            "switched": False,
            "violations": [],
            "ranked_plan_ids": [],
            "note": "no_candidates",
        }

    by_id = {summary.plan_id: summary for summary in plan_summaries}
    ranked = rank_plans_with_constraints(request, plan_summaries)
    ranked_ids = [summary.plan_id for summary in ranked]

    proposed = by_id.get(proposed_plan_id or "")
    if proposed is None:
        return {
            "final_plan_id": ranked[0].plan_id,
            "switched": True,
            "violations": ["invalid_or_missing_selection"],
            "ranked_plan_ids": ranked_ids,
            "note": "fallback_best_ranked",
        }

    proposed_violations = _selection_violations(request, proposed)
    proposed_score = _constraint_score(request, proposed)

    fallback = ranked[0]
    fallback_violations = _selection_violations(request, fallback)
    fallback_score = _constraint_score(request, fallback)

    switch_required = False
    switch_note = "keep_proposed"
    if proposed_violations and fallback.plan_id != proposed.plan_id:
        if len(fallback_violations) < len(proposed_violations):
            switch_required = True
            switch_note = "fix_hard_violations"
    elif fallback.plan_id != proposed.plan_id and fallback_score - proposed_score >= 18:
        switch_required = True
        switch_note = "stronger_constraint_match"

    if switch_required:
        return {
            "final_plan_id": fallback.plan_id,
            "switched": True,
            "violations": proposed_violations,
            "ranked_plan_ids": ranked_ids,
            "note": switch_note,
        }

    return {
        "final_plan_id": proposed.plan_id,
        "switched": False,
        "violations": proposed_violations,
        "ranked_plan_ids": ranked_ids,
        "note": "keep_proposed",
    }


def _call_llm(prompt: str) -> str | None:
    _load_dotenv_once()
    provider = str(os.getenv(LLM_PROVIDER_ENV, "")).strip().lower()
    api_key = str(os.getenv(LLM_API_KEY_ENV, "")).strip()
    base_url = str(os.getenv(LLM_BASE_URL_ENV, "")).strip()
    model = str(os.getenv(LLM_MODEL_ENV, "")).strip() or "default"

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
        with urlopen(req, timeout=6) as resp:
            return resp.read().decode("utf-8")

    return None


def _norm_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace(" ", "").replace("-", "_")


def _resolve_selected_plan_id(
    selected_plan_id: Any,
    plan_summaries: List[PlanSummary],
) -> tuple[str | None, bool]:
    if not plan_summaries:
        return None, False

    ids = [summary.plan_id for summary in plan_summaries]
    by_norm_id = {_norm_id(summary.plan_id): summary.plan_id for summary in plan_summaries}
    by_norm_variant = {_norm_id(summary.variant_label): summary.plan_id for summary in plan_summaries}

    if isinstance(selected_plan_id, int):
        # Accept 1-based index first, then 0-based fallback.
        if 1 <= selected_plan_id <= len(ids):
            return ids[selected_plan_id - 1], True
        if 0 <= selected_plan_id < len(ids):
            return ids[selected_plan_id], True
        return None, False

    if not isinstance(selected_plan_id, str):
        return None, False

    text = selected_plan_id.strip()
    if not text:
        return None, False

    if text in ids:
        return text, True

    # Numeric-like strings.
    if text.isdigit():
        return _resolve_selected_plan_id(int(text), plan_summaries)

    normalized = _norm_id(text)
    if normalized in by_norm_id:
        return by_norm_id[normalized], True
    if normalized in by_norm_variant:
        return by_norm_variant[normalized], True

    # Fuzzy contains match.
    for summary in plan_summaries:
        if _norm_id(summary.plan_id) in normalized or normalized in _norm_id(summary.plan_id):
            return summary.plan_id, True
        if _norm_id(summary.variant_label) in normalized or normalized in _norm_id(summary.variant_label):
            return summary.plan_id, True

    return None, False


def select_plan_with_llm_debug(
    request: PlanRequest,
    plan_summaries: List[PlanSummary],
) -> tuple[Dict[str, Any] | None, Dict[str, Any]]:
    debug = _new_debug_state()

    if not _enabled():
        debug["fallback_reason"] = "selector_disabled"
        _sync_debug_alias(debug)
        return None, debug

    debug["llm_selector_called"] = True
    prompt = _build_prompt(request, plan_summaries)
    raw = None
    for attempt in range(2):
        try:
            raw = _call_llm(prompt)
            break
        except Exception:
            # Retry exactly once for network-like selector exceptions.
            if attempt == 0:
                debug["llm_selector_retry_count"] = 1
                continue
            debug["fallback_reason"] = "llm_selector_call_exception"
            debug["selector_error_type"] = "network_exception"
            _sync_debug_alias(debug)
            return None, debug

    debug["llm_selector_raw_response_exists"] = bool(raw)
    if not raw:
        debug["fallback_reason"] = "empty_response"
        _sync_debug_alias(debug)
        return None, debug

    payload, json_ok = _extract_payload_from_raw(raw)
    debug["llm_selector_json_parse_ok"] = json_ok
    if payload is None:
        debug["fallback_reason"] = "invalid_json"
        _sync_debug_alias(debug)
        return None, debug

    selected_plan_id_raw = payload.get("selected_plan_id")
    selection_reason = payload.get("selection_reason")
    reason_tags = payload.get("reason_tags")
    if not isinstance(selection_reason, str) or not selection_reason.strip():
        selection_reason = "LLM 未给出理由。"
    if not isinstance(reason_tags, list) or not all(isinstance(tag, str) for tag in reason_tags):
        reason_tags = []

    resolved_plan_id, valid = _resolve_selected_plan_id(selected_plan_id_raw, plan_summaries)
    debug["llm_selected_plan_valid"] = valid
    if not valid or not resolved_plan_id:
        debug["fallback_reason"] = "invalid_plan_id"
        _sync_debug_alias(debug)
        return None, debug

    debug["llm_selector_schema_ok"] = True
    debug["fallback_reason"] = None

    post_check = post_check_selected_plan(request, plan_summaries, resolved_plan_id)
    final_plan_id = post_check.get("final_plan_id") or resolved_plan_id
    switched = bool(post_check.get("switched"))

    selected_summary = next((s for s in plan_summaries if s.plan_id == final_plan_id), None)
    if not reason_tags and selected_summary is not None:
        reason_tags = infer_reason_tags(request, selected_summary)

    if switched:
        note = str(post_check.get("note") or "post_check_adjusted")
        if note == "fix_hard_violations":
            reason_tags = ["后置复核改选", "避免核心约束违背"] + reason_tags
        else:
            reason_tags = ["后置复核改选"] + reason_tags
        selection_reason = f"{selection_reason.strip()}；经约束复核后改选 {final_plan_id}，以更贴合核心意图。"

    result = {
        "selected_plan_id": final_plan_id,
        "selection_reason": selection_reason.strip(),
        "reason_tags": list(dict.fromkeys(reason_tags)),
        "post_check_switched": switched,
        "post_check_note": post_check.get("note"),
        "post_check_violations": post_check.get("violations", []),
    }
    _sync_debug_alias(debug)
    return result, debug


def select_plan_with_llm(request: PlanRequest, plan_summaries: List[PlanSummary]) -> Dict[str, Any] | None:
    global _LAST_SELECTOR_DEBUG
    result, debug = select_plan_with_llm_debug(request, plan_summaries)
    _LAST_SELECTOR_DEBUG = debug
    return result


def get_last_selector_debug() -> Dict[str, Any]:
    return dict(_LAST_SELECTOR_DEBUG)
