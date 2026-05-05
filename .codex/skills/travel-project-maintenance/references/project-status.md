# Project Status Snapshot

Date observed: 2026-05-03
Workspace: `D:\MyDocuments\claude_projects\travel_multi_agent_planner`

## System Shape

- Core planner domain: `travel_multi_agent_planner/`
- FastAPI service layer: `backend/`
- React UI: `frontend/`
- Personalization extension layer: `personalization/`
- Regression tests: `tests/test_orchestrator.py`

## Primary Entry Points

- Core orchestration: `travel_multi_agent_planner/orchestrator.py`
- Backend bootstrap: `backend/main.py`
- Personalization API: `backend/routers/personalization.py`
- React shell: `frontend/src/App.tsx`
- Legacy Streamlit shell: `streamlit_app.py`

## Current Architecture Facts

- The base planning flow is still orchestrator-led.
- The personalization subsystem is a second workflow that can generate and load runtime extensions.
- `backend/main.py` loads personalization extensions before importing routers and also initializes a shared `PersonalizationEngine` on startup.
- `backend/routers/personalization.py` reads that shared engine from `backend.main`.
- `tests/test_orchestrator.py` is the strongest existing regression net for core planning behavior.

## Active Dirty Worktree

Observed from `git status -sb`:

- modified: `.claude/settings.local.json`
- modified: `backend/main.py`
- modified: `frontend/src/App.tsx`
- modified: `travel_multi_agent_planner/agents/food_spot.py`
- modified: `travel_multi_agent_planner/orchestrator.py`
- modified: `travel_multi_agent_planner/scheduling.py`
- untracked: `backend/routers/personalization.py`
- untracked: `frontend/src/components/PersonalizationView.tsx`
- untracked: `personalization/`
- untracked docs and demo files

Implication: treat personalization as active work in progress and avoid broad refactors.

## Change Hotspots

### Core planner

- `travel_multi_agent_planner/orchestrator.py`
- `travel_multi_agent_planner/agents/planner.py`
- `travel_multi_agent_planner/agents/food_spot.py`
- `travel_multi_agent_planner/scheduling.py`

### Personalization

- `personalization/engine.py`
- `personalization/agents/requirement_parser.py`
- `personalization/agents/code_modifier.py`
- `personalization/agents/version_manager.py`
- `backend/main.py`
- `backend/routers/personalization.py`

### Frontend

- `frontend/src/App.tsx`
- `frontend/src/components/PersonalizationView.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/types/api.ts`

## Current Risks

- The core planner and personalization layer can drift if the same behavior is edited in both places.
- Startup behavior is sensitive because extension loading happens early in `backend/main.py`.
- The personalization subsystem depends on runtime patching and file-backed snapshots, which raises rollback and determinism risk.
- The repository already contains local changes, so edits must be surgical.

## Verification Map

- Core planner changes: `python -m unittest tests.test_orchestrator -v`
- Personalization wiring changes: inspect `backend/main.py` and `backend/routers/personalization.py` together
- Frontend changes: `npm run build` in `frontend` when available
