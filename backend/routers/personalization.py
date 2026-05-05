"""Personalization API router."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from personalization import PersonalizationEngine

router = APIRouter(prefix="/api/personalize", tags=["personalization"])


def _get_engine() -> PersonalizationEngine:
    # Import from backend.main to use the globally stored engine instance
    from backend.main import _personalization_engine

    if _personalization_engine is None:
        raise HTTPException(status_code=503, detail="Personalization engine is not initialized")
    return _personalization_engine


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class PersonalizeRequest(BaseModel):
    user_text: str
    context: dict | None = None


class ApplyRequest(BaseModel):
    requirement_id: str
    approved: bool = True
    user_notes: str = ""
    save_extensions: bool = True  # Whether to save extension files to disk for persistence across restarts


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/process")
async def process_requirement(body: PersonalizeRequest) -> dict:
    """
    Process a personalization requirement and return analysis for user confirmation.

    Returns:
        PersonalizationResult with parsed requirement, patch, impact, and review.
    """
    engine = _get_engine()

    try:
        result = await engine.process_requirement(body.user_text, body.context)
        return result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/apply")
async def apply_modification(body: ApplyRequest) -> dict:
    """
    Apply a confirmed modification.

    Args:
        body.requirement_id: ID from process_requirement response
        body.approved: Whether user approved (true = apply, false = cancel)
        body.user_notes: Optional user notes
        body.save_extensions: Whether to save extension files to disk (default True)

    Returns:
        ApplyResult with snapshot_id, validation, and status.
    """
    engine = _get_engine()

    try:
        result = await engine.apply_modification(
            body.requirement_id,
            approved=body.approved,
            user_notes=body.user_notes,
            save_extensions=body.save_extensions,
        )
        return result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rollback")
async def rollback_modification(snapshot_id: str) -> dict:
    """
    Rollback to a previous snapshot.

    Args:
        snapshot_id: ID of the snapshot to rollback to

    Returns:
        RollbackResult with success status and reverted files.
    """
    engine = _get_engine()

    try:
        result = await engine.rollback_modification(snapshot_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_snapshot_history() -> list[dict]:
    """
    Get history of all snapshots.

    Returns:
        List of snapshot information.
    """
    engine = _get_engine()

    try:
        return engine.get_snapshot_history()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending")
async def list_pending() -> list[dict]:
    """
    List all pending requirements awaiting confirmation.

    Returns:
        List of pending requirement summaries.
    """
    engine = _get_engine()

    try:
        return engine.list_pending_requirements()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_status() -> dict:
    """
    Get current status of the personalization engine.

    Returns:
        Status dictionary with counts and availability.
    """
    engine = _get_engine()

    try:
        return engine.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/extensions")
async def list_extensions() -> dict:
    """
    List currently active extension files.

    Returns:
        Dictionary with active extension filenames.
    """
    engine = _get_engine()

    try:
        return {"extensions": engine.get_active_extensions()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear")
async def clear_all_extensions() -> dict:
    """
    Clear all personalization extensions, restore original agent classes,
    and remove all pending results and snapshots.

    Use this to reset the personalization system to a clean state.

    Returns:
        Dictionary describing what was cleared.
    """
    engine = _get_engine()

    try:
        return engine.clear_all_extensions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
