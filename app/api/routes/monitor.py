from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.routing import get_route_monitor, RouteCandidate, VehicleContext, MonitoringEvent
from app.domain.network import load_network

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
async def start_monitoring(req: StartMonitorRequest) -> Any:
    if not settings.enable_route_monitoring:
        raise HTTPException(
            status_code=400, detail="Route monitoring is disabled by configuration."
        )

    monitor = get_route_monitor()
    
    # Reconstruct a dummy or minimal RouteCandidate for monitoring purposes
    # In a real scenario, this would be a real route object from the engine
    route = RouteCandidate(
        distance_km=0.0,
        travel_time_minutes=req.original_eta_minutes,
        stops=req.route_stops,
        edges=[],
    )
    
    vehicle = None
    if req.vehicle_profile:
        # Simplistic mapping or placeholder
        vehicle = VehicleContext(profile=req.vehicle_profile)

    try:
        route_id = monitor.register(
            route=route, 
            vehicle=vehicle, 
            original_eta=req.original_eta_minutes
        )
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return StartMonitorResponse(
        id=route_id, 
        message=f"Started monitoring route {route_id}"
    )


@router.delete("/{route_id}", response_model=DeregisterResponse)
async def deregister_route(route_id: str) -> Any:
    monitor = get_route_monitor()
    ok = monitor.deregister(route_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Route not found.")
    
    return DeregisterResponse(
        ok=True, 
        message=f"Route {route_id} deregistered."
    )


@router.get("/status")
async def get_monitor_status() -> dict[str, Any]:
    monitor = get_route_monitor()
    return monitor.status()


@router.get("/{route_id}/events", response_model=list[MonitoringEvent])
async def get_route_events(route_id: str) -> Any:
    monitor = get_route_monitor()
    # Direct access to private member for API read (acceptable per spec for now)
    if route_id not in monitor._routes:
        raise HTTPException(status_code=404, detail="Route not found.")
    
    return monitor._routes[route_id].events


@router.post("/{route_id}/poll", response_model=MonitoringEvent)
async def poll_route(route_id: str) -> Any:
    monitor = get_route_monitor()
    if route_id not in monitor._routes:
        raise HTTPException(status_code=404, detail="Route not found.")

    try:
        network = await load_network()
        event = await monitor.poll(route_id, network)
        return event
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error polling route {route_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during polling.")
