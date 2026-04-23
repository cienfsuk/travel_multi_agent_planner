from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


TravelStyle = Literal["relaxed", "balanced", "dense"]
BudgetPreference = Literal["budget", "balanced", "premium"]
AppMode = Literal["online", "fallback"]
IssueSeverity = Literal["low", "medium", "high"]
MealSuitability = Literal["lunch", "dinner", "both"]
TransportType = Literal["intercity", "taxi", "metro", "bus", "walk"]
EvidenceType = Literal["大模型整理", "网页检索", "攻略聚合"]


@dataclass
class TripRequest:
    destination: str
    days: int
    budget: float
    origin: str = "Shanghai"
    departure_date: str = ""
    traveler_count: int = 1
    interests: list[str] = field(default_factory=lambda: ["culture", "food", "nature"])
    preferred_areas: list[str] = field(default_factory=list)
    avoid_tags: list[str] = field(default_factory=list)
    food_tastes: list[str] = field(default_factory=list)
    style: TravelStyle = "balanced"
    food_budget_preference: BudgetPreference = "balanced"
    hotel_budget_preference: BudgetPreference = "balanced"
    must_have_hotel_area: str = ""
    travel_note_style: str = "小红书风格"
    additional_notes: str = ""


@dataclass
class TravelConstraints:
    max_daily_spots: int
    max_daily_transport_transfers: int
    max_total_budget: float
    preferred_areas: list[str] = field(default_factory=list)
    must_have_tags: list[str] = field(default_factory=list)
    avoid_tags: list[str] = field(default_factory=list)
    must_include_spots: list[str] = field(default_factory=list)
    must_include_spots_by_day: dict[int, list[str]] = field(default_factory=dict)
    pacing_note: str = ""


@dataclass
class CityMatch:
    input_name: str
    normalized_query: str
    confirmed_name: str
    region: str
    country: str
    provider: str
    lat: float
    lon: float
    city_code: str = ""


@dataclass
class EvidenceItem:
    title: str
    source_url: str
    snippet: str
    provider: str
    retrieved_at: str
    evidence_type: EvidenceType = "网页检索"
    provider_label: str = ""
    poi_id: str = ""


@dataclass
class PointOfInterest:
    name: str
    category: str
    district: str
    description: str
    duration_hours: float
    ticket_cost: float
    lat: float
    lon: float
    tags: list[str]
    best_time: str
    opening_hours: str = ""
    address: str = ""
    estimated_visit_window: str = ""
    source_evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass
class FoodVenue:
    name: str
    district: str
    cuisine: str
    description: str
    average_cost: float
    tags: list[str]
    taste_profile: list[str] = field(default_factory=list)
    recommended_meal: str = "flexible"
    meal_suitability: MealSuitability = "both"
    lat: float = 0.0
    lon: float = 0.0
    address: str = ""
    source_evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass
class HotelVenue:
    name: str
    district: str
    description: str
    price_per_night: float
    lat: float
    lon: float
    address: str = ""
    tags: list[str] = field(default_factory=list)
    source_evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass
class TravelNote:
    title: str
    summary: str
    style_tag: str
    source_url: str
    provider: str
    evidence_type: EvidenceType = "攻略聚合"


@dataclass
class CityProfile:
    city: str
    aliases: list[str]
    intro: str
    local_transport_tip: str
    daily_local_transport_cost: float
    accommodation_budget: dict[str, float]
    intercity_transport: dict[str, dict[str, float | str]]
    recommended_seasons: list[str]
    pois: list[PointOfInterest]
    foods: list[FoodVenue]
    hotels: list[HotelVenue] = field(default_factory=list)


@dataclass
class MealRecommendation:
    venue_name: str
    meal_type: Literal["lunch", "dinner"]
    estimated_cost: float
    reason: str
    venue_district: str = ""
    cuisine: str = ""
    lat: float = 0.0
    lon: float = 0.0
    anchor_distance_km: float = 0.0
    route_distance_km: float = 0.0
    fallback_used: bool = False
    selection_tier: str = "strict"
    source_evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass
class IntercityOption:
    mode: str
    transport_code: str
    from_station: str
    to_station: str
    depart_time: str
    arrive_time: str
    duration_minutes: int
    price_cny: float
    seat_label: str
    queried_at: str
    source_name: str
    source_url: str
    train_no: str = ""
    from_station_no: str = ""
    to_station_no: str = ""
    seat_types: str = ""
    travel_date: str = ""
    confidence: str = "queried"
    note: str = ""


@dataclass
class TransportSegment:
    segment_type: TransportType
    from_label: str
    to_label: str
    duration_minutes: int
    estimated_cost: float
    description: str
    distance_km: float = 0.0
    path: list[list[float]] = field(default_factory=list)
    path_status: str = "ok"
    transport_code: str = ""
    depart_time: str = ""
    arrive_time: str = ""
    queried_at: str = ""
    source_name: str = ""
    source_url: str = ""
    confidence: str = "estimated"


@dataclass
class DailyTransportPlan:
    inbound: str
    intra_city: str
    walking_intensity: str
    estimated_duration_minutes: int = 0
    estimated_cost: float = 0.0
    route_summary: str = ""
    route_path: list[list[float]] = field(default_factory=list)


@dataclass
class DayPlan:
    day: int
    theme: str
    spots: list[PointOfInterest]
    meals: list[MealRecommendation]
    hotel: HotelVenue | None
    transport: DailyTransportPlan
    transport_segments: list[TransportSegment]
    notes: list[str]
    arrival_segment: TransportSegment | None = None
    departure_segment: TransportSegment | None = None
    weather_summary: str = ""


@dataclass
class BudgetLine:
    category: str
    amount: float
    note: str


@dataclass
class BudgetSummary:
    total_estimated: float
    remaining_budget: float
    is_within_budget: bool
    lines: list[BudgetLine]


@dataclass
class ValidationIssue:
    severity: IssueSeverity
    category: str
    message: str
    day: int | None
    suggested_fix: str


@dataclass
class AgentTraceStep:
    agent_name: str
    input_summary: str
    output_summary: str
    key_decisions: list[str]
    status: str = "ok"


@dataclass
class ProviderStatus:
    name: str
    active: bool
    detail: str


@dataclass
class RoutePoint:
    day: int
    label: str
    lat: float
    lon: float
    kind: str
    source: str = ""
    slot_label: str = ""
    transport_hint: str = ""


@dataclass
class MapNode:
    day: int
    order: int
    name: str
    kind: str
    slot: str
    desc: str
    lat: float
    lon: float
    color: str
    marker_text: str
    sequence_id: int
    visible_frame_start: int


@dataclass
class MapSegment:
    day: int
    order: int
    from_name: str
    to_name: str
    segment_type: str
    path: list[list[float]]
    color: str
    duration: int
    cost: float
    desc: str
    sequence_id: int
    visible_frame_start: int
    visible_frame_end: int
    arrow_lon: float
    arrow_lat: float
    angle: float
    distance_km: float = 0.0
    arrow_text: str = "➜"
    path_status: str = "ok"


@dataclass
class MapViewModel:
    nodes: list[MapNode]
    segments: list[MapSegment]
    total_frames: int
    center_lat: float
    center_lon: float


@dataclass
class AnimationNode:
    day: int
    step_index: int
    title: str
    kind: str
    label: str
    desc: str
    lat: float
    lon: float
    color: str
    frame_start: int
    marker_text: str
    day_color: str = ""
    type_color: str = ""
    address: str = ""
    district: str = ""


@dataclass
class AnimationSegment:
    day: int
    step_index: int
    from_title: str
    to_title: str
    segment_type: str
    path: list[list[float]]
    color: str
    frame_start: int
    frame_end: int
    timestamps: list[float]
    duration: int
    cost: float
    desc: str
    arrow_lon: float
    arrow_lat: float
    angle: float
    distance_km: float = 0.0
    arrow_text: str = "➜"
    path_status: str = "ok"


@dataclass
class AnimationStep:
    day: int
    step_index: int
    headline: str
    subheadline: str
    sidebar_title: str
    sidebar_desc: str
    node_refs: list[int]
    segment_refs: list[int]
    frame_start: int
    address: str = ""
    weather_note: str = ""
    next_transport_type: str = ""
    next_transport_duration: int = 0
    next_transport_cost: float = 0.0
    next_transport_distance_km: float = 0.0
    next_transport_desc: str = ""


@dataclass
class AnimationBundle:
    case_id: str
    request_summary: dict
    nodes: list[AnimationNode]
    segments: list[AnimationSegment]
    steps: list[AnimationStep]
    total_frames: int
    bundle_version: int = 4
    default_day_mode: str = "全部"
    default_play_mode: str = "静态全览"
    generated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TripPlan:
    request: TripRequest
    constraints: TravelConstraints
    city_profile: CityProfile
    origin_match: CityMatch
    destination_match: CityMatch
    day_plans: list[DayPlan]
    budget_summary: BudgetSummary
    validation_issues: list[ValidationIssue]
    summary_markdown: str
    route_points: list[RoutePoint]
    warnings: list[str]
    trace: list[AgentTraceStep]
    final_score: float
    mode: AppMode
    provider_statuses: list[ProviderStatus]
    travel_notes: list[TravelNote] = field(default_factory=list)
    evidence_mode_summary: str = ""
    was_revised: bool = False
    search_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
