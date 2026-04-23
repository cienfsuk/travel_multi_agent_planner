from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from travel_multi_agent_planner.config import AppConfig
from travel_multi_agent_planner.providers import TencentMapProvider

router = APIRouter(prefix="/api/route", tags=["route"])


class RoutePointInput(BaseModel):
    lat: float
    lon: float
    label: str = ""


class RoutePlanRequest(BaseModel):
    mode: Literal["walking", "driving", "bicycling", "transit"] = "driving"
    points: list[RoutePointInput] = Field(default_factory=list)
    prefer_waypoints: bool = True


@router.post("/plan")
async def plan_route(body: RoutePlanRequest) -> dict:
    if len(body.points) < 2:
        raise HTTPException(status_code=422, detail="At least two points are required.")

    cfg = AppConfig.from_env()
    if not cfg.tencent_map_server_key:
        raise HTTPException(
            status_code=503,
            detail="Missing TENCENT_MAP_SERVER_KEY, route planning is unavailable.",
        )

    provider = TencentMapProvider(api_key=cfg.tencent_map_server_key)
    try:
        result = provider.plan_ordered_route(
            points=[point.model_dump() for point in body.points],
            mode=body.mode,
            prefer_waypoints=body.prefer_waypoints,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Route planning failed: {exc}") from exc

    return result

