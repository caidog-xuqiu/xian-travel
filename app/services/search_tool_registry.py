from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from app.models.schemas import PlanRequest
from app.services.discovery_sources import run_search_round
from app.services.data_loader import load_pois
from app.services.rag_embedding import create_embedding_provider
from app.services.rag_pinecone_store import PineconeConfig, RestPineconeVectorStore
from app.services.routing import get_route_info
from app.services.weather_service import get_weather_context


def _as_request(request_context: Any) -> PlanRequest | None:
    if isinstance(request_context, PlanRequest):
        return request_context
    if hasattr(request_context, "model_dump"):
        payload = request_context.model_dump()
    elif isinstance(request_context, dict):
        payload = dict(request_context)
    else:
        return None
    try:
        return PlanRequest(**payload)
    except Exception:
        return None


def _runtime_base_pois(runtime_context: Dict[str, Any], request: PlanRequest | None) -> List[Dict[str, Any]]:
    base = list(runtime_context.get("base_pois") or [])
    if base:
        return base
    if request is None:
        return []
    return load_pois(request_context=request)


def _pick_anchor(
    tool_input: Dict[str, Any],
    runtime_context: Dict[str, Any],
    discovered_pois: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    anchor_text = str(tool_input.get("anchor") or "").strip()
    pool = list(runtime_context.get("anchor_candidates") or []) + list(discovered_pois or [])
    if not pool:
        return None
    if not anchor_text:
        return pool[0]
    for item in pool:
        if anchor_text == str(item.get("id") or "") or anchor_text in str(item.get("name") or ""):
            return item
    return pool[0]


def _search_poi(
    tool_input: Dict[str, Any],
    request: PlanRequest | None,
    runtime_context: Dict[str, Any],
) -> Dict[str, Any]:
    queries = []
    for key in ["query", "keyword"]:
        value = str(tool_input.get(key) or "").strip()
        if value:
            queries.append(value)
    if not queries:
        return {"success": False, "fallback_reason": "missing_query", "result_count": 0, "pois": []}
    round_spec = {
        "tool": "keyword_search",
        "queries": queries,
        "max_results": int(tool_input.get("top_k") or 6),
    }
    context = {
        "request_context": request,
        "base_pois": _runtime_base_pois(runtime_context, request),
        "primary_strategies": runtime_context.get("search_strategy") or [],
    }
    output = run_search_round(round_spec, context, round_index=int(runtime_context.get("round_index") or 1))
    pois = list(output.get("results") or [])
    return {
        "success": True,
        "fallback_reason": (output.get("source_meta") or {}).get("fallback_reason"),
        "result_count": len(pois),
        "pois": pois,
        "source_meta": output.get("source_meta") or {},
    }


def _search_nearby(
    tool_input: Dict[str, Any],
    request: PlanRequest | None,
    runtime_context: Dict[str, Any],
    discovered_pois: List[Dict[str, Any]],
) -> Dict[str, Any]:
    queries = []
    for key in ["query", "keyword"]:
        value = str(tool_input.get(key) or "").strip()
        if value:
            queries.append(value)
    if not queries:
        return {"success": False, "fallback_reason": "missing_query", "result_count": 0, "pois": []}

    anchor = _pick_anchor(tool_input, runtime_context, discovered_pois)
    if not anchor:
        return {"success": False, "fallback_reason": "missing_anchor", "result_count": 0, "pois": []}

    round_spec = {
        "tool": "nearby_search",
        "queries": queries,
        "radius_meters": int(tool_input.get("radius_meters") or 1800),
        "max_results": int(tool_input.get("top_k") or 6),
    }
    context = {
        "request_context": request,
        "base_pois": _runtime_base_pois(runtime_context, request),
        "primary_strategies": runtime_context.get("search_strategy") or [],
    }
    output = run_search_round(
        round_spec,
        context,
        around_poi=anchor,
        round_index=int(runtime_context.get("round_index") or 1),
    )
    pois = list(output.get("results") or [])
    return {
        "success": True,
        "fallback_reason": (output.get("source_meta") or {}).get("fallback_reason"),
        "result_count": len(pois),
        "pois": pois,
        "anchor": {
            "id": anchor.get("id"),
            "name": anchor.get("name"),
        },
        "source_meta": output.get("source_meta") or {},
    }


def _retrieve_cases(tool_input: Dict[str, Any], request: PlanRequest | None) -> Dict[str, Any]:
    query = str(tool_input.get("query") or "").strip()
    if not query and request is not None:
        query = " ".join(
            [
                str(request.origin or ""),
                str(getattr(request.purpose, "value", request.purpose) or ""),
                "吃饭" if request.need_meal else "",
                "低步行" if str(getattr(request.walking_tolerance, "value", request.walking_tolerance)) == "low" else "",
            ]
        ).strip()
    if not query:
        return {"success": False, "fallback_reason": "missing_query", "result_count": 0, "cases": []}

    top_k = max(1, min(10, int(tool_input.get("top_k") or 5)))
    try:
        provider_name = str(os.getenv("RAG_EMBEDDING_PROVIDER", "hash") or "hash")
        hash_dim = int(os.getenv("RAG_HASH_DIM", "512") or 512)
        provider = create_embedding_provider(provider_name, dimension=hash_dim)
        vector = provider.embed_query(query)
        store = RestPineconeVectorStore(PineconeConfig.from_env())
        namespace = str(os.getenv("PINECONE_NAMESPACE", "route_cases_v1") or "route_cases_v1")
        hits = store.query(vector, top_k=top_k, namespace=namespace)
        cases = []
        for hit in hits:
            metadata = hit.get("metadata") or {}
            cases.append(
                {
                    "id": hit.get("id"),
                    "score": hit.get("score"),
                    "document_id": metadata.get("document_id"),
                    "route_tags": metadata.get("route_tags") or [],
                    "summary": str(metadata.get("text") or "")[:180],
                }
            )
        return {"success": True, "fallback_reason": None, "result_count": len(cases), "cases": cases}
    except Exception as exc:  # pragma: no cover - depends on runtime env/network
        return {
            "success": False,
            "fallback_reason": f"retrieve_cases_failed:{exc.__class__.__name__}",
            "result_count": 0,
            "cases": [],
        }


def _get_weather(request: PlanRequest | None) -> Dict[str, Any]:
    if request is None:
        return {"success": False, "fallback_reason": "invalid_request", "result_count": 0}
    try:
        context = get_weather_context(request)
        return {
            "success": True,
            "fallback_reason": None,
            "result_count": 1,
            "weather_context": context,
        }
    except Exception as exc:  # pragma: no cover
        return {
            "success": False,
            "fallback_reason": f"weather_failed:{exc.__class__.__name__}",
            "result_count": 0,
        }


def _plan_route(
    tool_input: Dict[str, Any],
    request: PlanRequest | None,
    discovered_pois: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if request is None:
        return {"success": False, "fallback_reason": "invalid_request", "result_count": 0}

    origin = str(tool_input.get("origin") or request.origin or "").strip()
    destination = str(tool_input.get("destination") or "").strip()
    if not destination and discovered_pois:
        destination = str(discovered_pois[0].get("name") or "").strip()
    if not origin or not destination:
        return {"success": False, "fallback_reason": "missing_route_endpoints", "result_count": 0}

    try:
        info = get_route_info(origin=origin, destination=destination, mode="public_transit")
        return {
            "success": True,
            "fallback_reason": None if info.source == "real_api" else "route_fallback_local",
            "result_count": 1,
            "route_summary": info.summary_text,
            "route_distance_meters": info.distance_meters,
            "route_duration_minutes": info.duration_minutes,
            "route_source": info.source,
        }
    except Exception as exc:  # pragma: no cover
        return {
            "success": False,
            "fallback_reason": f"route_failed:{exc.__class__.__name__}",
            "result_count": 0,
        }


def execute_search_action(
    action: Dict[str, Any],
    request_context: Any,
    runtime_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    runtime_context = dict(runtime_context or {})
    request = _as_request(request_context)
    decision = str(action.get("decision") or "")
    tool_input = dict(action.get("tool_input") or {})
    discovered_pois = list(runtime_context.get("discovered_pois") or [])

    if decision == "retrieve_cases":
        return _retrieve_cases(tool_input, request)
    if decision == "search_poi":
        return _search_poi(tool_input, request, runtime_context)
    if decision == "search_nearby":
        return _search_nearby(tool_input, request, runtime_context, discovered_pois)
    if decision == "get_weather":
        return _get_weather(request)
    if decision == "plan_route":
        return _plan_route(tool_input, request, discovered_pois)
    if decision == "clarify_user":
        options = [str(x) for x in (tool_input.get("clarification_options") or []) if str(x).strip()][:4]
        return {
            "success": True,
            "result_count": 0,
            "clarification_needed": True,
            "clarification_question": str(tool_input.get("clarification_question") or "你更想离起点近一些，还是更想去市中心逛？"),
            "clarification_options": options,
        }
    if decision == "finish":
        return {"success": True, "result_count": 0, "finished": True}
    if decision == "fallback":
        return {"success": False, "result_count": 0, "fallback_reason": str(action.get("reason") or "react_fallback")}

    return {"success": False, "result_count": 0, "fallback_reason": "unsupported_decision"}
