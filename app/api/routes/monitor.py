from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.fleet import get_profile
from app.domain.network import load_network
from app.routing import (
    MonitoringEvent,
    RouteCandidate,
    VehicleContext,
    get_route_monitor,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/routes/monitor", tags=["monitoring"])


class StartMonitorRequest(BaseModel):
    origin: str
    destination: str
    route_stops: list[str] = Field(default_factory=list)
    original_eta_minutes: float = 0.0
    vehicle_profile: str | None = None


class StartMonitorResponse(BaseModel):
    id: str
    message: str


class DeregisterResponse(BaseModel):
    ok: bool
    message: str


@router.post("/start", response_model=StartMonitorResponse)
async def start_monitoring(req: StartMonitorRequest) -> StartMonitorResponse:
    """Register a route for delay monitoring.

    ``route_stops`` is the corridor being driven. Only the stop sequence and the
    promised ETA are needed — polling compares the current ETA against the
    original, so no cost breakdown has to be carried in.
    """

    if not settings.enable_route_monitoring:
        raise HTTPException(
            status_code=400, detail="Route monitoring is disabled by configuration."
        )

    network = await load_network()
    stops = [
        network.resolve(stop) or stop
        for stop in (req.route_stops or [req.origin, req.destination])
    ]
    # RouteCandidate.label indexes stops[0] and stops[-1], so a shorter sequence
    # would produce a monitored route that raises on read.
    if len(stops) < 2:
        raise HTTPException(
            status_code=400,
            detail="route_stops must contain at least an origin and a destination.",
        )

    vehicle: VehicleContext | None = None
    if req.vehicle_profile:
        profile = get_profile(req.vehicle_profile)
        if profile is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown vehicle profile '{req.vehicle_profile}'.",
            )
        vehicle = VehicleContext.from_profile(profile)

    route = RouteCandidate(
        rank=1,
        stops=stops,
        estimated_travel_minutes=req.original_eta_minutes,
    )

    monitor = get_route_monitor()
    try:
        route_id = monitor.register(
            route=route,
            vehicle=vehicle,
            original_eta=req.original_eta_minutes,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return StartMonitorResponse(
        id=route_id, message=f"Started monitoring route {route_id}"
    )


@router.get("/status")
async def get_monitor_status() -> dict[str, Any]:
    return get_route_monitor().status()


@router.get("/{route_id}/events", response_model=list[MonitoringEvent])
async def get_route_events(route_id: str) -> list[MonitoringEvent]:
    events = get_route_monitor().events(route_id)
    if events is None:
        raise HTTPException(status_code=404, detail="Route not found.")
    return events


@router.post("/{route_id}/poll", response_model=MonitoringEvent)
async def poll_route(route_id: str) -> MonitoringEvent:
    monitor = get_route_monitor()
    if monitor.get(route_id) is None:
        raise HTTPException(status_code=404, detail="Route not found.")

    try:
        network = await load_network()
        return await monitor.poll(route_id, network)
    except KeyError as exc:
        # Deregistered between the check above and the poll.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface a clean 500, log the cause
        logger.exception("Error polling route", extra={"route_id": route_id})
        raise HTTPException(
            status_code=500, detail="Internal server error during polling."
        ) from exc


@router.delete("/{route_id}", response_model=DeregisterResponse)
async def deregister_route(route_id: str) -> DeregisterResponse:
    if not get_route_monitor().deregister(route_id):
        raise HTTPException(status_code=404, detail="Route not found.")

    return DeregisterResponse(ok=True, message=f"Route {route_id} deregistered.")
