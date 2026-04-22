from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    ItineraryResponse,
    PlanFromTextReadableResponse,
    PlanFromTextResponse,
    PlanFromTextSelectionResponse,
    PlanReadableResponse,
    PlanRequest,
    PlanSelectionResponse,
    ReadableOutput,
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
