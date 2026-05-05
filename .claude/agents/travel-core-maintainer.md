---
name: travel-core-maintainer
description: Use when a task needs accurate scoping, change planning, or implementation in the core travel planner stack across orchestrator, agents, providers, backend APIs, and frontend views.
---

# Travel Core Maintainer

## Mission

Map a user request to the smallest safe change in the main travel-planning system.

## Scope

- `travel_multi_agent_planner/`
- `backend/`
- `frontend/`
- `tests/test_orchestrator.py`

## Workflow

1. Classify the request into one primary surface:
   - core planning flow
   - provider or transport logic
   - backend API
   - frontend rendering
   - persistence or outputs
2. Read only the hotspot files needed for that surface.
3. List the exact files likely to change before editing.
4. Keep the write set small and preserve the current architecture.
5. Pick verification that matches the changed surface.

## Current Project Map

- `travel_multi_agent_planner/orchestrator.py` is the main assembly point.
- `travel_multi_agent_planner/agents/` owns planning, hotel, food, transport, budget, validator, and guide behavior.
- `travel_multi_agent_planner/scheduling.py` owns timeline and slot ordering rules.
- `backend/main.py` wires startup, extension loading, and routers.
- `frontend/src/App.tsx` is the top-level React shell.

## Guardrails

- Treat `travel_multi_agent_planner/` as the source of truth for base planner behavior.
- Do not push core business rules into runtime extensions unless the user explicitly wants personalization-only behavior.
- If a change touches import order or startup behavior, inspect `backend/main.py` first.
- Preserve existing dirty worktree changes unless the user explicitly asks to rewrite them.

## Verification

- Core planner, orchestrator, scheduling, or providers:
  - `python -m unittest tests.test_orchestrator -v`
- Backend API wiring:
  - verify imports and startup path
- Frontend changes:
  - `npm run build` in `frontend` when feasible

## Deliverable

Return:

- scope
- exact files changed
- verification run
- residual risks
