from __future__ import annotations

from typing import Any


DEFAULT_DAY_START_MINUTES = 9 * 60
DEFAULT_DAY_END_MINUTES = 20 * 60 + 30
LUNCH_START_MINUTES = 11 * 60 + 30
LUNCH_START_MAX_MINUTES = 13 * 60 + 30
DINNER_START_MINUTES = 17 * 60
DINNER_START_MAX_MINUTES = 19 * 60
HOTEL_ANCHOR_MINUTES = 5
INTERCITY_ARRIVAL_BUFFER_MINUTES = 75
INTERCITY_DEPARTURE_BUFFER_MINUTES = 90
ROUGH_TRANSFER_BUFFER_MINUTES = 12


def parse_clock(value: str) -> int | None:
    text = (value or "").strip()
    if len(text) != 5 or ":" not in text:
        return None
    hour_text, minute_text = text.split(":", 1)
    if not hour_text.isdigit() or not minute_text.isdigit():
        return None
    hour = int(hour_text)
    minute = int(minute_text)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def format_minutes(total_minutes: int) -> str:
    normalized = max(0, total_minutes)
    return f"{normalized // 60:02d}:{normalized % 60:02d}"


def segment_arrival_minutes(segment: Any) -> int | None:
    return parse_clock(getattr(segment, "arrive_time", ""))


def segment_departure_minutes(segment: Any) -> int | None:
    return parse_clock(getattr(segment, "depart_time", ""))


def day_start_minutes(day: Any) -> int:
    arrival_minutes = segment_arrival_minutes(getattr(day, "arrival_segment", None))
    if arrival_minutes is None:
        return DEFAULT_DAY_START_MINUTES
    return max(DEFAULT_DAY_START_MINUTES, arrival_minutes + INTERCITY_ARRIVAL_BUFFER_MINUTES)


def day_end_minutes(day: Any) -> int:
    departure_minutes = segment_departure_minutes(getattr(day, "departure_segment", None))
    if departure_minutes is None:
        return DEFAULT_DAY_END_MINUTES
    return max(DEFAULT_DAY_START_MINUTES + 120, departure_minutes - INTERCITY_DEPARTURE_BUFFER_MINUTES)


def spot_time_bucket(spot: Any) -> str:
    best_time = str(getattr(spot, "best_time", "") or "").strip().lower()
    if best_time in {"morning", "afternoon", "evening", "night"}:
        return best_time
    window = str(getattr(spot, "estimated_visit_window", "") or "").strip()
    start_minutes = parse_clock(window.split("-", 1)[0]) if "-" in window else None
    if start_minutes is None:
        return "flexible"
    if start_minutes < 12 * 60:
        return "morning"
    if start_minutes < 17 * 60:
        return "afternoon"
    if start_minutes < 20 * 60:
        return "evening"
    return "night"


def node_duration_minutes(node: dict) -> int:
    if node.get("duration_hint_minutes"):
        return int(node["duration_hint_minutes"])
    if node["kind"] == "hotel":
        return HOTEL_ANCHOR_MINUTES
    if node["kind"] == "lunch":
        return 60
    if node["kind"] == "dinner":
        return 80
    desc = str(node.get("desc", ""))
    if "|" in desc:
        category = desc.split("|", 1)[0].strip()
        if category in {"博物馆", "美术馆"}:
            return 120
        if category in {"公园", "景区", "夜游", "旅游景点"}:
            return 90
        if category in {"老街"}:
            return 100
    return 100


def build_transport_nodes(
    hotel: Any,
    spots: list[Any],
    meals: list[Any],
    arrival_segment: Any | None = None,
    departure_segment: Any | None = None,
) -> list[dict]:
    sequence = _build_visit_sequence(spots, meals, arrival_segment, departure_segment)
    nodes: list[dict] = []
    if hotel:
        nodes.append({"label": hotel.name, "lat": hotel.lat, "lon": hotel.lon, "kind": "hotel"})
    for item in sequence:
        if item["kind"] == "spot":
            spot = item["spot"]
            nodes.append({"label": spot.name, "lat": spot.lat, "lon": spot.lon, "kind": "spot"})
            continue
        meal = item["meal"]
        meal_label = "午餐" if meal.meal_type == "lunch" else "晚餐"
        nodes.append({"label": f"{meal_label} · {meal.venue_name}", "lat": meal.lat, "lon": meal.lon, "kind": meal.meal_type})
    return nodes


def build_day_timeline(day: Any) -> list[dict]:
    sequence = _build_visit_sequence(
        getattr(day, "spots", []),
        getattr(day, "meals", []),
        getattr(day, "arrival_segment", None),
        getattr(day, "departure_segment", None),
    )
    timeline: list[dict] = []
    if getattr(day, "hotel", None):
        hotel = day.hotel
        timeline.append(
            {
                "name": hotel.name,
                "kind": "hotel",
                "slot": "酒店",
                "desc": f"{hotel.district} | {hotel.price_per_night:.0f} 元/晚",
                "lat": hotel.lat,
                "lon": hotel.lon,
                "color": "#c2410c",
                "address": hotel.address or hotel.description,
                "district": hotel.district,
                "duration_hint_minutes": HOTEL_ANCHOR_MINUTES,
            }
        )

    spot_order = 1
    for item in sequence:
        if item["kind"] == "spot":
            spot = item["spot"]
            timeline.append(
                {
                    "name": spot.name,
                    "kind": "spot",
                    "slot": f"景点{spot_order}",
                    "desc": f"{spot.category} | {spot.estimated_visit_window or spot.best_time}",
                    "lat": spot.lat,
                    "lon": spot.lon,
                    "color": "#2563eb",
                    "address": spot.address or spot.description,
                    "district": spot.district,
                    "duration_hint_minutes": round(spot.duration_hours * 60),
                }
            )
            spot_order += 1
            continue

        meal = item["meal"]
        timeline.append(
            {
                "name": meal.venue_name,
                "kind": meal.meal_type,
                "slot": "午餐" if meal.meal_type == "lunch" else "晚餐",
                "desc": f"{meal.cuisine} | {meal.estimated_cost:.0f} 元",
                "lat": meal.lat,
                "lon": meal.lon,
                "color": "#16a34a" if meal.meal_type == "lunch" else "#dc2626",
                "address": meal.source_evidence[0].snippet if meal.source_evidence else "",
                "district": meal.venue_district,
                "duration_hint_minutes": 60 if meal.meal_type == "lunch" else 80,
            }
        )
    return [node for node in timeline if node["lat"] and node["lon"]]


def build_scheduled_day_timeline(day: Any) -> list[dict]:
    timeline = build_day_timeline(day)
    if not timeline:
        return []
    scheduled: list[dict] = []
    current_minutes = day_start_minutes(day)
    end_limit = day_end_minutes(day)

    for index, node in enumerate(timeline):
        if index > 0:
            transport = day.transport_segments[index - 1] if index - 1 < len(day.transport_segments) else None
            current_minutes += transport.duration_minutes if transport else ROUGH_TRANSFER_BUFFER_MINUTES

        if node["kind"] == "lunch":
            current_minutes = max(current_minutes, LUNCH_START_MINUTES)
        elif node["kind"] == "dinner":
            current_minutes = max(current_minutes, DINNER_START_MINUTES)

        duration_minutes = node_duration_minutes(node)
        start_minutes = current_minutes
        end_minutes = start_minutes + duration_minutes
        enriched = dict(node)
        enriched["start_time"] = format_minutes(start_minutes)
        enriched["end_time"] = format_minutes(end_minutes)
        enriched["duration_minutes"] = duration_minutes
        enriched["time_window_ok"] = _node_time_window_ok(node["kind"], start_minutes)
        enriched["day_end_buffer_ok"] = end_minutes <= end_limit
        scheduled.append(enriched)
        current_minutes = end_minutes
    return scheduled


def _build_visit_sequence(
    spots: list[Any],
    meals: list[Any],
    arrival_segment: Any | None,
    departure_segment: Any | None,
) -> list[dict]:
    lunch = next((meal for meal in meals if getattr(meal, "meal_type", "") == "lunch"), None)
    dinner = next((meal for meal in meals if getattr(meal, "meal_type", "") == "dinner"), None)
    ordered_spots = _arrange_spots_for_visit_windows(spots, arrival_segment, departure_segment)
    pre_lunch, pre_dinner, post_dinner = _split_spots_by_meal_windows(ordered_spots, arrival_segment, departure_segment)
    sequence: list[dict] = []
    for spot in pre_lunch:
        sequence.append({"kind": "spot", "spot": spot})
    if lunch:
        sequence.append({"kind": "lunch", "meal": lunch})
    for spot in pre_dinner:
        sequence.append({"kind": "spot", "spot": spot})
    if dinner:
        sequence.append({"kind": "dinner", "meal": dinner})
    for spot in post_dinner:
        sequence.append({"kind": "spot", "spot": spot})
    return sequence


def _arrange_spots_for_visit_windows(
    spots: list[Any],
    arrival_segment: Any | None,
    departure_segment: Any | None,
) -> list[Any]:
    late_arrival = False
    arrival_minutes = segment_arrival_minutes(arrival_segment)
    if arrival_minutes is not None:
        late_arrival = arrival_minutes + INTERCITY_ARRIVAL_BUFFER_MINUTES >= LUNCH_START_MINUTES
    early_departure = False
    departure_minutes = segment_departure_minutes(departure_segment)
    if departure_minutes is not None:
        early_departure = departure_minutes - INTERCITY_DEPARTURE_BUFFER_MINUTES <= DINNER_START_MINUTES

    bucket_priority = {
        "default": {"morning": 0, "flexible": 1, "afternoon": 2, "evening": 3, "night": 4},
        "late": {"afternoon": 0, "flexible": 1, "evening": 2, "night": 3, "morning": 4},
        "early": {"morning": 0, "flexible": 1, "afternoon": 2, "evening": 3, "night": 4},
    }
    mode = "late" if late_arrival else "early" if early_departure else "default"
    ranked = sorted(
        enumerate(spots),
        key=lambda item: (
            bucket_priority[mode].get(spot_time_bucket(item[1]), 5),
            item[0],
        ),
    )
    return [spot for _, spot in ranked]


def _split_spots_by_meal_windows(
    spots: list[Any],
    arrival_segment: Any | None,
    departure_segment: Any | None,
) -> tuple[list[Any], list[Any], list[Any]]:
    if not spots:
        return [], [], []

    pre_lunch_count = _fit_spots_before_window(
        spots,
        max(DEFAULT_DAY_START_MINUTES, (segment_arrival_minutes(arrival_segment) or DEFAULT_DAY_START_MINUTES) + (INTERCITY_ARRIVAL_BUFFER_MINUTES if arrival_segment else 0)) + HOTEL_ANCHOR_MINUTES,
        LUNCH_START_MAX_MINUTES - 15,
    )
    pre_lunch = spots[:pre_lunch_count]
    remaining = spots[pre_lunch_count:]

    pre_dinner_count = _fit_spots_before_window(
        [spot for spot in remaining if spot_time_bucket(spot) not in {"evening", "night"}],
        max(LUNCH_START_MINUTES, max(DEFAULT_DAY_START_MINUTES, (segment_arrival_minutes(arrival_segment) or DEFAULT_DAY_START_MINUTES) + (INTERCITY_ARRIVAL_BUFFER_MINUTES if arrival_segment else 0))) + 60,
        min(DINNER_START_MAX_MINUTES - 20, (segment_departure_minutes(departure_segment) - INTERCITY_DEPARTURE_BUFFER_MINUTES - 20) if departure_segment and segment_departure_minutes(departure_segment) is not None else DINNER_START_MAX_MINUTES - 20),
    )
    pre_dinner: list[Any] = []
    used_keys: set[int] = set()
    for spot in remaining:
        if len(pre_dinner) >= pre_dinner_count:
            break
        if spot_time_bucket(spot) in {"evening", "night"}:
            continue
        pre_dinner.append(spot)
        used_keys.add(id(spot))

    post_dinner = [spot for spot in remaining if id(spot) not in used_keys]
    return pre_lunch, pre_dinner, post_dinner


def _fit_spots_before_window(spots: list[Any], start_minutes: int, latest_start_minutes: int) -> int:
    current_minutes = start_minutes
    count = 0
    for spot in spots:
        projected_end = current_minutes + (ROUGH_TRANSFER_BUFFER_MINUTES if count > 0 else 0) + round(float(getattr(spot, "duration_hours", 1.5)) * 60)
        if projected_end > latest_start_minutes:
            break
        current_minutes = projected_end
        count += 1
    return count


def _node_time_window_ok(kind: str, start_minutes: int) -> bool:
    if kind == "lunch":
        return LUNCH_START_MINUTES <= start_minutes <= LUNCH_START_MAX_MINUTES
    if kind == "dinner":
        return DINNER_START_MINUTES <= start_minutes <= DINNER_START_MAX_MINUTES
    return True
