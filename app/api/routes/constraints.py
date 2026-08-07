from __future__ import annotations

from fastapi import APIRouter

from app.domain.constraints import (
    ConstraintProfile,
    ConstraintReport,
    RouteCandidate,
    constraint_catalogue,
    evaluate_all,
    get_constraint_profile,
)


router = APIRouter(prefix="/api/constraints", tags=["logistics-constraints"])


@router.get("/profile", response_model=ConstraintProfile)
async def profile() -> ConstraintProfile:
    """Current constraint limits, tunable via ``logistics_constraints.json``."""

    return get_constraint_profile()


@router.get("/catalogue")
async def catalogue() -> dict:
    return {"catalogue": constraint_catalogue()}


@router.post("/evaluate", response_model=list[ConstraintReport])
async def evaluate(candidates: list[RouteCandidate]) -> list[ConstraintReport]:
    """Check candidate solutions against every hard and soft constraint.

    Exposed so planners and integration tests can verify a proposal without
    running the whole agent workflow.
    """

    return evaluate_all(candidates)
