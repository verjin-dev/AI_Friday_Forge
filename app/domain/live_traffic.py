"""Live traffic from the Google Routes API.

Division of responsibility across the platform:

* **Neo4j is the historical / authoritative record** — which roads exist, how
  long they are, which alternates are sanctioned, and which incidents are
  known. Feasibility is decided here and nowhere else.
* **Google Routes API is the live signal** — how long the drive actually takes
  under current traffic.

The Routes API returns ``duration`` (traffic-aware) alongside ``staticDuration``
(free-flow). Their difference is *measured* congestion delay rather than an
assumed one, which is why it is preferred over the heuristic factors in
:mod:`app.domain.delay` whenever the call succeeds.

Google never overrides the graph: if the graph says a road is blocked by a
Critical incident, a cheerful live ETA does not make that route legal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.geo import resolve_all
from app.domain.gmaps import decode_polyline


logger = get_logger(__name__)

_FIELD_MASK = ",".join(
    [
        "routes.duration",
        "routes.staticDuration",
        "routes.distanceMeters",
        "routes.description",
        "routes.polyline.encodedPolyline",
        "routes.travelAdvisory.speedReadingIntervals",
    ]
)


class LiveRoute(BaseModel):
    """One traffic-aware route returned by the live API."""

    description: str = ""
    distance_km: float = 0.0
    #: Traffic-aware travel time.
    duration_minutes: float = 0.0
    #: Free-flow travel time for the same geometry.
    static_duration_minutes: float = 0.0
    polyline: list[dict[str, float]] = Field(default_factory=list)
    congestion: dict[str, int] = Field(default_factory=dict)

    @property
    def traffic_delay_minutes(self) -> float:
        """Measured congestion delay — never negative."""

        return max(0.0, round(self.duration_minutes - self.static_duration_minutes, 1))

    @property
    def congestion_ratio(self) -> float:
        if self.static_duration_minutes <= 0:
            return 1.0
        return round(self.duration_minutes / self.static_duration_minutes, 2)

    def describe(self) -> str:
        return (
            f"{self.description or 'live route'}: {self.distance_km:.1f} km, "
            f"{self.duration_minutes:.0f} min with traffic "
            f"({self.static_duration_minutes:.0f} min free-flow, "
            f"+{self.traffic_delay_minutes:.0f} min congestion)"
        )


class LiveTraffic(BaseModel):
    available: bool = False
    origin: str = ""
    destination: str = ""
    routes: list[LiveRoute] = Field(default_factory=list)
    fetched_at: str = ""
    error: str | None = None

    @property
    def best(self) -> LiveRoute | None:
        if not self.routes:
            return None
        return min(self.routes, key=lambda route: route.duration_minutes)


def _seconds(value: Any) -> float:
    """Routes API durations arrive as strings like ``"1234s"``."""

    if value is None:
        return 0.0
    text = str(value).strip()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return 0.0


def _congestion_summary(advisory: dict[str, Any] | None) -> dict[str, int]:
    """Count how many road segments fall in each congestion band."""

    if not advisory:
        return {}
    counts: dict[str, int] = {}
    for interval in advisory.get("speedReadingIntervals") or []:
        band = interval.get("speed", "UNKNOWN")
        counts[band] = counts.get(band, 0) + 1
    return counts


def _waypoint(latitude: float, longitude: float) -> dict[str, Any]:
    return {"location": {"latLng": {"latitude": latitude, "longitude": longitude}}}


async def fetch_live_traffic(
    stops: list[str],
    *,
    departure: datetime | None = None,
    coordinates: dict[str, dict[str, float]] | None = None,
) -> LiveTraffic:
    """Ask Google for the traffic-aware drive along a sequence of stops.

    ``stops`` are graph location names; intermediate stops are passed as
    waypoints so the live route follows the same corridor the graph chose,
    rather than whatever Google would pick on its own.
    """

    if len(stops) < 2:
        return LiveTraffic(available=False, error="Need at least two stops.")

    if not settings.live_traffic_enabled:
        return LiveTraffic(available=False, error="Live traffic is disabled.")

    if not settings.google_maps_api_key:
        return LiveTraffic(
            available=False, error="GOOGLE_MAPS_API_KEY is not configured."
        )

    points = coordinates or await resolve_all(stops)
    missing = [stop for stop in stops if stop not in points]
    if missing:
        return LiveTraffic(
            available=False,
            origin=stops[0],
            destination=stops[-1],
            error=f"No coordinates for: {', '.join(missing)}",
        )

    body: dict[str, Any] = {
        "origin": _waypoint(**points[stops[0]]),
        "destination": _waypoint(**points[stops[-1]]),
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "computeAlternativeRoutes": len(stops) == 2,
        "languageCode": "en-IN",
        "units": "METRIC",
    }

    intermediates = [_waypoint(**points[stop]) for stop in stops[1:-1]]
    if intermediates:
        body["intermediates"] = intermediates

    if departure:
        # The API rejects departure times in the past.
        when = max(departure, datetime.now(timezone.utc) + timedelta(seconds=60))
        body["departureTime"] = when.astimezone(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")

    try:
        async with httpx.AsyncClient(
            timeout=settings.external_api_timeout_seconds,
            verify=settings.external_verify_ssl,
        ) as client:
            response = await client.post(
                settings.google_routes_url,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": settings.google_maps_api_key,
                    "X-Goog-FieldMask": _FIELD_MASK,
                },
            )
    except Exception as exc:  # noqa: BLE001 - live data is optional enrichment
        logger.info("Live traffic unavailable", extra={"error": str(exc)[:200]})
        return LiveTraffic(
            available=False,
            origin=stops[0],
            destination=stops[-1],
            error=str(exc)[:200],
        )

    if response.status_code != 200:
        detail = response.text[:300]
        logger.warning(
            "Routes API error",
            extra={"status": response.status_code, "detail": detail},
        )
        return LiveTraffic(
            available=False,
            origin=stops[0],
            destination=stops[-1],
            error=f"Routes API {response.status_code}: {detail}",
        )

    payload = response.json()
    routes: list[LiveRoute] = []
    for route in payload.get("routes", []):
        encoded = (route.get("polyline") or {}).get("encodedPolyline", "")
        decoded = decode_polyline(encoded) if encoded else []
        routes.append(
            LiveRoute(
                description=route.get("description", ""),
                distance_km=round(route.get("distanceMeters", 0) / 1000, 2),
                duration_minutes=round(_seconds(route.get("duration")) / 60, 1),
                static_duration_minutes=round(
                    _seconds(route.get("staticDuration")) / 60, 1
                ),
                polyline=[
                    {"latitude": lat, "longitude": lon} for lat, lon in decoded
                ],
                congestion=_congestion_summary(route.get("travelAdvisory")),
            )
        )

    if not routes:
        return LiveTraffic(
            available=False,
            origin=stops[0],
            destination=stops[-1],
            error="Routes API returned no routes.",
        )

    return LiveTraffic(
        available=True,
        origin=stops[0],
        destination=stops[-1],
        routes=routes,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
