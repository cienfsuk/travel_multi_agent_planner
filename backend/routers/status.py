from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from travel_multi_agent_planner.orchestrator import TravelPlanningOrchestrator

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("")
async def get_status() -> dict:
    orch = TravelPlanningOrchestrator()
    statuses = orch.provider_statuses()
    return {
        "providers": [asdict(s) for s in statuses],
        "mode": orch.current_mode(),
    }
