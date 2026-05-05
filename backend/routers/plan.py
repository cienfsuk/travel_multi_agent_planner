from __future__ import annotations

import asyncio
import json
import queue
import re
import threading
from dataclasses import asdict
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from travel_multi_agent_planner.models import TripRequest
from travel_multi_agent_planner.orchestrator import TravelPlanningOrchestrator
from travel_multi_agent_planner.persistence import build_case_id, save_case

router = APIRouter(prefix="/api/plan", tags=["plan"])


class PlanRequest(BaseModel):
    destination: str
    days: int
    budget: float
    origin: str = "上海"
    departure_date: str = ""
    traveler_count: int = 1
    interests: list[str] = ["culture", "food", "nature"]
    preferred_areas: list[str] = []
    avoid_tags: list[str] = []
    food_tastes: list[str] = []
    style: str = "balanced"
    food_budget_preference: str = "balanced"
    hotel_budget_preference: str = "balanced"
    must_have_hotel_area: str = ""
    travel_note_style: str = "小红书风格"
    additional_notes: str = ""


@router.post("/stream")
async def stream_plan(body: PlanRequest) -> StreamingResponse:
    trip_request = TripRequest(
        destination=body.destination,
        days=body.days,
        budget=body.budget,
        origin=body.origin,
        departure_date=body.departure_date,
        traveler_count=body.traveler_count,
        interests=body.interests,
        preferred_areas=body.preferred_areas,
        avoid_tags=body.avoid_tags,
        food_tastes=body.food_tastes,
        style=body.style,  # type: ignore[arg-type]
        food_budget_preference=body.food_budget_preference,  # type: ignore[arg-type]
        hotel_budget_preference=body.hotel_budget_preference,  # type: ignore[arg-type]
        must_have_hotel_area=body.must_have_hotel_area,
        travel_note_style=body.travel_note_style,
        additional_notes=body.additional_notes,
    )

    result_queue: queue.Queue = queue.Queue()

    def _on_trace(step) -> None:
        result_queue.put(("trace", step, None, None))

    def _run() -> None:
        try:
            orch = TravelPlanningOrchestrator()
            plan = orch.create_plan(trip_request, on_trace=_on_trace)
            _apply_personalization_plan_overrides(plan, trip_request.additional_notes)

            from travel_multi_agent_planner.app import _build_animation_bundle  # type: ignore[attr-defined]
            from travel_multi_agent_planner.config import AppConfig

            case_id = build_case_id(
                trip_request.origin,
                trip_request.destination,
                trip_request.days,
                trip_request.budget,
            )
            try:
                animation = _build_animation_bundle(plan, case_id)
                tencent_js_key = AppConfig.from_env().tencent_map_js_key
                save_case(plan, animation, tencent_js_key)
            except Exception:
                animation = None

            result_queue.put(("done", plan, animation, case_id))
        except Exception as exc:
            result_queue.put(("error", str(exc), None, None))

    loop = asyncio.get_event_loop()

    async def generate() -> AsyncIterator[str]:
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        while True:
            try:
                item = await loop.run_in_executor(None, lambda: result_queue.get(timeout=2.0))
            except Exception:
                yield _sse({"type": "heartbeat"})
                continue

            event_type = item[0]
            if event_type == "error":
                yield _sse({"type": "error", "msg": item[1]})
                break

            if event_type == "trace":
                step = item[1]
                yield _sse(
                    {
                        "type": "trace",
                        "agent": step.agent_name,
                        "msg": step.output_summary,
                        "decisions": step.key_decisions,
                    }
                )
                continue

            _, plan, animation, case_id = item
            plan_dict = _plan_to_dict(plan)
            animation_dict = asdict(animation) if animation is not None else None
            yield _sse(
                {
                    "type": "done",
                    "case_id": case_id,
                    "plan": plan_dict,
                    "animation": animation_dict,
                }
            )
            break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _plan_to_dict(plan) -> dict:
    try:
        data = asdict(plan)
        if "day_plans" in data:
            data["days"] = data.pop("day_plans")
        return data
    except Exception:
        return {}


def _apply_personalization_plan_overrides(plan, additional_notes: str) -> None:
    if not additional_notes:
        return

    meal_overrides: list[str] = []
    transport_overrides: list[str] = []
    for day in getattr(plan, "day_plans", []) or []:
        day_number = int(getattr(day, "day", 0) or 0)
        notes = list(getattr(day, "notes", []) or [])

        # Meal personalization is already resolved in the orchestrator, including
        # unmatched-candidate notes. This backend pass only keeps transport
        # compatibility overrides to avoid duplicate or misleading meal messages.

        min_departure_minutes = _extract_day_transport_min_departure(additional_notes, day_number)
        if min_departure_minutes is not None:
            if _apply_segment_departure_floor(getattr(day, "arrival_segment", None), min_departure_minutes):
                note = f"【个性化】已将第{day_number}天出发时间调整为{_format_minutes(min_departure_minutes)}以后。"
                if note not in notes:
                    notes.append(note)
                transport_overrides.append(
                    f"第{day_number}天入城交通：{_format_minutes(min_departure_minutes)} 以后出发"
                )
            if _apply_segment_departure_floor(getattr(day, "departure_segment", None), min_departure_minutes):
                note = f"【个性化】已将第{day_number}天返程时间调整为{_format_minutes(min_departure_minutes)}以后。"
                if note not in notes:
                    notes.append(note)
                transport_overrides.append(
                    f"第{day_number}天返程交通：{_format_minutes(min_departure_minutes)} 以后出发"
                )

        day.notes = notes

    summary_markdown = str(getattr(plan, "summary_markdown", "") or "")
    if meal_overrides and "## 个性化餐饮落地" not in summary_markdown:
        summary_markdown += "\n".join(["", "## 个性化餐饮落地", *[f"- {item}" for item in meal_overrides]])
    if transport_overrides and "## 个性化交通落地" not in summary_markdown:
        summary_markdown += "\n".join(["", "## 个性化交通落地", *[f"- {item}" for item in transport_overrides]])
    plan.summary_markdown = summary_markdown


def _extract_day_transport_min_departure(notes: str, day: int) -> int | None:
    if not notes or day <= 0:
        return None

    day_tokens = {
        1: "第一天",
        2: "第二天",
        3: "第三天",
        4: "第四天",
        5: "第五天",
        6: "第六天",
        7: "第七天",
    }
    day_token = day_tokens.get(day, f"第{day}天")
    specific_phrases = [
        f"{day_token}出发别太早",
        f"{day_token}出发不要太早",
        f"{day_token}别太早出发",
        f"{day_token}不要太早出发",
        f"{day_token}不要赶早车",
        f"{day_token}不要赶车太早",
        f"{day_token}晚点出发",
        f"{day_token}晚些出发",
    ]
    if any(phrase in notes for phrase in specific_phrases):
        return 9 * 60
    if day == 1 and any(
        token in notes
        for token in ["出发别太早", "出发不要太早", "别太早出发", "不要太早出发", "不要赶早车", "不要赶车太早", "晚点出发", "晚些出发"]
    ):
        return 9 * 60
    return None


def _apply_segment_departure_floor(segment, min_departure_minutes: int) -> bool:
    if segment is None:
        return False
    depart_minutes = _time_to_minutes(getattr(segment, "depart_time", ""))
    if depart_minutes is None or depart_minutes >= min_departure_minutes:
        return False

    original_depart = getattr(segment, "depart_time", "") or "待定"
    original_arrive = getattr(segment, "arrive_time", "") or "待定"
    duration_minutes = max(60, int(getattr(segment, "duration_minutes", 90) or 90))
    segment.depart_time = _format_minutes(min_departure_minutes)
    segment.arrive_time = _format_minutes(min_departure_minutes + duration_minutes)
    segment.confidence = "personalized"
    segment.description = (
        f"【个性化】已避开过早车次，建议改乘 {segment.depart_time} 以后出发的班次。"
        f"原始查询参考为 {original_depart} -> {original_arrive}。"
    )
    return True


def _meal_object_matches_keywords(meal, keywords: list[str]) -> bool:
    evidence_titles = " ".join(
        str(getattr(item, "title", "") or "")
        for item in list(getattr(meal, "source_evidence", []) or [])
    )
    text = " ".join(
        [
            str(getattr(meal, "venue_name", "") or ""),
            str(getattr(meal, "cuisine", "") or ""),
            str(getattr(meal, "reason", "") or ""),
            evidence_titles,
        ]
    ).lower()
    return any(keyword.lower() in text for keyword in keywords)


def _time_to_minutes(value: str) -> int | None:
    if not value or ":" not in value:
        return None
    try:
        hour_text, minute_text = value.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)
    except ValueError:
        return None


def _format_minutes(minutes: int) -> str:
    hour = max(0, int(minutes) // 60) % 24
    minute = max(0, int(minutes) % 60)
    return f"{hour:02d}:{minute:02d}"


def _extract_day_meal_preferences(notes: str, day: int) -> list[dict]:
    if not notes or day <= 0:
        return []

    day_tokens = {
        1: "第一天",
        2: "第二天",
        3: "第三天",
        4: "第四天",
        5: "第五天",
        6: "第六天",
        7: "第七天",
    }
    day_token = day_tokens.get(day, f"第{day}天")

    preferences: list[dict] = []
    candidates = [
        ("dinner", ["火锅", "hotpot", "huoguo"], ["晚饭吃火锅", "晚餐吃火锅", "晚上想吃火锅", "晚上吃火锅"]),
        ("dinner", ["烧烤", "烤肉", "bbq", "barbecue", "grill"], ["晚饭吃烧烤", "晚餐吃烧烤", "晚上想吃烧烤", "晚上吃烧烤"]),
        ("lunch", ["火锅", "hotpot", "huoguo"], ["午饭吃火锅", "午餐吃火锅", "中午想吃火锅", "中午吃火锅"]),
        ("lunch", ["烧烤", "烤肉", "bbq", "barbecue", "grill"], ["午饭吃烧烤", "午餐吃烧烤", "中午想吃烧烤", "中午吃烧烤"]),
    ]
    for meal_type, keywords, suffixes in candidates:
        for suffix in suffixes:
            phrase = f"{day_token}{suffix}"
            if phrase in notes:
                preferences.append({"meal_type": meal_type, "label": phrase, "keywords": keywords})
                break
    return preferences


def _extract_day_meal_preferences_v2(notes: str, day: int) -> list[dict]:
    if not notes or day <= 0:
        return []
    preferences: list[dict] = []
    seen: set[tuple[str, str]] = set()
    clauses = re.split(r"[，。；;,\n]+", str(notes))
    day_tokens = _day_token_variants_v2(day)
    for raw_clause in clauses:
        clause = str(raw_clause or "").strip()
        if not clause:
            continue
        lowered = clause.lower()
        if not any(token in clause or token in lowered for token in day_tokens):
            continue
        meal_type = _infer_meal_type_v2(clause)
        for cuisine_label, keywords in _extract_cuisine_preferences_v2(clause):
            dedupe_key = (meal_type, cuisine_label)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            preferences.append(
                {
                    "meal_type": meal_type,
                    "label": clause,
                    "cuisine_label": cuisine_label,
                    "keywords": keywords,
                }
            )
    return preferences


def _day_token_variants_v2(day: int) -> list[str]:
    chinese = {
        1: "第一天",
        2: "第二天",
        3: "第三天",
        4: "第四天",
        5: "第五天",
        6: "第六天",
        7: "第七天",
    }
    return [chinese.get(day, f"第{day}天"), f"第{day}天", f"day{day}", f"day {day}", f"d{day}"]


def _infer_meal_type_v2(clause: str) -> str:
    text = str(clause or "")
    if any(token in text for token in ["午饭", "午餐", "中午"]):
        return "lunch"
    if any(token in text for token in ["晚饭", "晚餐", "晚上", "夜宵", "宵夜"]):
        return "dinner"
    return "dinner"


def _extract_cuisine_preferences_v2(clause: str) -> list[tuple[str, list[str]]]:
    alias_map: list[tuple[str, list[str]]] = [
        ("火锅", ["火锅", "hotpot", "huoguo"]),
        ("烧烤", ["烧烤", "烤肉", "bbq", "barbecue", "grill"]),
        ("海鲜", ["海鲜"]),
        ("日料", ["日料", "寿司", "刺身", "日本料理"]),
        ("西餐", ["西餐", "牛排", "意面", "披萨", "汉堡"]),
        ("川菜", ["川菜"]),
        ("湘菜", ["湘菜"]),
        ("粤菜", ["粤菜"]),
        ("鲁菜", ["鲁菜"]),
        ("淮扬菜", ["淮扬菜"]),
        ("本帮菜", ["本帮菜"]),
        ("串串", ["串串", "串串香"]),
        ("烤鱼", ["烤鱼"]),
        ("小龙虾", ["小龙虾"]),
        ("咖啡", ["咖啡"]),
        ("甜品", ["甜品", "蛋糕", "冰淇淋"]),
        ("面食", ["面", "面条", "拉面", "刀削面", "面馆"]),
        ("米粉", ["米粉"]),
        ("麻辣烫", ["麻辣烫"]),
        ("麻辣香锅", ["麻辣香锅"]),
        ("饺子", ["饺子"]),
        ("早茶", ["早茶"]),
    ]
    text = str(clause or "")
    found: list[tuple[str, list[str]]] = []
    seen_labels: set[str] = set()
    for label, keywords in alias_map:
        if any(keyword.lower() in text.lower() for keyword in keywords):
            if label not in seen_labels:
                seen_labels.add(label)
                found.append((label, keywords))
    pattern = re.compile(r"(?:想吃|想安排|安排|吃|来点|试试|尝尝)([^，。；,]{1,12})")
    for match in pattern.findall(text):
        segment = str(match or "").strip()
        segment = re.sub(r"^(早饭|早餐|早茶|午饭|午餐|中午|晚饭|晚餐|晚上|夜宵|宵夜)", "", segment)
        segment = re.sub(r"(就行|即可|就好|都行|安排一下|安排)$", "", segment).strip()
        for item in re.split(r"(?:和|及|与|、|/|或|或者)", segment):
            cuisine = str(item or "").strip()
            cuisine = cuisine.strip("想吃来点试试尝尝安排一下的吧呀呢")
            if len(cuisine) < 2 or len(cuisine) > 8:
                continue
            if not re.search(r"[\u4e00-\u9fffA-Za-z]", cuisine):
                continue
            if cuisine not in seen_labels:
                seen_labels.add(cuisine)
                found.append((cuisine, [cuisine]))
    return found


def _meal_matches_keywords(meal: dict, keywords: list[str]) -> bool:
    text = str(meal.get("venue_name", "") or "").lower()
    return any(keyword.lower() in text for keyword in keywords)


def _meal_type_label(meal_type: str) -> str:
    return "午餐" if meal_type == "lunch" else "晚餐"
