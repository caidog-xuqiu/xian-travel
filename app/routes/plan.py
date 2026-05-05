from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    ItineraryResponse,
    PlanFromTextReadableResponse,
    PlanFromTextResponse,
    PlanFromTextSelectionResponse,
    PlanReadableResponse,
    PlanRequest,
    PlanSelectionResponse,
    ReadableOutput,
    RouteFeedbackRequest,
    RouteFeedbackResponse,
    RouteMemoryItem,
    RouteMemoryResponse,
    TextPlanRequest,
)
from app.services.itinerary_renderer import render_itinerary_text
from app.services.plan_selector import select_best_plan
from app.services.planner import generate_itinerary
from app.services.request_parser import parse_free_text_to_plan_request
from app.services.agent_graph import continue_agent, run_agent, run_agent_v2, run_agent_v3
from app.services.agent_state import (
    AgentPlanContinueRequest,
    AgentPlanRequest,
    AgentPlanResponse,
    AgentPlanV2Request,
    AgentPlanV3Request,
)
from app.services import sqlite_store
from app.services.case_memory import save_high_quality_case
from app.services.route_scoring import STORE_SCORE_THRESHOLD, score_route_case, score_with_user_feedback

router = APIRouter()


@router.post("/plan", response_model=ItineraryResponse)
def plan_trip(request: PlanRequest) -> ItineraryResponse:
    return generate_itinerary(request)


@router.post("/plan-from-text", response_model=PlanFromTextResponse)
def plan_trip_from_text(payload: TextPlanRequest) -> PlanFromTextResponse:
    try:
        parsed_request = parse_free_text_to_plan_request(payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    itinerary = generate_itinerary(parsed_request)
    return PlanFromTextResponse(parsed_request=parsed_request, itinerary=itinerary)


@router.post("/plan-readable", response_model=PlanReadableResponse)
def plan_trip_readable(request: PlanRequest) -> PlanReadableResponse:
    itinerary = generate_itinerary(request)
    readable_dict = render_itinerary_text(itinerary=itinerary, request=request)
    return PlanReadableResponse(itinerary=itinerary, readable_output=ReadableOutput(**readable_dict))


@router.post("/plan-from-text-readable", response_model=PlanFromTextReadableResponse)
def plan_trip_from_text_readable(payload: TextPlanRequest) -> PlanFromTextReadableResponse:
    try:
        parsed_request = parse_free_text_to_plan_request(payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    itinerary = generate_itinerary(parsed_request)
    readable_dict = render_itinerary_text(
        itinerary=itinerary,
        request=parsed_request,
        parsed_request=parsed_request,
    )
    return PlanFromTextReadableResponse(
        parsed_request=parsed_request,
        itinerary=itinerary,
        readable_output=ReadableOutput(**readable_dict),
    )


@router.post("/plan-with-llm-selection", response_model=PlanSelectionResponse)
def plan_with_llm_selection(request: PlanRequest) -> PlanSelectionResponse:
    result = select_best_plan(request)
    return PlanSelectionResponse(
        selected_plan=result["selected_plan"],
        alternative_plans_summary=result["alternative_plans_summary"],
        selection_reason=result["selection_reason"],
        reason_tags=result["reason_tags"],
        selected_by=result["selected_by"],
        readable_output=ReadableOutput(**result["readable_output"]),
    )


@router.post("/plan-from-text-with-llm-selection", response_model=PlanFromTextSelectionResponse)
def plan_from_text_with_llm_selection(payload: TextPlanRequest) -> PlanFromTextSelectionResponse:
    try:
        parsed_request = parse_free_text_to_plan_request(payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = select_best_plan(parsed_request)
    return PlanFromTextSelectionResponse(
        parsed_request=parsed_request,
        selected_plan=result["selected_plan"],
        alternative_plans_summary=result["alternative_plans_summary"],
        selection_reason=result["selection_reason"],
        reason_tags=result["reason_tags"],
        selected_by=result["selected_by"],
        readable_output=ReadableOutput(**result["readable_output"]),
    )


@router.post("/agent-plan", response_model=AgentPlanResponse)
def agent_plan(payload: AgentPlanRequest) -> AgentPlanResponse:
    return run_agent(text=payload.text, thread_id=payload.thread_id)


@router.post("/agent-plan-v2", response_model=AgentPlanResponse)
def agent_plan_v2(payload: AgentPlanV2Request) -> AgentPlanResponse:
    return run_agent_v2(text=payload.text, thread_id=payload.thread_id, user_key=payload.user_key)


@router.post("/agent-plan-v3", response_model=AgentPlanResponse)
def agent_plan_v3(payload: AgentPlanV3Request) -> AgentPlanResponse:
    if payload.fast_mode:
        return run_agent_v3(
            text=payload.text,
            thread_id=payload.thread_id,
            user_key=payload.user_key,
            fast_mode=True,
        )
    return run_agent_v3(
        text=payload.text,
        thread_id=payload.thread_id,
        user_key=payload.user_key,
    )


@router.post("/agent-plan/continue", response_model=AgentPlanResponse)
def agent_plan_continue(payload: AgentPlanContinueRequest) -> AgentPlanResponse:
    try:
        return continue_agent(thread_id=payload.thread_id, clarification_answer=payload.clarification_answer)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "thread_id" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/route-feedback", response_model=RouteFeedbackResponse)
def submit_route_feedback(payload: RouteFeedbackRequest) -> RouteFeedbackResponse:
    if payload.system_score_breakdown:
        score_result = score_with_user_feedback(payload.system_score_breakdown, payload.user_rating)
        breakdown = score_result["score_breakdown"]
        score_result["constraints_met"] = bool(
            breakdown.get("constraints_met", float(breakdown.get("raw_constraint_score") or 0.0) >= 3.0)
        )
        score_result["serious_fallback"] = bool(breakdown.get("serious_fallback", False))
        score_result["route_source"] = (payload.route_summary or {}).get("route_source")
    else:
        score_result = score_route_case(
            request_context=payload.parsed_request or {},
            selected_plan=payload.itinerary,
            selected_plan_area_summary=payload.route_summary or {},
            route_source=(payload.route_summary or {}).get("route_source"),
            user_rating=payload.user_rating,
        )

    feedback_id = sqlite_store.save_route_feedback(
        {
            "case_memory_id": payload.case_memory_id,
            "user_key": payload.user_key,
            "user_query": payload.user_query,
            "user_rating": payload.user_rating,
            "feedback_text": payload.feedback_text,
        }
    )

    case_memory_id = payload.case_memory_id
    stored_to_case_memory = bool(case_memory_id)
    stored_reason = "updated_existing_case_memory" if case_memory_id else None
    if case_memory_id:
        sqlite_store.update_route_case_feedback(
            case_memory_id,
            total_score=float(score_result.get("total_score") or 0.0),
            user_feedback_score=float(score_result.get("user_feedback_score") or 0.0),
            user_feedback_text=payload.feedback_text,
        )
    else:
        stored = save_high_quality_case(
            user_key=payload.user_key,
            user_query=payload.user_query,
            parsed_request=payload.parsed_request or {},
            selected_plan=payload.selected_plan,
            itinerary=payload.itinerary,
            route_summary=payload.route_summary or {},
            knowledge_ids=payload.knowledge_ids,
            knowledge_bias=payload.knowledge_bias,
            score_result=score_result,
            user_feedback_text=payload.feedback_text,
        )
        stored_to_case_memory = bool(stored.get("stored_to_case_memory"))
        case_memory_id = stored.get("case_memory_id")
        stored_reason = stored.get("stored_reason")

    return RouteFeedbackResponse(
        final_total_score=float(score_result.get("total_score") or 0.0),
        score_breakdown=score_result.get("score_breakdown") or {},
        stored_to_case_memory=stored_to_case_memory,
        case_memory_id=case_memory_id,
        feedback_id=feedback_id,
        stored_reason=stored_reason,
    )


@router.get("/route-memory", response_model=RouteMemoryResponse)
def list_route_memory(
    user_key: str | None = None,
    limit: int = Query(default=5, ge=1, le=20),
) -> RouteMemoryResponse:
    cases = sqlite_store.list_recent_high_score_cases(user_key=user_key, limit=limit, min_score=STORE_SCORE_THRESHOLD)
    return RouteMemoryResponse(
        items=[
            RouteMemoryItem(
                case_id=int(item["id"]),
                query=str(item.get("user_query") or "")[:60],
                score=float(item.get("total_score") or 0.0),
                selected_plan=item.get("selected_plan"),
                created_at=item.get("created_at"),
            )
            for item in cases
        ]
    )
