from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List


@dataclass(frozen=True)
class SkillDescriptor:
    skill_name: str
    description: str
    input_schema_hint: str
    output_schema_hint: str


_REGISTERED_SKILLS: List[SkillDescriptor] = [
    SkillDescriptor(
        skill_name="parse_request_skill",
        description="将自然语言需求解析为 PlanRequest 参数结构。",
        input_schema_hint="{text: string}",
        output_schema_hint="{parsed_request: PlanRequest, parsed_by: 'rule'|'llm'}",
    ),
    SkillDescriptor(
        skill_name="recall_memory_skill",
        description="召回用户历史偏好与 thread 记忆。",
        input_schema_hint="{user_key?: string, thread_id?: string}",
        output_schema_hint="{recalled_memory: object}",
    ),
    SkillDescriptor(
        skill_name="search_candidate_skill",
        description="按策略执行候选点发现与补搜。",
        input_schema_hint="{query: string, strategy: string[]}",
        output_schema_hint="{search_results: POI[]}",
    ),
    SkillDescriptor(
        skill_name="generate_candidates_skill",
        description="将 POI 候选组合成 2~3 条可比较路线方案。",
        input_schema_hint="{parsed_request: PlanRequest, search_results?: POI[]}",
        output_schema_hint="{candidate_plans: Itinerary[], summaries: PlanSummary[]}",
    ),
    SkillDescriptor(
        skill_name="select_plan_skill",
        description="在候选方案内做 LLM 选优，并带回退保障。",
        input_schema_hint="{candidate_summaries: PlanSummary[], parsed_request: PlanRequest}",
        output_schema_hint="{selected_plan_id: string, selection_reason: string, reason_tags: string[]}",
    ),
    SkillDescriptor(
        skill_name="render_response_skill",
        description="把结构化 itinerary 渲染为用户可读中文文案。",
        input_schema_hint="{itinerary: object, parsed_request?: PlanRequest}",
        output_schema_hint="{readable_output: object}",
    ),
    SkillDescriptor(
        skill_name="knowledge_enrichment_skill",
        description="基于知识层为候选摘要、选优理由和可读文案补充短解释标签。",
        input_schema_hint="{query: string, context: object, summary?: PlanSummary}",
        output_schema_hint="{knowledge_tags: string[], knowledge_notes: string[]}",
    ),
    SkillDescriptor(
        skill_name="evaluation_skill",
        description="执行固定回归用例，产出聚合评估与版本对比结果。",
        input_schema_hint="{endpoint_name: string, cases: EvalCase[]}",
        output_schema_hint="{summary: object, details: object[], compare?: object}",
    ),
    SkillDescriptor(
        skill_name="amap_search_skill",
        description="调用高德 Web 搜索能力（关键词/附近）发现候选点位。",
        input_schema_hint="{query: string, strategies?: string[], area_scope?: string[]}",
        output_schema_hint="{pois: POI[], source: 'amap_web_search'}",
    ),
    SkillDescriptor(
        skill_name="amap_geocode_skill",
        description="调用高德地理编码/逆地理编码规范化起点与坐标。",
        input_schema_hint="{origin_text: string}",
        output_schema_hint="{origin: string, latitude?: number, longitude?: number, adcode?: string}",
    ),
    SkillDescriptor(
        skill_name="amap_route_skill",
        description="调用高德路径规划获取 walking/driving 距离和时长。",
        input_schema_hint="{origin: lnglat, destination: lnglat, mode: 'walking'|'driving'}",
        output_schema_hint="{distance_meters: int, duration_minutes: int, source: 'amap_route'}",
    ),
    SkillDescriptor(
        skill_name="amap_weather_skill",
        description="调用高德天气接口获取实时天气上下文。",
        input_schema_hint="{city_or_adcode: string}",
        output_schema_hint="{weather_condition: string, temperature_c?: number, is_rainy: bool, is_hot: bool}",
    ),
]

_NODE_SKILL_MAP: Dict[str, str] = {
    "parse_request": "parse_request_skill",
    "recall_memory": "recall_memory_skill",
    "candidate_discovery": "search_candidate_skill",
    "dynamic_search": "search_candidate_skill",
    "generate_candidates": "generate_candidates_skill",
    "select_plan": "select_plan_skill",
    "render_output": "render_response_skill",
    "knowledge_enrichment": "knowledge_enrichment_skill",
    "evaluation": "evaluation_skill",
    "amap_search": "amap_search_skill",
    "amap_geocode": "amap_geocode_skill",
    "amap_route": "amap_route_skill",
    "amap_weather": "amap_weather_skill",
}

_PLANNING_ACTION_SKILL_MAP: Dict[str, str] = {
    "SEARCH": "search_candidate_skill",
    "GENERATE_CANDIDATES": "generate_candidates_skill",
    "REVISE": "generate_candidates_skill",
    "FINISH": "select_plan_skill",
}


def list_registered_skills() -> List[SkillDescriptor]:
    """Return all registered skill descriptors."""

    return list(_REGISTERED_SKILLS)


def get_skill_descriptor(skill_name: str) -> SkillDescriptor | None:
    """Get one skill descriptor by name."""

    for descriptor in _REGISTERED_SKILLS:
        if descriptor.skill_name == skill_name:
            return descriptor
    return None


def build_skill_catalog() -> List[Dict[str, str]]:
    """Build serializable skill catalog for docs/debug APIs.

    This registry is a capability directory scaffold.
    It does not change existing business execution logic.
    """

    return [asdict(skill) for skill in _REGISTERED_SKILLS]


def get_skill_for_node(node_name: str) -> str | None:
    """Get runtime skill name for one agent graph node."""

    return _NODE_SKILL_MAP.get(node_name)


def get_skill_for_planning_action(action: str) -> str | None:
    """Get runtime skill name for one planning-loop action."""

    return _PLANNING_ACTION_SKILL_MAP.get((action or "").upper())


def get_active_skills_for_agent() -> List[str]:
    """Return active skills used by current single-agent main chain."""

    names: List[str] = []
    for skill_name in _NODE_SKILL_MAP.values():
        if skill_name not in names:
            names.append(skill_name)
    for skill_name in _PLANNING_ACTION_SKILL_MAP.values():
        if skill_name not in names:
            names.append(skill_name)
    return names
