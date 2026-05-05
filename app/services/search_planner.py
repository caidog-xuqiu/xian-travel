from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List

from app.services.llm_parser import _call_llm_provider, _load_json_lenient

LLM_SEARCH_PLANNER_ENABLED_ENV = "LLM_SEARCH_PLANNER_ENABLED"

ALLOWED_TOOLS = {"geocode", "keyword_search", "nearby_search", "weather", "route"}

INTENT_KEYWORDS: Dict[str, List[str]] = {
    "park": ["公园", "绿地", "散步", "湿地", "植物园", "草坪", "曲江池", "大唐芙蓉园"],
    "bbq": ["烧烤", "烤肉", "烤串", "夜宵烧烤"],
    "food": ["餐饮", "餐厅", "吃饭", "美食", "小吃", "夜宵"],
    "night_view": ["夜游", "夜景", "晚上", "晚间", "灯光"],
    "photo": ["拍照", "打卡", "出片"],
    "museum": ["博物馆", "文博", "展馆", "美术馆"],
    "parents": ["父母", "老人", "爸妈", "长辈"],
    "family": ["亲子", "带娃", "小孩", "孩子", "家庭"],
    "couple": ["情侣", "约会", "对象", "二人世界"],
    "low_walk": ["少步行", "少走路", "不要太累", "轻松", "低强度", "不想走太多"],
    "rain": ["下雨", "雨天", "阴雨", "室内一点"],
    "budget": ["预算", "性价比", "便宜", "省钱", "花费低"],
}

INTENT_ALIASES: Dict[str, List[str]] = {
    "park": ["公园", "城市公园", "绿地", "散步绿地"],
    "bbq": ["烧烤", "烤肉", "烤串"],
    "food": ["餐厅", "美食", "小吃"],
    "night_view": ["夜游", "夜景", "夜景打卡"],
    "photo": ["拍照", "打卡", "出片"],
    "museum": ["博物馆", "展馆", "文博"],
    "parents": ["低强度", "少折返", "休闲"],
    "family": ["亲子友好", "休闲活动", "互动体验"],
    "couple": ["约会", "氛围感", "拍照"],
    "low_walk": ["少步行", "轻松", "低强度"],
    "rain": ["室内", "雨天可去", "短步行"],
    "budget": ["性价比", "预算友好", "平价"],
}

ORIGIN_HINT_TOKENS = ["我在", "人在", "从", "起点", "附近", "周边", "这边", "出发"]
CITY_CENTER_TOKENS = ["市中心", "城区", "中心"]



def _value(value: Any) -> Any:
    return getattr(value, "value", value)



def _request_context_dict(request_context: Any) -> Dict[str, Any]:
    if request_context is None:
        return {}
    if isinstance(request_context, dict):
        return dict(request_context)
    if hasattr(request_context, "model_dump"):
        return request_context.model_dump()
    return {
        key: _value(getattr(request_context, key))
        for key in dir(request_context)
        if not key.startswith("_") and not callable(getattr(request_context, key, None))
    }



def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)



def _unique(items: Iterable[str]) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered



def _origin_is_explicit(user_query: str, context: Dict[str, Any]) -> bool:
    origin = str(context.get("origin") or "").strip()
    if not origin:
        return False
    if origin and origin != "钟楼" and origin in user_query:
        return True
    if any(token in user_query for token in ORIGIN_HINT_TOKENS):
        return True
    return bool(context.get("origin_latitude") is not None and context.get("origin_longitude") is not None)



def _infer_primary_intents(user_query: str, context: Dict[str, Any]) -> List[str]:
    intents: List[str] = []
    query = str(user_query or "")

    for intent, keywords in INTENT_KEYWORDS.items():
        if _contains_any(query, keywords):
            intents.append(intent)

    purpose = str(_value(context.get("purpose")) or "")
    companion = str(_value(context.get("companion_type")) or "")
    weather = str(_value(context.get("weather")) or "")

    if purpose == "dating":
        intents.extend(["couple", "photo", "night_view", "food"])
    if purpose == "food" or bool(context.get("need_meal")):
        intents.append("food")
    if companion == "partner":
        intents.append("couple")
    if companion == "parents":
        intents.extend(["parents", "family", "low_walk"])
    if str(_value(context.get("walking_tolerance")) or "") == "low":
        intents.append("low_walk")
    if str(_value(context.get("budget_level")) or "") == "low":
        intents.append("budget")
    if weather == "rainy":
        intents.append("rain")

    return _unique(intents or ["classic"])



def _infer_secondary_preferences(user_query: str, context: Dict[str, Any], primary_intents: List[str]) -> List[str]:
    prefs: List[str] = []
    if _origin_is_explicit(user_query, context):
        prefs.append("near_origin")
    if "low_walk" in primary_intents or str(_value(context.get("walking_tolerance")) or "") == "low":
        prefs.append("low_walk")
    if "park" in primary_intents:
        prefs.append("park_first")
    if bool(context.get("need_meal")) or "bbq" in primary_intents or "food" in primary_intents:
        prefs.append("meal_anchor")
    if any(token in user_query for token in CITY_CENTER_TOKENS):
        prefs.append("city_center")
    if "rain" in primary_intents or str(_value(context.get("weather")) or "") == "rainy":
        prefs.append("indoor_first")
    return _unique(prefs)



def _aliases_for(intents: Iterable[str]) -> Dict[str, List[str]]:
    return {intent: list(INTENT_ALIASES.get(intent, [intent])) for intent in intents if intent in INTENT_ALIASES}



def _queries_for_intent(intent: str, aliases: Dict[str, List[str]]) -> List[str]:
    values = aliases.get(intent) or [intent]
    if intent == "bbq":
        return ["烧烤", "烤肉", "烤串"]
    if intent == "park":
        return ["公园", "城市公园", "适合散步的绿地"]
    if intent in {"night_view", "night"}:
        return ["夜游", "夜景", "夜景打卡"]
    if intent == "rain":
        return ["室内景点", "商场", "博物馆"]
    if intent == "couple":
        return ["约会", "夜景", "拍照打卡"]
    return values[:3]



def _build_rounds(primary_intents: List[str], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    rounds: List[Dict[str, Any]] = []
    aliases = _aliases_for(primary_intents)

    sight_blacklist = {"food", "bbq", "budget", "low_walk", "parents", "family"}
    sight_intents = [intent for intent in primary_intents if intent not in sight_blacklist]
    if "park" in primary_intents:
        sight_intents = ["park"] + [intent for intent in sight_intents if intent != "park"]
    elif "night_view" in primary_intents and "night_view" not in sight_intents:
        sight_intents = ["night_view"] + sight_intents

    if not sight_intents:
        sight_intents = ["classic"]

    first_intent = sight_intents[0]
    rounds.append(
        {
            "goal": f"find {first_intent} places matching user intent",
            "tool": "keyword_search",
            "queries": _queries_for_intent(first_intent, aliases),
            "area_bias": "origin_or_user_area" if context.get("origin") else "citywide",
            "max_results": 5,
        }
    )

    if "bbq" in primary_intents:
        rounds.append(
            {
                "goal": "find bbq around selected place candidates",
                "tool": "nearby_search",
                "queries": _queries_for_intent("bbq", aliases),
                "area_bias": "around_first_round_candidates",
                "anchor_strategy": "top_ranked_anchor",
                "anchor_from_round": 1,
                "anchor_top_k": 2,
                "radius_meters": 1800,
                "max_results": 5,
            }
        )
    elif bool(context.get("need_meal")) or "food" in primary_intents:
        rounds.append(
            {
                "goal": "find meal options around selected place candidates",
                "tool": "nearby_search",
                "queries": ["餐厅", "美食", "小吃"],
                "area_bias": "around_first_round_candidates",
                "anchor_strategy": "top_ranked_anchor",
                "anchor_from_round": 1,
                "anchor_top_k": 2,
                "radius_meters": 1600,
                "max_results": 5,
            }
        )

    return rounds[:3]



def _summarize_plan(plan: Dict[str, Any]) -> str:
    intents = "、".join(plan.get("primary_intents") or [])
    rounds = plan.get("search_rounds") or []
    if not rounds:
        return f"围绕{intents or '用户诉求'}进行基础搜索。"
    first = rounds[0].get("queries") or []
    second = rounds[1].get("queries") if len(rounds) > 1 else []
    if second:
        return f"先找{'、'.join(first[:2])}，再围绕候选找{'、'.join(second[:2])}；偏好少步行和顺路。"
    return f"围绕{'、'.join(first[:3])}搜索；再由规则控制时间、步行和跨区域。"



def _default_search_plan(user_query: str, request_context: Dict[str, Any], *, mode: str) -> Dict[str, Any]:
    primary = _infer_primary_intents(user_query, request_context)
    secondary = _infer_secondary_preferences(user_query, request_context, primary)
    aliases = _aliases_for(primary + secondary)
    origin_explicit = _origin_is_explicit(user_query, request_context)
    needs_location_choice = (
        not origin_explicit
        and any(intent in primary for intent in ["park", "bbq", "photo", "night_view", "couple"])
        and "city_center" not in secondary
    )
    plan = {
        "planner_mode": mode,
        "primary_intents": primary,
        "secondary_preferences": secondary,
        "clarification_needed": bool(needs_location_choice),
        "clarification_question": None,
        "clarification_options": [],
        "target_area_hints": ["市中心", "起点附近"] if not origin_explicit else [str(request_context.get("origin") or "")],
        "prefer_origin_nearby": origin_explicit or "near_origin" in secondary,
        "search_aliases": aliases,
        "search_rounds": _build_rounds(primary, request_context),
    }
    if needs_location_choice:
        plan["clarification_question"] = "你更想离起点近一些，还是更想去市中心逛？"
        plan["clarification_options"] = ["离起点近一点", "去市中心", "公园优先", "吃饭更重要"]
    plan["search_plan_summary"] = _summarize_plan(plan)
    return plan



def _build_prompt(user_query: str, request_context: Dict[str, Any], fast_mode: bool) -> str:
    return (
        "你是旅行 Agent 的搜索规划器。你不直接规划最终路线，只决定先搜什么、怎么搜、是否需要追问。\n"
        "只能输出 JSON，不要解释。工具只能从 geocode, keyword_search, nearby_search, weather, route 中选择。\n"
        "search_rounds 最多 3 轮。\n"
        "如果用户信息不足以决定搜索方向，clarification_needed=true，并给 2-4 个 clarification_options。\n"
        "输出字段：planner_mode, primary_intents, secondary_preferences, search_rounds, "
        "clarification_needed, clarification_question, clarification_options, search_aliases, "
        "target_area_hints, prefer_origin_nearby。\n"
        f"fast_mode={fast_mode}\n"
        f"user_query={user_query}\n"
        f"request_context={json.dumps(request_context, ensure_ascii=False)}\n"
    )



def _validate_plan(payload: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    rounds = payload.get("search_rounds")
    if not isinstance(rounds, list):
        return None
    cleaned_rounds: List[Dict[str, Any]] = []
    for item in rounds[:3]:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if tool not in ALLOWED_TOOLS:
            continue
        queries = [str(q).strip() for q in (item.get("queries") or []) if str(q).strip()]
        if not queries and tool in {"keyword_search", "nearby_search"}:
            continue
        cleaned_rounds.append(
            {
                "goal": str(item.get("goal") or tool),
                "tool": tool,
                "queries": queries[:5],
                "area_bias": str(item.get("area_bias") or ""),
                "anchor_strategy": str(item.get("anchor_strategy") or ""),
                "anchor_from_round": int(item.get("anchor_from_round") or 1),
                "anchor_top_k": max(1, min(5, int(item.get("anchor_top_k") or 2))),
                "radius_meters": max(300, min(10000, int(item.get("radius_meters") or 1800))),
                "max_results": max(1, min(10, int(item.get("max_results") or 5))),
            }
        )
    if not cleaned_rounds:
        return None
    if not any(item.get("tool") in {"keyword_search", "nearby_search"} for item in cleaned_rounds):
        return None

    primary = [str(x).strip() for x in (payload.get("primary_intents") or []) if str(x).strip()]
    secondary = [str(x).strip() for x in (payload.get("secondary_preferences") or []) if str(x).strip()]
    aliases = payload.get("search_aliases") if isinstance(payload.get("search_aliases"), dict) else {}
    cleaned = {
        "planner_mode": "llm_search_planned",
        "primary_intents": _unique(primary),
        "secondary_preferences": _unique(secondary),
        "clarification_needed": bool(payload.get("clarification_needed")),
        "clarification_question": payload.get("clarification_question") if payload.get("clarification_question") else None,
        "clarification_options": [str(x).strip() for x in (payload.get("clarification_options") or []) if str(x).strip()][:4],
        "target_area_hints": [str(x).strip() for x in (payload.get("target_area_hints") or []) if str(x).strip()][:6],
        "prefer_origin_nearby": bool(payload.get("prefer_origin_nearby")),
        "search_aliases": {str(k): [str(vv) for vv in (v if isinstance(v, list) else [v])] for k, v in aliases.items()},
        "search_rounds": cleaned_rounds,
    }
    cleaned["search_plan_summary"] = _summarize_plan(cleaned)
    return cleaned



def _parse_llm_payload(raw: str | None) -> Dict[str, Any] | None:
    payload = _load_json_lenient(raw)
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("choices"), list):
        choice = payload.get("choices", [None])[0]
        if isinstance(choice, dict):
            message = choice.get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return _load_json_lenient(content)
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                if parts:
                    return _load_json_lenient("\n".join(parts))
        return None
    return payload



def build_search_plan(user_query: str, request_context: dict, fast_mode: bool = False) -> dict:
    context = _request_context_dict(request_context)
    debug = {
        "llm_search_planner_called": False,
        "llm_search_planner_success": False,
        "llm_search_planner_error_type": None,
        "llm_search_planner_error_message": None,
    }
    if str(os.getenv(LLM_SEARCH_PLANNER_ENABLED_ENV, "1")).strip().lower() not in {"0", "false", "off", "no"}:
        debug["llm_search_planner_called"] = True
        try:
            raw = _call_llm_provider(_build_prompt(user_query, context, fast_mode))
            payload = _parse_llm_payload(raw)
            validated = _validate_plan(payload or {})
            if validated:
                validated.update(debug)
                validated["llm_search_planner_success"] = True
                return validated
            debug["llm_search_planner_error_type"] = "schema_validation_failed"
            debug["llm_search_planner_error_message"] = "LLM did not return a valid search_plan"
        except Exception as exc:  # pragma: no cover - provider failures vary
            debug["llm_search_planner_error_type"] = exc.__class__.__name__
            debug["llm_search_planner_error_message"] = str(exc)

    plan = _default_search_plan(user_query, context, mode="rule_first_fallback")
    plan.update(debug)
    return plan



def refine_search_plan(search_plan: dict, tool_results: dict, request_context: dict) -> dict:
    plan = dict(search_plan or {})
    total = int((tool_results or {}).get("total_discovered") or 0)
    if total > 0:
        plan["refine_reason"] = "tool_results_sufficient"
        return plan
    rounds = list(plan.get("search_rounds") or [])
    primary = list(plan.get("primary_intents") or [])
    if "park" in primary and not any("公园" in " ".join(r.get("queries") or []) for r in rounds):
        rounds.insert(
            0,
            {
                "goal": "broaden park search after weak results",
                "tool": "keyword_search",
                "queries": ["西安 公园", "城市公园", "湿地公园"],
                "area_bias": "citywide",
                "max_results": 5,
            },
        )
    plan["search_rounds"] = rounds[:3]
    plan["refine_reason"] = "weak_results_expand_queries"
    plan["search_plan_summary"] = _summarize_plan(plan)
    return plan



def _candidate_text(candidate: Dict[str, Any]) -> str:
    return " ".join(
        [
            str(candidate.get("name") or ""),
            str(candidate.get("category") or ""),
            " ".join(str(x) for x in (candidate.get("tags") or [])),
            str(candidate.get("district_cluster") or ""),
            str(candidate.get("area_name") or ""),
        ]
    ).lower()



def rerank_search_results(candidates: list, request_context: dict, search_plan: dict) -> list:
    if not candidates:
        return []
    primary = [str(x).lower() for x in (search_plan or {}).get("primary_intents") or []]
    aliases = search_plan.get("search_aliases") if isinstance(search_plan, dict) else {}
    alias_words: List[str] = []
    if isinstance(aliases, dict):
        for key in primary:
            alias_words.extend([str(x).lower() for x in aliases.get(key, [])])

    def score(candidate: Dict[str, Any]) -> tuple[int, float]:
        text = _candidate_text(candidate)
        value = 0
        if "park" in primary and _contains_any(text, [x.lower() for x in INTENT_ALIASES["park"]]):
            value += 8
        if "bbq" in primary and _contains_any(text, [x.lower() for x in INTENT_ALIASES["bbq"]]):
            value += 7
        if ("food" in primary or "bbq" in primary) and candidate.get("kind") == "restaurant":
            value += 3
        if ("low_walk" in primary or "parents" in primary or "family" in primary) and str(candidate.get("walking_level") or "") != "high":
            value += 2
        if "night_view" in primary and _contains_any(text, [x.lower() for x in INTENT_ALIASES["night_view"]]):
            value += 3
        if "rain" in primary and str(candidate.get("indoor_or_outdoor") or "") == "indoor":
            value += 3
        for word in alias_words:
            if word and word in text:
                value += 1
        try:
            base_score = float(candidate.get("_score") or 0)
        except (TypeError, ValueError):
            base_score = 0.0
        return (value, base_score)

    return sorted([dict(item) for item in candidates if isinstance(item, dict)], key=score, reverse=True)



def flatten_search_queries(search_plan: dict) -> List[str]:
    queries: List[str] = []
    for round_item in (search_plan or {}).get("search_rounds") or []:
        if not isinstance(round_item, dict):
            continue
        queries.extend(str(q).strip() for q in (round_item.get("queries") or []) if str(q).strip())
    return _unique(queries)
