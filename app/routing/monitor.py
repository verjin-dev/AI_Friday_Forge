from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.routing.engine import RouteCandidate
from app.routing.cost import VehicleContext

logger = get_logger(__name__)


class MonitoringEventType(str, Enum):
    ok = "ok"
    degraded = "degraded"
    replan_triggered = "replan_triggered"
    incident_detected = "incident_detected"
    weather_alert = "weather_alert"
    completed = "completed"
    deregistered = "deregistered"


class MonitoringEvent(BaseModel):
    event_type: MonitoringEventType
    timestamp: str
    message: str
    delay_delta_minutes: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class MonitoredRoute:
    id: str
    route: RouteCandidate
    vehicle: VehicleContext | None
    registered_at: float
    last_polled: float
    original_eta_minutes: float
    current_eta_minutes: float
    events: list[MonitoringEvent] = field(default_factory=list)


class RouteMonitor:
    """Service to monitor active routes and trigger replanning if conditions degrade."""

    def __init__(self):
        self._routes: dict[str, MonitoredRoute] = {}

    @property
    def active_count(self) -> int:
        return len(self._routes)

    def register(
        self,
        route: RouteCandidate,
        vehicle: VehicleContext | None = None,
        original_eta: float = 0.0,
    ) -> str:
        if not settings.enable_route_monitoring:
            raise ValueError("Route monitoring is disabled by configuration.")

        if len(self._routes) >= settings.monitor_max_active_routes:
            raise RuntimeError(f"Max active routes ({settings.monitor_max_active_routes}) reached")

        route_id = str(uuid.uuid4())
        now_ts = time.monotonic()
        
        self._routes[route_id] = MonitoredRoute(
            id=route_id,
            route=route,
            vehicle=vehicle,
            registered_at=now_ts,
            last_polled=now_ts,
            original_eta_minutes=original_eta,
            current_eta_minutes=original_eta,
            events=[],
        )
        logger.info(f"Registered route {route_id} for monitoring.")
        return route_id

    def deregister(self, route_id: str) -> bool:
        if route_id not in self._routes:
            return False
        
        route = self._routes.pop(route_id)
        evt = MonitoringEvent(
            event_type=MonitoringEventType.deregistered,
            timestamp=datetime.now(timezone.utc).isoformat(),
            message="Route deregistered.",
        )
        route.events.append(evt)
        logger.info(f"Deregistered route {route_id}.")
        return True

    def _check_incidents(self, route: MonitoredRoute, network: Any) -> list[str]:
        if not network:
            return []
        incidents = []
        # In a real environment, this might query the network or an incident service.
        # Here we just provide the signature and return an empty list or mock.
        return incidents

    def _should_replan(self, delay_delta: float) -> bool:
        return delay_delta > settings.monitor_replan_threshold_minutes

    async def poll(self, route_id: str, network: Any = None) -> MonitoringEvent:
        if route_id not in self._routes:
            raise KeyError(f"Route {route_id} not found.")

        route = self._routes[route_id]
        
        # Simulate checking conditions / updating current ETA
        # Since we cannot call external Google live traffic in tests, 
        # we assume network or other mechanism updates this if provided.
        # For tests, the test could modify current_eta_minutes before calling poll.
        
        route.last_polled = time.monotonic()
        delay_delta = route.current_eta_minutes - route.original_eta_minutes
        
        new_incidents = self._check_incidents(route, network)
        timestamp = datetime.now(timezone.utc).isoformat()
        
        if self._should_replan(delay_delta):
            evt_type = MonitoringEventType.replan_triggered
            msg = f"Delay threshold exceeded ({delay_delta:.1f} mins > {settings.monitor_replan_threshold_minutes:.1f} mins). Replanning."
        elif new_incidents:
            evt_type = MonitoringEventType.incident_detected
            msg = f"New incidents detected on route: {new_incidents}"
        elif delay_delta > 0.0:
            evt_type = MonitoringEventType.degraded
            msg = f"Route slightly degraded, delay: {delay_delta:.1f} mins."
        else:
            evt_type = MonitoringEventType.ok
            msg = "Route conditions are nominal."

        evt = MonitoringEvent(
            event_type=evt_type,
            timestamp=timestamp,
            message=msg,
            delay_delta_minutes=delay_delta,
            details={"incidents": new_incidents} if new_incidents else {},
        )
        route.events.append(evt)
        logger.debug(f"Polled route {route_id}: {evt_type.value}")
        return evt

    async def poll_all(self, network: Any = None) -> list[tuple[str, MonitoringEvent]]:
        results = []
        for route_id in list(self._routes.keys()):
            evt = await self.poll(route_id, network)
            results.append((route_id, evt))
        return results

    def status(self) -> dict[str, Any]:
        return {
            "active_count": self.active_count,
            "routes": {
                r_id: {
                    "original_eta": r.original_eta_minutes,
                    "current_eta": r.current_eta_minutes,
                    "last_polled": r.last_polled,
                    "events_count": len(r.events),
                }
                for r_id, r in self._routes.items()
            }
        }


_monitor = RouteMonitor()

def get_route_monitor() -> RouteMonitor:
    return _monitor
