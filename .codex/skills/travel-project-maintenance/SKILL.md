---
name: travel-project-maintenance
description: Use when working in this repository on architecture discovery, scoped refactors, core travel-planning changes, backend and frontend wiring, or personalization-related changes. It maps requests to the correct subsystem, enforces minimal edits, and requires repo-specific verification before completion.
---

# Travel Project Maintenance

Rule: classify the request before editing. Pick one primary subsystem and keep the write set minimal.

## Workflow

1. Read `.codex/skills/travel-project-maintenance/references/project-status.md`.
2. Classify the task into one primary surface:
   - core planner
   - personalization
   - backend API
   - frontend
   - docs only
3. State the key assumption:
   - base planner behavior change
   - personalization-only behavior change
4. Read only the hotspot files for that surface.
5. Make the smallest change that satisfies the request.
6. Run the matching verification before handoff.

## Required Verification

- If `travel_multi_agent_planner/`, `tests/`, or planner rules change:
  - `python -m unittest tests.test_orchestrator -v`
- If `personalization/` or `backend/routers/personalization.py` changes:
  - verify startup and shared-engine wiring through `backend/main.py`
- If `frontend/src/` changes:
  - `npm run build` in `frontend` when dependencies are already available

## Guardrails

- Keep base planner logic in `travel_multi_agent_planner/` unless the request is explicitly personalization-scoped.
- Do not widen scope into unrelated cleanup.
- Do not rewrite startup import order blindly.
- Do not revert user work in the dirty tree.

## Anti-Patterns

- Editing both core planner logic and personalization patches for the same behavior without a clear boundary
- Treating `backend/main.py` as a generic bootstrap file and ignoring its extension-loading role
- Making frontend polish changes during backend or planner tasks
- Declaring success without running the surface-matched verification

## Handoff Checklist

- The touched subsystem is explicit.
- The changed files are minimal.
- Verification matches the touched surface.
- Residual risks are named.
