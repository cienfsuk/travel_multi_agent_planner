from __future__ import annotations

import asyncio
import json
import queue
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


# ---------------------------------------------------------------------------
# Pydantic request schema (mirrors TripRequest dataclass)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# SSE streaming endpoint
# ---------------------------------------------------------------------------
@router.post("/stream")
async def stream_plan(body: PlanRequest) -> StreamingResponse:
    """
    Accept a trip request and stream back:
      - {"type": "trace", "agent": "...", "msg": "..."}  (agent log lines, replayed after completion)
      - {"type": "done", "case_id": "...", "plan": {...}, "animation": {...}}
      - {"type": "error", "msg": "..."}
    """

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
        """Called by the orchestrator after each agent step — pushes a trace event immediately."""
        result_queue.put(("trace", step, None, None))

    def _run() -> None:
        try:
            orch = TravelPlanningOrchestrator()
            plan = orch.create_plan(trip_request, on_trace=_on_trace)

            # Build animation bundle (same as app.py logic)
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
        # Start orchestrator in a thread
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        # Stream events as they arrive from the orchestrator
        while True:
            try:
                item = await loop.run_in_executor(
                    None, lambda: result_queue.get(timeout=2.0)
                )
            except Exception:
                # timeout – send heartbeat
                yield _sse({"type": "heartbeat"})
                continue

            event_type = item[0]
            if event_type == "error":
                yield _sse({"type": "error", "msg": item[1]})
                break

            if event_type == "trace":
                # Real-time trace event from on_trace callback
                step = item[1]
                yield _sse({
                    "type": "trace",
                    "agent": step.agent_name,
                    "msg": step.output_summary,
                    "decisions": step.key_decisions,
                })
                continue

            # event_type == "done"
            _, plan, animation, case_id = item

            # Final payload
            plan_dict = _plan_to_dict(plan)
            animation_dict = asdict(animation) if animation is not None else None
            yield _sse({
                "type": "done",
                "case_id": case_id,
                "plan": plan_dict,
                "animation": animation_dict,
            })
            break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _plan_to_dict(plan) -> dict:
    try:
        d = asdict(plan)
        # Frontend expects `days`, Python dataclass field is `day_plans`
        if "day_plans" in d:
            d["days"] = d.pop("day_plans")
        return d
    except Exception:
        return {}
