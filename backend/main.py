from __future__ import annotations

import importlib
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

# Ensure project root is on the path so the existing package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------------------------------------------------------------------------
# Load personalization extensions BEFORE importing routers/agents.
# This must happen before "from .routers import ..." because routers import
# orchestrator which imports agents, and the patch must be in place before
# those imports happen.
# ---------------------------------------------------------------------------
from personalization.agents.code_modifier import (
    ensure_extension_runtime,
    sync_runtime_agent_bindings,
    upgrade_saved_extension_file,
)

_base_path = Path(__file__).parent.parent
_ext_dir = _base_path / "personalization" / "extensions"
if _ext_dir.exists():
    for _ext_file in _ext_dir.glob("*.py"):
        if _ext_file.name.startswith("_"):
            continue
        if _ext_file.name.startswith("template_"):
            continue
        try:
            upgrade_saved_extension_file(_ext_file)
            _mod_path = f"personalization.extensions.{_ext_file.stem}"
            if _mod_path not in sys.modules:
                _module = importlib.import_module(_mod_path)
                ensure_extension_runtime(_module)
                print(f"[Extension] Loaded: {_ext_file.name}")
        except Exception as e:
            print(f"[Extension] Failed to load {_ext_file.name}: {e}")
sync_runtime_agent_bindings()
# ---------------------------------------------------------------------------

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import plan, cases, status, route, personalization

# Store engine globally so personalization router can use it
_personalization_engine = None


async def initialize_personalization_engine() -> None:
    """Load personalization extensions at application startup."""
    global _personalization_engine
    base_path = Path(__file__).parent.parent
    try:
        from travel_multi_agent_planner.providers.bailian import BailianLLMProvider
        from travel_multi_agent_planner.config import AppConfig

        config = AppConfig.from_env()
        llm_provider = None
        if config.dashscope_api_key:
            llm_provider = BailianLLMProvider(api_key=config.dashscope_api_key, model=config.bailian_model)

        from personalization.engine import PersonalizationEngine
        # Initialize the engine with LLM provider to load saved extensions
        _personalization_engine = PersonalizationEngine(base_path, llm_provider=llm_provider)
        sync_runtime_agent_bindings()
        print(f"[Startup] Personalization engine initialized, loaded extensions")
    except Exception as e:
        print(f"[Startup] Failed to initialize personalization engine: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await initialize_personalization_engine()
    yield


app = FastAPI(title="Travel Multi-Agent Planner API", version="1.0.0", lifespan=lifespan)

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
app.include_router(personalization.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
async def frontend_config() -> dict:
    from travel_multi_agent_planner.config import AppConfig
    cfg = AppConfig.from_env()
    return {"tencent_map_js_key": cfg.tencent_map_js_key or ""}
