from __future__ import annotations

import json

from fastapi import APIRouter, Query

from pydantic import BaseModel

from app.core.config import settings
from app.observability.kpis import compute_kpis, record_outcome
from app.observability.metrics import load_recent_runs, summarise_runs


router = APIRouter(prefix="/api/observability", tags=["observability"])


@router.get("/metrics")
async def metrics(limit: int = Query(default=200, le=1000)) -> dict:
    return summarise_runs(limit)


class OutcomeReport(BaseModel):
    """A real arrival, which is what turns predictions into accuracy."""

    origin: str
    destination: str
    route_label: str
    predicted_minutes: float
    actual_minutes: float
    promised_minutes: float | None = None


@router.get("/kpis")
async def kpis(limit: int = Query(default=500, le=5000)) -> dict:
    """Business KPIs.

    Splits what the platform can measure from its own decisions from what needs
    delivery outcomes fed back before it can be reported honestly.
    """

    return compute_kpis(limit)


@router.post("/outcomes")
async def submit_outcome(report: OutcomeReport) -> dict:
    """Record an actual arrival so on-time rate and accuracy become computable."""

    record_outcome(**report.model_dump())
    return {"recorded": True}


@router.get("/runs")
async def runs(limit: int = Query(default=25, le=200)) -> list[dict]:
    return load_recent_runs(limit)


@router.get("/security-audit")
async def security_audit(limit: int = Query(default=50, le=500)) -> list[dict]:
    """Tail of the security audit log."""

    path = settings.security_audit_log_path
    if not path.exists():
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    events: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(events) >= limit:
            break
    return events
