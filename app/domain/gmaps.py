"""Optional Google Maps Platform integrations.

Two different jobs, often confused:

``snap_to_roads``
    Takes GPS breadcrumbs a vehicle actually produced and snaps them onto the
    road network, removing GPS scatter. Requires a real trace — snapping two
    town centroids tells you nothing.

``road_geometry``
    Asks the Directions API for the driving polyline between two points. This
    is what draws a road-shaped line on the map when you have endpoints but no
    trace.

Both are inert unless ``GOOGLE_MAPS_API_KEY`` is set; the platform's own
graph-based routing never depends on them.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger(__name__)

#: Google caps a single snapToRoads request at 100 points.
MAX_SNAP_POINTS = 100


class GoogleMapsNotConfiguredError(RuntimeError):
    """Raised when a Google tool is called without an API key."""


def _require_key() -> str:
    if not settings.google_maps_api_key:
        raise GoogleMapsNotConfiguredError(
            "GOOGLE_MAPS_API_KEY is not set; Google Maps tools are disabled."
        )
    return settings.google_maps_api_key


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Decode Google's encoded polyline format into (lat, lon) pairs."""

    points: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lon = 0

    while index < len(encoded):
        for axis in range(2):
            shift = 0
            result = 0
            while index < len(encoded):
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if axis == 0:
                lat += delta
            else:
                lon += delta
        points.append((lat / 1e5, lon / 1e5))

    return points


def _parse_point(value: Any) -> tuple[float, float] | None:
    """Accept {"lat":..,"lng":..}, [lat, lon] or "lat,lon"."""

    if isinstance(value, dict):
        lat = value.get("lat", value.get("latitude"))
        lon = value.get("lng", value.get("lon", value.get("longitude")))
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        lat, lon = value
    elif isinstance(value, str) and "," in value:
        parts = value.split(",", 1)
        lat, lon = parts[0], parts[1]
    else:
        return None

    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


async def snap_to_roads(
    path: list[Any], interpolate: bool = True
) -> dict[str, Any]:
    """Snap a vehicle GPS trace onto the road network.

    ``path`` is an ordered list of recorded positions. Anything under two
    points is rejected: a single fix has no direction to snap along.
    """

    key = _require_key()

    points = [parsed for parsed in (_parse_point(item) for item in path) if parsed]
    if len(points) < 2:
        raise ValueError(
            "snap_to_roads needs at least 2 GPS points from a recorded vehicle "
            "trace. To draw a road between two towns, use road_geometry instead."
        )

    if len(points) > MAX_SNAP_POINTS:
        logger.info(
            "Trimming GPS trace to Google's per-request limit",
            extra={"received": len(points), "limit": MAX_SNAP_POINTS},
        )
        points = points[:MAX_SNAP_POINTS]

    encoded_path = "|".join(f"{lat},{lon}" for lat, lon in points)

    async with httpx.AsyncClient(
        timeout=settings.external_api_timeout_seconds,
        verify=settings.external_verify_ssl,
    ) as client:
        response = await client.get(
            settings.google_roads_snap_url,
            params={
                "path": encoded_path,
                "interpolate": "true" if interpolate else "false",
                "key": key,
            },
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"Roads API returned {response.status_code}: {response.text[:200]}"
        )

    payload = response.json()
    snapped = payload.get("snappedPoints") or []

    return {
        "input_points": len(points),
        "snapped_points": len(snapped),
        "interpolated": interpolate,
        "points": [
            {
                "latitude": item["location"]["latitude"],
                "longitude": item["location"]["longitude"],
                "original_index": item.get("originalIndex"),
                "place_id": item.get("placeId"),
            }
            for item in snapped
        ],
        "warning": payload.get("warningMessage"),
    }


async def road_geometry(
    origin: str, destination: str, waypoints: list[str] | None = None
) -> dict[str, Any]:
    """Driving polyline between two places, for map rendering.

    ``origin``/``destination`` may be place names or "lat,lon" strings.
    """

    key = _require_key()

    params: dict[str, Any] = {
        "origin": origin,
        "destination": destination,
        "mode": "driving",
        "alternatives": "true",
        "key": key,
    }
    if waypoints:
        params["waypoints"] = "|".join(waypoints)

    async with httpx.AsyncClient(
        timeout=settings.external_api_timeout_seconds,
        verify=settings.external_verify_ssl,
    ) as client:
        response = await client.get(settings.google_directions_url, params=params)

    if response.status_code != 200:
        raise RuntimeError(
            f"Directions API returned {response.status_code}: {response.text[:200]}"
        )

    payload = response.json()
    status = payload.get("status")
    if status != "OK":
        return {
            "found": False,
            "status": status,
            "error": payload.get("error_message", "No route returned."),
        }

    routes = []
    for route in payload.get("routes", [])[:3]:
        leg_totals = route.get("legs", [])
        distance = sum(leg.get("distance", {}).get("value", 0) for leg in leg_totals)
        duration = sum(leg.get("duration", {}).get("value", 0) for leg in leg_totals)
        encoded = (route.get("overview_polyline") or {}).get("points", "")
        decoded = decode_polyline(encoded) if encoded else []

        routes.append(
            {
                "summary": route.get("summary"),
                "distance_km": round(distance / 1000, 2),
                "duration_minutes": round(duration / 60, 1),
                "polyline": [
                    {"latitude": lat, "longitude": lon} for lat, lon in decoded
                ],
                "warnings": route.get("warnings", []),
            }
        )

    return {"found": True, "origin": origin, "destination": destination, "routes": routes}
