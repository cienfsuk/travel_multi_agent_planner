// TypeScript interfaces mirroring the Python dataclass models

export type TravelStyle = "relaxed" | "balanced" | "dense";
export type BudgetPreference = "budget" | "balanced" | "premium";

export interface TripRequest {
  destination: string;
  days: number;
  budget: number;
  origin: string;
  departure_date?: string;
  traveler_count?: number;
  interests?: string[];
  preferred_areas?: string[];
  avoid_tags?: string[];
  food_tastes?: string[];
  style?: TravelStyle;
  food_budget_preference?: BudgetPreference;
  hotel_budget_preference?: BudgetPreference;
  must_have_hotel_area?: string;
  travel_note_style?: string;
  additional_notes?: string;
}

export type RouteTransportMode =
  | "walking"
  | "driving"
  | "bicycling"
  | "transit";

export interface RoutePointInput {
  lat: number;
  lon: number;
  label?: string;
}

export interface RoutePlanRequest {
  mode: RouteTransportMode;
  points: RoutePointInput[];
  prefer_waypoints?: boolean;
}

export interface RoutePlanLeg {
  from_index: number;
  to_index: number;
  status: string;
  distance_km: number;
  duration_minutes: number;
  path: [number, number][];
  warning?: string;
}

export interface RoutePlanResponse {
  requested_mode: RouteTransportMode | string;
  mode: RouteTransportMode | string;
  status: "ok" | "partial" | "failed" | string;
  distance_km: number;
  duration_minutes: number;
  path: [number, number][];
  legs: RoutePlanLeg[];
  warnings: string[];
}

export interface AgentTraceStep {
  agent_name: string;
  status?: "ok" | "fallback" | "warning" | string;
  input_summary: string;
  output_summary: string;
  key_decisions: string[];
}

export interface SourceEvidence {
  evidence_type: string;
  provider: string;
  provider_label?: string;
  title?: string;
  snippet?: string;
  source_url?: string;
}

export interface PointOfInterest {
  name: string;
  category: string;
  district: string;
  description: string;
  duration_hours: number;
  ticket_cost: number;
  lat: number;
  lon: number;
  tags: string[];
  best_time: string;
  opening_hours: string;
  address: string;
  source_evidence?: SourceEvidence[];
  estimated_visit_window?: string;
}

export interface FoodVenue {
  name: string;
  district: string;
  cuisine: string;
  description: string;
  average_cost: number;
  tags: string[];
  lat: number;
  lon: number;
  address: string;
}

export interface HotelVenue {
  name: string;
  district: string;
  description: string;
  price_per_night: number;
  lat: number;
  lon: number;
  address: string;
  tags: string[];
  source_evidence?: SourceEvidence[];
}

export interface MealRecommendation {
  venue_name: string;
  meal_type: "lunch" | "dinner";
  estimated_cost: number;
  reason: string;
  venue_district: string;
  cuisine: string;
  lat: number;
  lon: number;
  source_evidence?: SourceEvidence[];
  route_distance_km?: number;
  selection_tier?: string;
}

export interface TransportSegment {
  segment_type: string;
  from_label: string;
  to_label: string;
  duration_minutes: number;
  estimated_cost: number;
  description: string;
  distance_km: number;
  transport_code?: string;
  depart_time?: string;
  arrive_time?: string;
  source_url?: string;
  source_name?: string;
  queried_at?: string;
  confidence?: string;
}

export interface DailyTransportPlan {
  inbound: string;
  intra_city: string;
  walking_intensity: string;
  estimated_duration_minutes: number;
  estimated_cost: number;
  route_summary: string;
}

export interface DayPlan {
  day: number;
  theme: string;
  spots: PointOfInterest[];
  meals: MealRecommendation[];
  hotel: HotelVenue | null;
  transport: DailyTransportPlan;
  transport_segments: TransportSegment[];
  arrival_segment?: TransportSegment | null;
  departure_segment?: TransportSegment | null;
  notes: string[];
  weather_summary: string;
}

export interface BudgetLine {
  category: string;
  amount: number;
  note?: string;
}

export interface ProviderStatus {
  name: string;
  active: boolean;
  detail: string;
}

export interface CityMatch {
  input_name: string;
  confirmed_name: string;
  region?: string;
  country?: string;
  provider?: string;
}

export interface TravelNote {
  title: string;
  style_tag?: string;
  evidence_type?: string;
  provider: string;
  source_url?: string;
  summary: string;
}

export interface ValidationIssue {
  severity: string;
  category: string;
  message: string;
  day?: number | null;
  suggested_fix?: string;
}

export interface BudgetSummary {
  total_estimated: number;
  remaining_budget: number;
  is_within_budget: boolean;
  lines: BudgetLine[];
}

export interface PlanConstraints {
  preferred_areas?: string[];
  avoid_tags?: string[];
  must_include_spots?: string[];
  must_include_spots_by_day?: Record<string, string[]>;
}

export interface TripPlan {
  request: TripRequest;
  constraints?: PlanConstraints;
  days: DayPlan[];
  budget_summary: BudgetSummary;
  warnings: string[];
  final_score: number;
  was_revised: boolean;
  trace: AgentTraceStep[];
  evidence_mode_summary?: string;
  provider_statuses?: ProviderStatus[];
  search_notes?: string[];
  origin_match?: CityMatch;
  destination_match?: CityMatch;
  travel_notes?: TravelNote[];
  validation_issues?: ValidationIssue[];
  summary_markdown?: string;
}

// Animation types
export interface AnimationNode {
  day: number;
  step_index: number;
  title: string;
  kind: string;
  label: string;
  desc: string;
  lat: number;
  lon: number;
  color: string;
  frame_start: number;
  marker_text: string;
  day_color: string;
  type_color: string;
  address: string;
  district: string;
}

export interface AnimationSegment {
  day: number;
  step_index: number;
  from_title: string;
  to_title: string;
  segment_type: string;
  path: [number, number][];
  color: string;
  frame_start: number;
  frame_end: number;
  duration: number;
  cost: number;
  distance_km: number;
  desc: string;
  path_status: string;
}

export interface AnimationStep {
  day: number;
  step_index: number;
  headline: string;
  subheadline: string;
  sidebar_title: string;
  sidebar_desc: string;
  frame_start: number;
  address: string;
  weather_note?: string;
  next_transport_type?: string;
  next_transport_duration?: number;
  next_transport_cost?: number;
  next_transport_distance_km?: number;
  next_transport_desc?: string;
}

export interface AnimationBundle {
  case_id: string;
  request_summary: {
    origin: string;
    destination: string;
    days: number;
    budget: number;
    city: string;
  };
  nodes: AnimationNode[];
  segments: AnimationSegment[];
  steps: AnimationStep[];
  total_frames: number;
}

// API response types
export interface PlanResponse {
  case_id: string;
  plan: TripPlan;
  animation: AnimationBundle | null;
}

export interface SystemStatus {
  providers: ProviderStatus[];
  mode: string;
}

export interface CaseSummary {
  case_id: string;
  summary: string;
  generated_at: string;
}

export interface PersonalizationTraceItem {
  stage: string;
  agent: string;
  status: string;
  summary: string;
  details?: Record<string, unknown>;
}

export interface PersonalizationImpactReport {
  risk_level: string;
  impacted_files: string[];
  impacted_agents: string[];
  summary: string;
}

export interface PersonalizationReviewResult {
  passed: boolean;
  recommendation: string;
  llm_review_used?: boolean;
  repair_recommended?: boolean;
  issues: Array<{
    severity: string;
    category: string;
    message: string;
    suggestion: string;
  }>;
}

export interface PersonalizationValidationResult {
  success: boolean;
  message: string;
  runtime_signature_ok: boolean;
  tests_failed: string[];
  smoke_checks: string[];
}

export interface PersonalizationPatch {
  patch_id: string;
  patches: Array<{
    file_path: string;
    operation: string;
    new_snippet: string;
    diff_lines: string[];
  }>;
}

export interface PersonalizationSubRequirement {
  id: string;
  text: string;
  target_agent: string;
  target_method: string;
  generation_source: string;
  attempt_count: number;
  repair_attempts: number;
  review_passed: boolean;
  validation_success: boolean;
  runtime_signature_ok: boolean;
  blocking_issues: string[];
}

export interface PersonalizationResult {
  requirement_id: string;
  raw_requirement?: string;
  modification_type?: string;
  target_files?: string[];
  modification_patch?: PersonalizationPatch;
  impact_report?: PersonalizationImpactReport;
  review_result?: PersonalizationReviewResult;
  requires_confirmation?: boolean;
  status?: string;
  error_message?: string;
  agent_trace?: PersonalizationTraceItem[];
  sub_requirements?: PersonalizationSubRequirement[];
  attempt_count?: number;
  repair_attempts?: number;
  final_generation_source?: string;
  stage_statuses?: Record<string, string>;
  blocking_issues?: string[];
  explanation?: {
    summary?: string;
    review_recommendation?: string;
    validation?: PersonalizationValidationResult;
    validation_message?: string;
  };
}

export interface PersonalizationApplyResult {
  success?: boolean;
  apply_message?: string;
  blocking_issues?: string[];
}

// SSE streaming events
export type SSEEvent =
  | { type: "heartbeat" }
  | { type: "trace"; agent: string; msg: string; decisions: string[] }
  | {
      type: "done";
      case_id: string;
      plan: TripPlan;
      animation: AnimationBundle | null;
    }
  | { type: "error"; msg: string };
