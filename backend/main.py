from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on the path so the existing package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import plan, cases, status, route

app = FastAPI(title="Travel Multi-Agent Planner API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(plan.router)
app.include_router(cases.router)
app.include_router(status.router)
app.include_router(route.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
async def frontend_config() -> dict:
    from travel_multi_agent_planner.config import AppConfig
    cfg = AppConfig.from_env()
    return {"tencent_map_js_key": cfg.tencent_map_js_key or ""}
