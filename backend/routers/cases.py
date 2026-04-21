from __future__ import annotations

from fastapi import APIRouter, HTTPException

from travel_multi_agent_planner.persistence import list_saved_cases, load_case_record, load_latest_case

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("")
async def get_cases() -> list[dict]:
    records = list_saved_cases()
    return [
        {
            "case_id": r.case_id,
            "summary": r.summary,
            "generated_at": r.generated_at,
        }
        for r in records
    ]


@router.get("/latest")
async def get_latest_case() -> dict:
    result = load_latest_case()
    if result is None:
        raise HTTPException(status_code=404, detail="没有已保存的案例")
    plan, animation, record = result
    return _bundle_to_dict(plan, animation, record.case_id)


@router.get("/{case_id}")
async def get_case(case_id: str) -> dict:
    records = list_saved_cases()
    matched = next((r for r in records if r.case_id == case_id), None)
    if matched is None:
        raise HTTPException(status_code=404, detail=f"案例 {case_id} 不存在")
    plan, animation, record = load_case_record(matched)
    return _bundle_to_dict(plan, animation, record.case_id)


def _bundle_to_dict(plan, animation, case_id: str) -> dict:
    from dataclasses import asdict

    try:
        plan_dict = asdict(plan)
        # Frontend expects `days`, Python dataclass field is `day_plans`
        if "day_plans" in plan_dict:
            plan_dict["days"] = plan_dict.pop("day_plans")
    except Exception:
        plan_dict = {}
    try:
        animation_dict = asdict(animation)
    except Exception:
        animation_dict = None
    return {
        "case_id": case_id,
        "plan": plan_dict,
        "animation": animation_dict,
    }
