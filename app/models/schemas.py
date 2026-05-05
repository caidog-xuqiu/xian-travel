from enum import Enum
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field


class CompanionType(str, Enum):
    solo = "solo"
    parents = "parents"
    friends = "friends"
    partner = "partner"


class BudgetLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Purpose(str, Enum):
    tourism = "tourism"
    relax = "relax"
    food = "food"
    dating = "dating"


class WalkingTolerance(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Weather(str, Enum):
    sunny = "sunny"
    rainy = "rainy"
    hot = "hot"
    cold = "cold"


class TransportPreference(str, Enum):
    drive = "drive"
    taxi = "taxi"
    public_transit = "public_transit"
    walking = "walking"


class TrafficSensitivity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class RestaurantRatingPreference(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ParkingTolerance(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class PreferredTripStyle(str, Enum):
    relaxed = "relaxed"
    balanced = "balanced"
    dense = "dense"


class TastePreferences(BaseModel):
    avoid_spicy: bool = False
    prefer_light: bool = False
    avoid_heavy_flavor: bool = False


class PlanRequest(BaseModel):
    """路线规划请求。

    版本范围:
    - V1 为西安市区核心路线版，仅覆盖碑林区、莲湖区、雁塔区
    - 选点簇固定为: 城墙钟鼓楼簇、小寨文博簇、大雁塔簇、曲江夜游簇

    已接收并校验字段:
    - companion_type, available_hours, budget_level, purpose, need_meal,
      walking_tolerance, weather, origin, preferred_period,
      origin_preference_mode, parsed_by
    - has_car, transport_preference, traffic_sensitivity, taste_preferences,
      restaurant_rating_preference, parking_tolerance, avoid_queue,
      preferred_trip_style

    V1.5 实际参与打分/排程字段:
    - companion_type, available_hours, budget_level, purpose(粗粒度),
      need_meal, walking_tolerance, weather(作为手动值/兜底值)
    - origin(用于首段路线代价查询；失败时回退)
    - preferred_period(轻量时段信号：morning/midday/afternoon/evening)
    - origin_preference_mode(仅 nearby 轻量影响首站偏好)
    - parsed_by(标记解析来源：llm 或 rule)
    - transport_preference, has_car(轻量影响路段模式选择，主要在真实路线可用时生效)

    天气字段关系:
    - 优先使用 weather_service 的实时天气上下文
    - request.weather 保留并作为实时天气不可用时的兜底输入

    当前未进入复杂策略主链:
    - traffic_sensitivity, taste_preferences, restaurant_rating_preference,
      parking_tolerance, avoid_queue, preferred_trip_style
    - preferred_period（当前仅作时段起始偏好与轻量候选偏置信号）
    - origin_preference_mode（当前仅作首站邻近偏好信号）
    - parsed_by（仅用于调试与统计，不进入评分/排程）
    """

    companion_type: CompanionType
    available_hours: float = Field(..., gt=0, le=24)
    budget_level: BudgetLevel
    # V1: 粗粒度目的偏好，会参与 score_poi。
    purpose: Purpose
    need_meal: bool = True
    walking_tolerance: WalkingTolerance
    weather: Weather
    # V1.5: 用于首段路线代价查询（真实 API 或降级估算）。
    origin: str = Field(..., min_length=1)
    # V4.1: 可选起点地理增强（由解析层或上游地理服务填充，未命中时为 None）。
    origin_latitude: float | None = None
    origin_longitude: float | None = None
    origin_adcode: str | None = None
    # V1.12: 自然语言入口的轻量时段信号（morning/midday/afternoon/evening）。
    preferred_period: Literal["morning", "midday", "afternoon", "evening"] | None = None
    # V1.13: 自然语言入口的轻量邻近信号（仅 nearby 生效）。
    origin_preference_mode: Literal["nearby"] | None = None
    # V1.14: 解析来源（llm 或 rule），用于调试/追踪。
    parsed_by: Literal["llm", "rule"] = "rule"

    # V1.5: has_car / transport_preference 会轻量影响默认交通模式。
    # 其余字段保持 V2 预留，仅接收并校验。
    has_car: bool = False
    transport_preference: TransportPreference = TransportPreference.public_transit
    traffic_sensitivity: TrafficSensitivity = TrafficSensitivity.medium
    taste_preferences: TastePreferences = Field(default_factory=TastePreferences)
    restaurant_rating_preference: RestaurantRatingPreference = RestaurantRatingPreference.medium
    parking_tolerance: ParkingTolerance = ParkingTolerance.medium
    avoid_queue: bool = False
    preferred_trip_style: PreferredTripStyle = PreferredTripStyle.balanced

    model_config = ConfigDict(extra="forbid")


class POIFacilities(BaseModel):
    child_friendly: bool | None = None
    accessible: bool | None = None
    stroller_friendly: bool | None = None


class POISuitability(BaseModel):
    parent_friendly: bool | None = None
    friend_friendly: bool | None = None
    couple_friendly: bool | None = None
    child_friendly: bool | None = None


class POI(BaseModel):
    id: str
    name: str
    kind: Literal["sight", "restaurant"]
    district_cluster: str
    category: str
    rating: float | None = None
    rating_count: int | None = None
    facilities: POIFacilities | None = None
    suitability: POISuitability | None = None
    open_time: str | None = None
    close_time: str | None = None
    is_all_day: bool | None = None


class TextPlanRequest(BaseModel):
    text: str = Field(..., min_length=1, description="自然语言行程需求")


class RouteItem(BaseModel):
    time_slot: str
    type: Literal["sight", "restaurant"]
    name: str
    district_cluster: str
    transport_from_prev: str
    reason: str
    estimated_distance_meters: int | None = None
    estimated_duration_minutes: int | None = None


class ItineraryResponse(BaseModel):
    summary: str
    route: List[RouteItem]
    tips: List[str]


class PlanFromTextResponse(BaseModel):
    parsed_request: PlanRequest
    itinerary: ItineraryResponse


class ReadableOutput(BaseModel):
    title: str
    overview: str
    schedule_text: str
    transport_text: str
    tips_text: str


class PlanReadableResponse(BaseModel):
    itinerary: ItineraryResponse
    readable_output: ReadableOutput


class PlanFromTextReadableResponse(BaseModel):
    parsed_request: PlanRequest
    itinerary: ItineraryResponse
    readable_output: ReadableOutput


class PlanSummary(BaseModel):
    plan_id: str
    variant_label: str
    stop_count: int
    clusters: List[str]
    is_cross_cluster: bool
    cross_cluster_count: int
    cluster_transition_summary: str
    is_cross_area: bool = False
    cross_area_count: int = 0
    area_transition_summary: str = ""
    area_bias_note: str | None = None
    has_meal: bool
    total_distance_meters: int
    total_duration_minutes: int
    rhythm: str
    budget_level: str
    walking_tolerance: str
    purpose: str
    diff_points: List[str]
    bias_tags: List[str]
    knowledge_tags: List[str] = Field(default_factory=list)
    knowledge_notes: List[str] = Field(default_factory=list)
    place_context_note: str | None = None
    note: str


class PlanSelectionResponse(BaseModel):
    selected_plan: ItineraryResponse
    alternative_plans_summary: List[PlanSummary]
    selection_reason: str
    reason_tags: List[str]
    selected_by: Literal["llm", "fallback_rule"]
    readable_output: ReadableOutput


class PlanFromTextSelectionResponse(BaseModel):
    parsed_request: PlanRequest
    selected_plan: ItineraryResponse
    alternative_plans_summary: List[PlanSummary]
    selection_reason: str
    reason_tags: List[str]
    selected_by: Literal["llm", "fallback_rule"]
    readable_output: ReadableOutput


class RouteFeedbackRequest(BaseModel):
    user_key: str | None = None
    user_query: str = Field(..., min_length=1)
    selected_plan: str | None = None
    itinerary: Dict[str, Any] = Field(default_factory=dict)
    system_score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    user_rating: int = Field(..., ge=1, le=10)
    feedback_text: str | None = None
    case_memory_id: int | None = None
    parsed_request: Dict[str, Any] | None = None
    route_summary: Dict[str, Any] | None = None
    knowledge_ids: List[str] = Field(default_factory=list)
    knowledge_bias: Dict[str, Any] = Field(default_factory=dict)


class RouteFeedbackResponse(BaseModel):
    final_total_score: float
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    stored_to_case_memory: bool = False
    case_memory_id: int | None = None
    feedback_id: int | None = None
    stored_reason: str | None = None


class RouteMemoryItem(BaseModel):
    case_id: int
    query: str
    score: float
    selected_plan: str | None = None
    created_at: str | None = None


class RouteMemoryResponse(BaseModel):
    items: List[RouteMemoryItem] = Field(default_factory=list)
