from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import (
    AgentTraceStep,
    AnimationBundle,
    AnimationNode,
    AnimationSegment,
    AnimationStep,
    BudgetLine,
    BudgetSummary,
    CityMatch,
    CityProfile,
    DailyTransportPlan,
    DayPlan,
    EvidenceItem,
    FoodVenue,
    HotelVenue,
    MealRecommendation,
    PointOfInterest,
    ProviderStatus,
    RoutePoint,
    TransportSegment,
    TravelConstraints,
    TravelNote,
    TripPlan,
    TripRequest,
    ValidationIssue,
)


OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"
LATEST_CASE_PATH = OUTPUTS_DIR / "latest_case.json"
PLAYER_TEMPLATE_PATH = Path(__file__).resolve().parent / "web" / "player_template.html"
PLAYER_VERSION = 4
BUNDLE_VERSION = 4


@dataclass
class SavedCaseRecord:
    case_id: str
    plan_path: str
    animation_path: str
    summary: str
    generated_at: str
    player_path: str = ""
    bundle_version: int = BUNDLE_VERSION
    player_version: int = PLAYER_VERSION


def build_case_id(origin: str, destination: str, days: int, budget: float) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    origin_slug = _slugify(origin)
    destination_slug = _slugify(destination)
    return f"{origin_slug}-{destination_slug}-{days}d-{int(round(budget))}-{timestamp}"


def save_case(plan: TripPlan, animation: AnimationBundle, tencent_js_key: str | None = None) -> SavedCaseRecord:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    case_dir = OUTPUTS_DIR / animation.case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    plan_path = case_dir / "plan.json"
    animation_path = case_dir / "animation.json"
    player_path = case_dir / "player.html"

    animation.bundle_version = BUNDLE_VERSION
    plan_path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    animation_path.write_text(json.dumps(animation.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    player_path.write_text(render_player_html(animation, tencent_js_key or ""), encoding="utf-8")

    record = SavedCaseRecord(
        case_id=animation.case_id,
        plan_path=str(plan_path),
        animation_path=str(animation_path),
        player_path=str(player_path),
        summary=_build_summary(plan),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        bundle_version=animation.bundle_version,
        player_version=PLAYER_VERSION,
    )
    LATEST_CASE_PATH.write_text(json.dumps(record.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def list_saved_cases() -> list[SavedCaseRecord]:
    if not OUTPUTS_DIR.exists():
        return []
    records: list[SavedCaseRecord] = []
    for plan_path in OUTPUTS_DIR.glob("*/plan.json"):
        case_dir = plan_path.parent
        animation_path = case_dir / "animation.json"
        player_path = case_dir / "player.html"
        if not animation_path.exists():
            continue
        latest_generated = datetime.fromtimestamp(plan_path.stat().st_mtime).isoformat(timespec="seconds")
        summary = case_dir.name
        try:
            plan_dict = json.loads(plan_path.read_text(encoding="utf-8"))
            request = plan_dict.get("request", {})
            destination = request.get("destination", case_dir.name)
            origin = request.get("origin", "")
            days = request.get("days", "-")
            budget = request.get("budget", "-")
            summary = f"{origin} -> {destination} | {days} 天 | {budget} 元"
        except Exception:
            pass
        records.append(
            SavedCaseRecord(
                case_id=case_dir.name,
                plan_path=str(plan_path),
                animation_path=str(animation_path),
                player_path=str(player_path) if player_path.exists() else "",
                summary=summary,
                generated_at=latest_generated,
                bundle_version=_read_bundle_version(animation_path),
                player_version=_read_player_version(player_path),
            )
        )
    records.sort(key=lambda item: item.generated_at, reverse=True)
    return records


def load_latest_case() -> tuple[TripPlan, AnimationBundle, SavedCaseRecord] | None:
    if not LATEST_CASE_PATH.exists():
        return None
    record_dict = json.loads(LATEST_CASE_PATH.read_text(encoding="utf-8"))
    record = SavedCaseRecord(**_normalize_saved_case_record(record_dict))
    return load_case_record(record)


def load_case_record(record: SavedCaseRecord) -> tuple[TripPlan, AnimationBundle, SavedCaseRecord]:
    plan_dict = json.loads(Path(record.plan_path).read_text(encoding="utf-8"))
    animation_dict = json.loads(Path(record.animation_path).read_text(encoding="utf-8"))
    plan_changed = _repair_plan_payload_paths(plan_dict)
    animation_changed = _repair_animation_payload_paths(animation_dict)
    if plan_changed:
        Path(record.plan_path).write_text(json.dumps(plan_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    if animation_changed:
        Path(record.animation_path).write_text(json.dumps(animation_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    normalized_record = SavedCaseRecord(**_normalize_saved_case_record(record.__dict__))
    if not normalized_record.bundle_version:
        normalized_record.bundle_version = int(animation_dict.get("bundle_version", 1) or 1)
    if not normalized_record.player_version:
        normalized_record.player_version = _read_player_version(Path(normalized_record.player_path))
    return deserialize_trip_plan(plan_dict), deserialize_animation_bundle(animation_dict), normalized_record


def load_player_html(record: SavedCaseRecord) -> str:
    if not record.player_path:
        return ""
    player_path = Path(record.player_path)
    if not player_path.exists():
        return ""
    return player_path.read_text(encoding="utf-8")


def render_player_html(animation: AnimationBundle, tencent_js_key: str) -> str:
    if not PLAYER_TEMPLATE_PATH.exists():
        raise RuntimeError(f"播放器模板不存在：{PLAYER_TEMPLATE_PATH}")
    template = PLAYER_TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template.replace("__PLAYER_TITLE__", f"{animation.request_summary.get('origin', '')} -> {animation.request_summary.get('destination', '')}")
        .replace("__TENCENT_MAP_JS_KEY__", tencent_js_key)
        .replace("__ANIMATION_BUNDLE__", json.dumps(animation.to_dict(), ensure_ascii=False))
        .replace("__PLAYER_VERSION__", str(PLAYER_VERSION))
    )


def rebuild_case_assets(record: SavedCaseRecord, plan: TripPlan, animation: AnimationBundle, tencent_js_key: str | None = None) -> SavedCaseRecord:
    rebuilt_animation = AnimationBundle(
        case_id=record.case_id,
        request_summary=animation.request_summary,
        nodes=animation.nodes,
        segments=animation.segments,
        steps=animation.steps,
        total_frames=animation.total_frames,
        generated_at=animation.generated_at,
        bundle_version=BUNDLE_VERSION,
        default_day_mode=animation.default_day_mode,
        default_play_mode=animation.default_play_mode,
    )
    case_dir = Path(record.plan_path).parent
    animation_path = case_dir / "animation.json"
    player_path = case_dir / "player.html"
    animation_path.write_text(json.dumps(rebuilt_animation.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    player_path.write_text(render_player_html(rebuilt_animation, tencent_js_key or ""), encoding="utf-8")
    refreshed_record = SavedCaseRecord(
        case_id=record.case_id,
        plan_path=str(case_dir / "plan.json"),
        animation_path=str(animation_path),
        player_path=str(player_path),
        summary=record.summary or _build_summary(plan),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        bundle_version=BUNDLE_VERSION,
        player_version=PLAYER_VERSION,
    )
    LATEST_CASE_PATH.write_text(json.dumps(refreshed_record.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
    return refreshed_record


def case_requires_rebuild(record: SavedCaseRecord, animation: AnimationBundle | None = None) -> bool:
    record_bundle_version = getattr(record, "bundle_version", 0) or 0
    record_player_version = getattr(record, "player_version", 0) or 0
    bundle_version = animation.bundle_version if animation is not None else record_bundle_version
    return bundle_version < BUNDLE_VERSION or record_bundle_version < BUNDLE_VERSION or record_player_version < PLAYER_VERSION


def deserialize_trip_plan(data: dict) -> TripPlan:
    request = TripRequest(**data["request"])
    constraints = TravelConstraints(**data["constraints"])
    city_profile = _build_city_profile(data["city_profile"])
    origin_match = CityMatch(**data["origin_match"])
    destination_match = CityMatch(**data["destination_match"])
    day_plans = [_build_day_plan(item) for item in data["day_plans"]]
    budget_summary = BudgetSummary(
        total_estimated=data["budget_summary"]["total_estimated"],
        remaining_budget=data["budget_summary"]["remaining_budget"],
        is_within_budget=data["budget_summary"]["is_within_budget"],
        lines=[BudgetLine(**line) for line in data["budget_summary"]["lines"]],
    )
    validation_issues = [ValidationIssue(**issue) for issue in data["validation_issues"]]
    route_points = [RoutePoint(**point) for point in data["route_points"]]
    trace = [AgentTraceStep(**step) for step in data["trace"]]
    provider_statuses = [ProviderStatus(**status) for status in data["provider_statuses"]]
    travel_notes = [TravelNote(**note) for note in data.get("travel_notes", [])]
    return TripPlan(
        request=request,
        constraints=constraints,
        city_profile=city_profile,
        origin_match=origin_match,
        destination_match=destination_match,
        day_plans=day_plans,
        budget_summary=budget_summary,
        validation_issues=validation_issues,
        summary_markdown=data["summary_markdown"],
        route_points=route_points,
        warnings=data.get("warnings", []),
        trace=trace,
        final_score=data["final_score"],
        mode=data["mode"],
        provider_statuses=provider_statuses,
        travel_notes=travel_notes,
        evidence_mode_summary=data.get("evidence_mode_summary", ""),
        was_revised=data.get("was_revised", False),
        search_notes=data.get("search_notes", []),
    )


def deserialize_animation_bundle(data: dict) -> AnimationBundle:
    return AnimationBundle(
        case_id=data["case_id"],
        request_summary=data["request_summary"],
        nodes=[AnimationNode(**node) for node in data["nodes"]],
        segments=[AnimationSegment(**segment) for segment in data["segments"]],
        steps=[AnimationStep(**step) for step in data["steps"]],
        total_frames=data["total_frames"],
        bundle_version=data.get("bundle_version", 1),
        default_day_mode=data.get("default_day_mode", "全部"),
        default_play_mode=data.get("default_play_mode", "静态全览"),
        generated_at=data.get("generated_at", ""),
    )


def _build_city_profile(data: dict) -> CityProfile:
    return CityProfile(
        city=data["city"],
        aliases=data.get("aliases", []),
        intro=data["intro"],
        local_transport_tip=data["local_transport_tip"],
        daily_local_transport_cost=data["daily_local_transport_cost"],
        accommodation_budget=data["accommodation_budget"],
        intercity_transport=data["intercity_transport"],
        recommended_seasons=data.get("recommended_seasons", []),
        pois=[_build_poi(item) for item in data.get("pois", [])],
        foods=[_build_food(item) for item in data.get("foods", [])],
        hotels=[_build_hotel(item) for item in data.get("hotels", [])],
    )


def _build_day_plan(data: dict) -> DayPlan:
    return DayPlan(
        day=data["day"],
        theme=data["theme"],
        spots=[_build_poi(item) for item in data.get("spots", [])],
        meals=[_build_meal(item) for item in data.get("meals", [])],
        hotel=_build_hotel(data["hotel"]) if data.get("hotel") else None,
        transport=DailyTransportPlan(**data["transport"]),
        transport_segments=[TransportSegment(**segment) for segment in data.get("transport_segments", [])],
        notes=data.get("notes", []),
        arrival_segment=TransportSegment(**data["arrival_segment"]) if data.get("arrival_segment") else None,
        departure_segment=TransportSegment(**data["departure_segment"]) if data.get("departure_segment") else None,
        weather_summary=data.get("weather_summary", ""),
    )


def _build_poi(data: dict) -> PointOfInterest:
    payload = dict(data)
    payload["source_evidence"] = [_build_evidence(item) for item in payload.get("source_evidence", [])]
    return PointOfInterest(**payload)


def _build_food(data: dict) -> FoodVenue:
    payload = dict(data)
    payload["source_evidence"] = [_build_evidence(item) for item in payload.get("source_evidence", [])]
    return FoodVenue(**payload)


def _build_hotel(data: dict) -> HotelVenue:
    payload = dict(data)
    payload["source_evidence"] = [_build_evidence(item) for item in payload.get("source_evidence", [])]
    return HotelVenue(**payload)


def _build_meal(data: dict) -> MealRecommendation:
    payload = dict(data)
    payload["source_evidence"] = [_build_evidence(item) for item in payload.get("source_evidence", [])]
    return MealRecommendation(**payload)


def _build_evidence(data: dict) -> EvidenceItem:
    return EvidenceItem(**data)


def _repair_plan_payload_paths(plan_dict: dict) -> bool:
    changed = False
    for day in plan_dict.get("day_plans", []):
        for key in ("transport_segments",):
            for segment in day.get(key, []) or []:
                sanitized, path_changed = _sanitize_serialized_path(segment.get("path", []))
                if path_changed:
                    segment["path"] = sanitized
                    changed = True
        for key in ("arrival_segment", "departure_segment"):
            segment = day.get(key)
            if not isinstance(segment, dict):
                continue
            sanitized, path_changed = _sanitize_serialized_path(segment.get("path", []))
            if path_changed:
                segment["path"] = sanitized
                changed = True
    return changed


def _repair_animation_payload_paths(animation_dict: dict) -> bool:
    changed = False
    node_lookup: dict[tuple[int, int], tuple[float, float]] = {}
    for node in animation_dict.get("nodes", []) or []:
        lon_lat, point_changed = _normalize_serialized_point([node.get("lon"), node.get("lat")])
        if lon_lat is None:
            continue
        lon, lat = lon_lat
        node_lookup[(int(node.get("day", 0)), int(node.get("step_index", 0)))] = (lon, lat)
        if point_changed:
            node["lon"] = lon
            node["lat"] = lat
            changed = True

    for segment in animation_dict.get("segments", []) or []:
        sanitized, path_changed = _sanitize_serialized_path(segment.get("path", []))
        if len(sanitized) < 2:
            day = int(segment.get("day", 0))
            step_index = int(segment.get("step_index", 0))
            start = node_lookup.get((day, step_index))
            end = node_lookup.get((day, step_index + 1))
            if start and end:
                sanitized = [[start[0], start[1]], [end[0], end[1]]]
                path_changed = True
        if path_changed:
            segment["path"] = sanitized
            changed = True
    return changed


def _sanitize_serialized_path(path: list) -> tuple[list[list[float]], bool]:
    if not isinstance(path, list):
        return [], True
    changed = False
    cleaned: list[list[float]] = []
    prev: tuple[float, float] | None = None
    for raw in path:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            changed = True
            continue
        lon_lat, point_changed = _normalize_serialized_point(raw, prev)
        if lon_lat is None:
            changed = True
            continue
        if point_changed:
            changed = True
        lon, lat = lon_lat
        if prev is not None and math.isclose(prev[0], lon) and math.isclose(prev[1], lat):
            changed = True
            continue
        cleaned.append([lon, lat])
        prev = (lon, lat)
    if len(cleaned) != len(path):
        changed = True
    return cleaned, changed


def _normalize_serialized_point(
    raw_point: list | tuple,
    prev: tuple[float, float] | None = None,
) -> tuple[tuple[float, float] | None, bool]:
    try:
        first = float(raw_point[0])
        second = float(raw_point[1])
    except Exception:
        return None, True

    if _is_valid_lon_lat(first, second):
        direct = (first, second)
        if prev is None or _is_locally_continuous(prev, direct):
            return direct, False

    # Fallback for legacy bad decode data:
    # Tencent deltas were left undecoded (e.g. [3, -150]) and should be applied
    # as 1e6 increments from previous valid point.
    if (
        prev is not None
        and abs(first) <= 1_000_000
        and abs(second) <= 1_000_000
    ):
        candidate = (prev[0] + first / 1_000_000, prev[1] + second / 1_000_000)
        if _is_valid_lon_lat(candidate[0], candidate[1]) and _is_locally_continuous(prev, candidate):
            return candidate, True

    # Fallback: value order accidentally swapped as [lat, lon].
    if _is_valid_lon_lat(second, first):
        swapped = (second, first)
        if prev is None or _is_locally_continuous(prev, swapped):
            return swapped, True

    return None, True


def _is_valid_lon_lat(lon: float, lat: float) -> bool:
    return (
        math.isfinite(lon)
        and math.isfinite(lat)
        and -180.0 <= lon <= 180.0
        and -90.0 <= lat <= 90.0
    )


def _is_locally_continuous(prev: tuple[float, float], nxt: tuple[float, float]) -> bool:
    return abs(prev[0] - nxt[0]) <= 1.5 and abs(prev[1] - nxt[1]) <= 1.5


def _slugify(value: str) -> str:
    compact = value.strip().replace(" ", "-")
    compact = re.sub(r"[\\\\/:*?\"<>|]+", "-", compact)
    compact = re.sub(r"-{2,}", "-", compact)
    return compact.strip("-") or "trip"


def _build_summary(plan: TripPlan) -> str:
    return f"{plan.request.origin} -> {plan.request.destination} | {plan.request.days} 天 | {plan.request.budget:.0f} 元"


def _normalize_saved_case_record(record_dict: dict) -> dict:
    normalized = dict(record_dict)
    normalized.setdefault("player_path", "")
    normalized.setdefault("bundle_version", 0)
    normalized.setdefault("player_version", 0)
    return normalized


def _read_bundle_version(animation_path: Path) -> int:
    try:
        payload = json.loads(animation_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return int(payload.get("bundle_version", 0) or 0)


def _read_player_version(player_path: Path) -> int:
    if not player_path.exists():
        return 0
    try:
        content = player_path.read_text(encoding="utf-8")
    except Exception:
        return 0
    marker = "data-player-version=\""
    if marker not in content:
        return 0
    tail = content.split(marker, 1)[1]
    value = tail.split("\"", 1)[0]
    try:
        return int(value)
    except Exception:
        return 0
