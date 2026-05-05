from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

from app.models.schemas import ItineraryResponse, PlanRequest, PlanSummary, ReadableOutput


class DebugLog(BaseModel):
    ts: str
    node: str | None = None
    level: Literal["info", "warn", "error"] = "info"
    message: str


class AgentState(BaseModel):
    user_input: str | None = None
    normalized_input: str | None = None
    parsed_request: PlanRequest | None = None
    parsed_by: Literal["llm", "rule", "unknown"] = "unknown"
    clarification_needed: bool = False
    clarification_question: str | None = None
    clarification_answer: str | None = None
    recalled_memory: Dict[str, Any] | None = None
    search_intent: Dict[str, Any] | None = None
    search_strategy: List[str] = Field(default_factory=list)
    search_mode: str = "rule_based"
    search_plan: Dict[str, Any] = Field(default_factory=dict)
    search_plan_used: Dict[str, Any] = Field(default_factory=dict)
    search_plan_summary: str | None = None
    search_round_count: int = 0
    search_rounds_debug: List[Dict[str, Any]] = Field(default_factory=list)
    react_steps: List[Dict[str, Any]] = Field(default_factory=list)
    react_fallback_reason: str | None = None
    search_round_outputs: List[Dict[str, Any]] = Field(default_factory=list)
    first_round_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    anchor_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    second_round_grouped_results: List[Dict[str, Any]] = Field(default_factory=list)
    llm_search_planner_called: bool = False
    llm_search_planner_success: bool = False
    llm_search_planner_error_type: str | None = None
    llm_search_planner_error_message: str | None = None
    search_rerank_used: bool = False
    final_search_queries: List[str] = Field(default_factory=list)
    clarification_from_search_planner: bool = False
    clarification_options: List[str] = Field(default_factory=list)
    primary_strategies: List[str] = Field(default_factory=list)
    secondary_strategies: List[str] = Field(default_factory=list)
    candidate_biases: List[str] = Field(default_factory=list)
    strategy_notes: List[str] = Field(default_factory=list)
    search_round: int = 0
    governed_pois: List[Dict[str, Any]] = Field(default_factory=list)
    data_quality_report: Dict[str, Any] = Field(default_factory=dict)
    quarantined_count: int = 0
    quality_issue_summary: Dict[str, int] = Field(default_factory=dict)
    discovered_pois: List[Dict[str, Any]] = Field(default_factory=list)
    discovered_pois_count: int = 0
    discovery_sources: List[str] = Field(default_factory=list)
    amap_called: bool = False
    amap_sources_used: List[str] = Field(default_factory=list)
    amap_route_used: bool = False
    amap_geo_used: bool = False
    amap_weather_used: bool = False
    amap_fallback_reason: str | None = None
    amap_events: List[Dict[str, Any]] = Field(default_factory=list)
    discovered_source_counts: Dict[str, int] = Field(default_factory=dict)
    area_scope_used: List[str] = Field(default_factory=list)
    area_priority_order: List[str] = Field(default_factory=list)
    discovered_area_counts: Dict[str, int] = Field(default_factory=dict)
    area_coverage_summary: Dict[str, Any] = Field(default_factory=dict)
    discovery_notes: List[str] = Field(default_factory=list)
    discovery_coverage_summary: Dict[str, Any] = Field(default_factory=dict)
    search_results: List[Dict[str, Any]] = Field(default_factory=list)
    search_results_count: int = 0
    planning_loop_enabled: bool = False
    planning_step_index: int = 0
    planning_max_steps: int = 3
    planning_history: List[Dict[str, Any]] = Field(default_factory=list)
    planning_action: str | None = None
    planning_reason: str | None = None
    planning_args: Dict[str, Any] | None = None
    revision_biases: List[str] = Field(default_factory=list)
    candidate_plans_count: int = 0
    finish_ready: bool = False
    weather_context: Dict[str, Any] | None = None
    route_source: str | None = None
    weather_source: str | None = None
    candidate_plans: List[Dict[str, Any]] = Field(default_factory=list)
    selected_plan: ItineraryResponse | None = None
    selected_plan_area_summary: Dict[str, Any] = Field(default_factory=dict)
    alternative_plans_summary: List[PlanSummary] = Field(default_factory=list)
    selection_reason: str | None = None
    reason_tags: List[str] = Field(default_factory=list)
    selected_by: Literal["llm", "fallback_rule", "unknown"] = "unknown"
    knowledge_used_count: int = 0
    knowledge_ids: List[str] = Field(default_factory=list)
    knowledge_bias: Dict[str, Any] = Field(default_factory=dict)
    explanation_basis: List[str] = Field(default_factory=list)
    retrieved_knowledge_count: int = 0
    knowledge_source_tags: List[str] = Field(default_factory=list)
    knowledge_usage_notes: List[str] = Field(default_factory=list)
    retrieved_case_count: int = 0
    retrieved_case_ids: List[int] = Field(default_factory=list)
    retrieved_case_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    case_bias: Dict[str, Any] = Field(default_factory=dict)
    case_memory_used: bool = False
    total_score: float | None = None
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    case_memory_id: int | None = None
    stored_to_case_memory: bool = False
    stored_reason: str | None = None
    readable_output: ReadableOutput | None = None
    memory_write_payload: Dict[str, Any] | None = None
    final_response: Dict[str, Any] | None = None
    thread_id: str | None = None
    current_step: str | None = None
    current_node: str | None = None
    active_skill: str | None = None
    skill_trace: List[Dict[str, Any]] = Field(default_factory=list)
    last_skill_result_summary: str | None = None
    errors: List[str] = Field(default_factory=list)
    user_key: str | None = None
    debug_logs: List[DebugLog] = Field(default_factory=list)


class AgentPlanRequest(BaseModel):
    text: str = Field(..., min_length=1)
    thread_id: str | None = None


class AgentPlanV2Request(BaseModel):
    text: str = Field(..., min_length=1)
    thread_id: str | None = None
    user_key: str | None = None


class AgentPlanV3Request(BaseModel):
    text: str = Field(..., min_length=1)
    thread_id: str | None = None
    user_key: str | None = None
    fast_mode: bool = False


class AgentPlanContinueRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    clarification_answer: str = Field(..., min_length=1)


class AgentPlanResponse(BaseModel):
    thread_id: str
    current_step: str | None = None
    current_node: str | None = None
    clarification_needed: bool = False
    clarification_question: str | None = None
    parsed_request: PlanRequest | None = None
    search_intent: Dict[str, Any] | None = None
    search_strategy: List[str] = Field(default_factory=list)
    search_mode: str = "rule_based"
    search_plan: Dict[str, Any] = Field(default_factory=dict)
    search_plan_used: Dict[str, Any] = Field(default_factory=dict)
    search_plan_summary: str | None = None
    search_round_count: int = 0
    search_rounds_debug: List[Dict[str, Any]] = Field(default_factory=list)
    react_steps: List[Dict[str, Any]] = Field(default_factory=list)
    react_fallback_reason: str | None = None
    search_round_outputs: List[Dict[str, Any]] = Field(default_factory=list)
    first_round_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    anchor_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    second_round_grouped_results: List[Dict[str, Any]] = Field(default_factory=list)
    llm_search_planner_called: bool = False
    llm_search_planner_success: bool = False
    llm_search_planner_error_type: str | None = None
    llm_search_planner_error_message: str | None = None
    search_rerank_used: bool = False
    final_search_queries: List[str] = Field(default_factory=list)
    clarification_from_search_planner: bool = False
    clarification_options: List[str] = Field(default_factory=list)
    candidate_biases: List[str] = Field(default_factory=list)
    strategy_notes: List[str] = Field(default_factory=list)
    search_round: int = 0
    data_quality_report: Dict[str, Any] = Field(default_factory=dict)
    quarantined_count: int = 0
    quality_issue_summary: Dict[str, int] = Field(default_factory=dict)
    discovered_pois_count: int = 0
    discovery_sources: List[str] = Field(default_factory=list)
    amap_called: bool = False
    amap_sources_used: List[str] = Field(default_factory=list)
    amap_route_used: bool = False
    amap_geo_used: bool = False
    amap_weather_used: bool = False
    amap_fallback_reason: str | None = None
    amap_events: List[Dict[str, Any]] = Field(default_factory=list)
    discovered_source_counts: Dict[str, int] = Field(default_factory=dict)
    area_scope_used: List[str] = Field(default_factory=list)
    area_priority_order: List[str] = Field(default_factory=list)
    discovered_area_counts: Dict[str, int] = Field(default_factory=dict)
    area_coverage_summary: Dict[str, Any] = Field(default_factory=dict)
    discovery_notes: List[str] = Field(default_factory=list)
    discovery_coverage_summary: Dict[str, Any] = Field(default_factory=dict)
    route_source: str | None = None
    weather_source: str | None = None
    planning_history: List[Dict[str, Any]] = Field(default_factory=list)
    search_results_count: int = 0
    candidate_plans_count: int = 0
    finish_ready: bool = False
    candidate_plans_summary: List[PlanSummary] = Field(default_factory=list)
    selected_plan: ItineraryResponse | None = None
    selected_plan_area_summary: Dict[str, Any] = Field(default_factory=dict)
    selection_reason: str | None = None
    reason_tags: List[str] = Field(default_factory=list)
    selected_by: Literal["llm", "fallback_rule", "unknown"] = "unknown"
    knowledge_used_count: int = 0
    knowledge_ids: List[str] = Field(default_factory=list)
    knowledge_bias: Dict[str, Any] = Field(default_factory=dict)
    explanation_basis: List[str] = Field(default_factory=list)
    retrieved_knowledge_count: int = 0
    knowledge_source_tags: List[str] = Field(default_factory=list)
    knowledge_usage_notes: List[str] = Field(default_factory=list)
    retrieved_case_count: int = 0
    retrieved_case_ids: List[int] = Field(default_factory=list)
    retrieved_case_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    case_bias: Dict[str, Any] = Field(default_factory=dict)
    case_memory_used: bool = False
    total_score: float | None = None
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    case_memory_id: int | None = None
    stored_to_case_memory: bool = False
    stored_reason: str | None = None
    active_skill: str | None = None
    skill_trace: List[Dict[str, Any]] = Field(default_factory=list)
    last_skill_result_summary: str | None = None
    readable_output: ReadableOutput | None = None
    final_response: Dict[str, Any] | None = None
    errors: List[str] = Field(default_factory=list)
    debug_logs: List[DebugLog] = Field(default_factory=list)


