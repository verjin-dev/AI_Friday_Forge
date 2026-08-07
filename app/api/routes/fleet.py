from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.domain.fleet import get_profile, load_profiles, profile_summary
from app.domain.fleet_ops import build_fleet


router = APIRouter(prefix="/api/fleet", tags=["fleet"])


@router.get("/profiles")
async def profiles() -> dict[str, Any]:
    """Vehicle and consignment profiles from the fleet template."""

    records = load_profiles()
    return {
        "count": len(records),
        "profiles": [profile_summary(profile) for profile in records.values()],
    }


@router.get("/profiles/{profile_id}")
async def profile(profile_id: str) -> dict[str, Any]:
    record = get_profile(profile_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown vehicle profile '{profile_id}'."
        )
    return profile_summary(record)


@router.get("/overview")
async def overview(limit: int = Query(default=6, ge=1, le=12)) -> dict[str, Any]:
    """Live fleet view for the dashboard: trucks, alerts and headline metrics.

    Vehicles and progress are derived — the dataset has no telemetry — but
    routes, constraint verdicts, incidents and delay predictions are real. The
    payload says so via `derived` and `position_source`.
    """

    return await build_fleet(limit)
