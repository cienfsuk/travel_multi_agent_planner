---
name: personalization-maintainer
description: Use when editing the personalization subsystem, extension loading, patch generation, review/apply flow, or rollback behavior.
---

# Personalization Maintainer

## Mission

Keep the personalization layer useful without letting it silently diverge from the base planner.

## Scope

- `personalization/`
- `backend/routers/personalization.py`
- `backend/main.py`

## Workflow

1. Confirm whether the request belongs in:
   - requirement parsing
   - patch generation
   - impact analysis or review
   - apply or rollback flow
   - extension loading
2. Decide whether the change should be:
   - a base-code change
   - a personalization-only extension change
3. Trace startup and import order before editing anything related to extension loading.
4. Keep approval, snapshot, and rollback semantics intact.

## Guardrails

- Do not use monkey patching for behavior that should live in the base planner by default.
- Do not change both base planner logic and extension logic for the same rule unless the separation is explicit.
- Keep extension loading deterministic and startup-safe.
- Preserve the process -> review -> apply flow unless the user explicitly wants a different product behavior.

## Key Hotspots

- `personalization/engine.py`
- `personalization/agents/requirement_parser.py`
- `personalization/agents/code_modifier.py`
- `personalization/agents/version_manager.py`
- `backend/main.py`
- `backend/routers/personalization.py`

## Verification

- Confirm engine initialization still works from `backend/main.py`.
- Confirm the router still accesses the shared engine instance.
- If patch persistence changes, inspect `personalization/extensions/` and `personalization/patches/` behavior.

## Deliverable

Return:

- affected stage in the personalization pipeline
- files changed
- compatibility risk
- verification run
