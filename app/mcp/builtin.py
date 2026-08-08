from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.candidates import path_to_candidate
from app.domain.delay import predict_with_live_traffic
from app.domain.gmaps import road_geometry, snap_to_roads
from app.domain.network import load_network
from app.kg.client import get_kg_client
from app.kg.cypher import UnsafeCypherError, sanitize
from app.kg.introspect import get_graph_schema
from app.mcp.registry import ToolSpec, get_registry
from app.security.rbac import ROLES
from app.search.engine import document_search as _document_search
from app.search.web import web_search as _web_search


logger = get_logger(__name__)


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
    }


async def _http_json(
    url: str, params: dict[str, Any] | None = None, *, verify: bool | None = None
) -> Any:
    async with httpx.AsyncClient(
        timeout=settings.external_api_timeout_seconds,
        verify=settings.external_verify_ssl if verify is None else verify,
    ) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


# ----------------------------------------------------------------------
# Knowledge graph
# ----------------------------------------------------------------------
async def graph_schema() -> dict[str, Any]:
    """Return the live Neo4j schema (labels, relationships, indexes)."""

    schema = await get_graph_schema()
    return schema.model_dump(mode="json")


async def graph_query(
    cypher: str, parameters: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Execute a read-only Cypher query against the enterprise graph."""

    try:
        safe = sanitize(cypher)
    except UnsafeCypherError as exc:
        raise ValueError(f"Rejected unsafe Cypher: {exc}") from exc

    records = await get_kg_client().run(safe, parameters or {})
    return {"cypher": safe, "row_count": len(records), "rows": records}


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------
async def document_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Keyword search across enterprise documents on the platform filesystem."""

    results = await _document_search(query, limit=limit)
    return [result.model_dump(mode="json") for result in results]


async def web_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Public web search for external context (advisories, notices, regulations)."""

    results = await _web_search(query, limit=limit)
    return [result.model_dump(mode="json") for result in results]


# ----------------------------------------------------------------------
# Logistics context: weather + routing
# ----------------------------------------------------------------------
async def _geocode(place: str) -> dict[str, Any] | None:
    payload = await _http_json(
        settings.geocoding_api_url, {"name": place, "count": 1, "format": "json"}
    )
    results = payload.get("results") or []
    return results[0] if results else None


async def weather_lookup(location: str) -> dict[str, Any]:
    """Current conditions and 3-day outlook for a place — route risk input."""

    place = await _geocode(location)
    if not place:
        return {"location": location, "found": False, "error": "Location not found."}

    forecast = await _http_json(
        settings.weather_api_url,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
            "daily": "precipitation_sum,wind_speed_10m_max,weather_code",
            "forecast_days": 3,
            "timezone": "auto",
        },
    )
    return {
        "location": f"{place.get('name')}, {place.get('country', '')}".strip(", "),
        "found": True,
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": forecast.get("current", {}),
        "daily": forecast.get("daily", {}),
    }


async def maps_route(origin: str, destination: str) -> dict[str, Any]:
    """Driving distance and duration between two places."""

    start, end = await asyncio.gather(_geocode(origin), _geocode(destination))
    if not start or not end:
        missing = origin if not start else destination
        return {"found": False, "error": f"Could not geocode '{missing}'."}

    url = (
        f"{settings.routing_api_url}/"
        f"{start['longitude']},{start['latitude']};"
        f"{end['longitude']},{end['latitude']}"
    )
    payload = await _http_json(url, {"overview": "false", "alternatives": "true"})
    routes = payload.get("routes") or []
    if not routes:
        return {"found": False, "error": "No route returned by the routing service."}

    return {
        "found": True,
        "origin": start.get("name"),
        "destination": end.get("name"),
        "routes": [
            {
                "distance_km": round(route.get("distance", 0) / 1000, 2),
                "duration_minutes": round(route.get("duration", 0) / 60, 1),
            }
            for route in routes[:3]
        ],
    }


# ----------------------------------------------------------------------
# Road network (graph-native routing)
# ----------------------------------------------------------------------
async def route_plan(
    origin: str, destination: str, max_routes: int = 4
) -> dict[str, Any]:
    """Find routes between two locations using only real graph edges.

    Pathfinding is deterministic: every leg comes from a ``CONNECTED_TO``
    relationship and every incident verdict from ``HAS_INCIDENT``. Blocked
    routes are returned too, so the platform can explain the rejection.
    """

    network = await load_network()
    if not network.locations:
        return {
            "found": False,
            "error": "The road network is empty — no Location nodes in the graph.",
        }

    resolved_origin = network.resolve(origin)
    resolved_destination = network.resolve(destination)
    if not resolved_origin or not resolved_destination:
        unknown = origin if not resolved_origin else destination
        return {
            "found": False,
            "error": f"Location '{unknown}' is not in the road network.",
            "known_locations": sorted(network.locations)[:50],
        }

    paths = network.plan(resolved_origin, resolved_destination, k=max_routes)
    clear = [path for path in paths if path.is_clear]

    weather: dict[str, Any] | None = None
    try:
        weather = await weather_lookup(resolved_destination)
    except Exception:  # noqa: BLE001 - weather is optional context
        weather = None

    predictions = await predict_with_live_traffic(paths, weather=weather)

    routes = []
    for path, prediction in zip(paths, predictions):
        routes.append(
            {
                "label": path.label,
                "stops": path.stops,
                "total_distance_km": path.total_distance_km,
                "uses_alternate": path.uses_alternate,
                "legs_verified": path.legs_verified,
                "is_clear": path.is_clear,
                "legs": [leg.describe() for leg in path.legs],
                "blocking_incidents": [
                    item.describe() for item in path.blocking_incidents
                ],
                "endpoint_incidents": [
                    item.describe() for item in path.endpoint_incidents
                ],
                "advisory_incidents": [
                    item.describe() for item in path.advisory_incidents
                ],
                "predicted_total_minutes": prediction.predicted_total_minutes,
                "predicted_delay_minutes": prediction.predicted_delay_minutes,
                "free_flow_minutes": prediction.free_flow_minutes,
                "delay_risk": prediction.risk.value,
                "delay_confidence": prediction.confidence,
                "delay_baseline_source": prediction.baseline_source,
                "live_traffic_used": prediction.live_traffic_used,
                "delay_factors": [factor.describe() for factor in prediction.factors],
            }
        )

    return {
        "found": bool(paths),
        "origin": resolved_origin,
        "destination": resolved_destination,
        "clear_route_count": len(clear),
        "blocked_route_count": len(paths) - len(clear),
        "weather_at_destination": (weather or {}).get("current"),
        "routes": routes,
    }


async def network_status(near_tvm_only: bool = False) -> dict[str, Any]:
    """Current road-network state: locations, connections and active incidents."""

    network = await load_network()

    locations = network.locations
    if near_tvm_only:
        locations = {
            name: data
            for name, data in locations.items()
            if str(data.get("is_near_tvm", "")).strip().lower() == "yes"
        }

    active = [
        incident
        for items in network.incidents_by_location.values()
        for incident in items
        if incident.is_active
    ]

    return {
        "location_count": len(locations),
        "locations": sorted(locations),
        "road_count": sum(len(legs) for legs in network.adjacency.values()) // 2,
        "alternate_route_count": len(network.alternates),
        "active_incidents": [incident.describe() for incident in active],
        "blocked_locations": sorted(
            {incident.location for incident in active if incident.is_blocking}
        ),
    }


# ----------------------------------------------------------------------
# Relational + REST
# ----------------------------------------------------------------------
_SQL_FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "attach", "detach", "pragma", "vacuum",
)


def _sql_query_sync(query: str, parameters: list[Any] | None) -> dict[str, Any]:
    if settings.sqlite_path is None:
        raise ValueError("SQLITE_PATH is not configured; sql_query is unavailable.")

    lowered = query.lower()
    for keyword in _SQL_FORBIDDEN:
        if f" {keyword} " in f" {lowered} " or lowered.startswith(keyword):
            raise ValueError(f"Read-only tool: '{keyword}' is not permitted.")

    uri = f"file:{settings.sqlite_path}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(query, parameters or [])
        rows = [dict(row) for row in cursor.fetchmany(settings.neo4j_max_rows)]
    return {"row_count": len(rows), "rows": rows}


async def sql_query(
    query: str, parameters: list[Any] | None = None
) -> dict[str, Any]:
    """Run a read-only SQL query against the configured relational source."""

    return await asyncio.to_thread(_sql_query_sync, query, parameters)


async def rest_get(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET an allow-listed internal or partner REST endpoint."""

    if not settings.rest_allowlist:
        raise ValueError(
            "REST_ALLOWLIST is empty; outbound REST calls are disabled by policy."
        )

    host = (urlparse(url).hostname or "").lower()
    if not any(
        host == entry.lower() or host.endswith(f".{entry.lower()}")
        for entry in settings.rest_allowlist
    ):
        raise ValueError(f"Host '{host}' is not in REST_ALLOWLIST.")

    return await _http_json(url, params)


# ----------------------------------------------------------------------
# Filesystem (sandboxed)
# ----------------------------------------------------------------------
def _resolve_in_sandbox(relative: str) -> Path:
    root = settings.mcp_filesystem_root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Path escapes the configured document sandbox.")
    return candidate


async def file_list(subdirectory: str = "") -> list[dict[str, Any]]:
    """List files available in the enterprise document sandbox."""

    def _list() -> list[dict[str, Any]]:
        target = _resolve_in_sandbox(subdirectory)
        if not target.exists():
            return []
        root = settings.mcp_filesystem_root.resolve()
        return [
            {
                "path": str(path.relative_to(root)),
                "size_bytes": path.stat().st_size,
                "is_dir": path.is_dir(),
            }
            for path in sorted(target.iterdir())
        ]

    return await asyncio.to_thread(_list)


async def file_read(path: str, max_chars: int = 20000) -> dict[str, Any]:
    """Read a document from the sandbox."""

    def _read() -> dict[str, Any]:
        target = _resolve_in_sandbox(path)
        if not target.is_file():
            raise ValueError(f"'{path}' is not a readable file in the sandbox.")
        text = target.read_text(encoding="utf-8", errors="ignore")
        return {
            "path": path,
            "truncated": len(text) > max_chars,
            "content": text[:max_chars],
        }

    return await asyncio.to_thread(_read)


# ----------------------------------------------------------------------
# Advanced Routing, Replanning, Monitoring & Constraints
# ----------------------------------------------------------------------
async def replan_route(
    stops: list[str],
    current_node: str,
    blocked_edge: list[str] | None = None,
    reason: str = "incident_detour",
    vehicle_profile: str | None = None,
) -> dict[str, Any]:
    """Perform segment-level route replanning when a road is blocked."""
    from app.domain.fleet import get_profile
    from app.routing import ReplanRequest, VehicleContext, get_replanner

    network = await load_network()
    vehicle = get_profile(vehicle_profile) if vehicle_profile else None
    edge_tuple = tuple(blocked_edge[:2]) if blocked_edge and len(blocked_edge) >= 2 else None
    request = ReplanRequest(
        stops=stops,
        current_node=current_node,
        blocked_edge=edge_tuple,
        reason=reason,
        vehicle=VehicleContext.from_profile(vehicle) if vehicle else None,
    )
    outcome = get_replanner().replan(network, request)
    return outcome.model_dump(mode="json")


async def route_monitor_start(
    origin: str,
    destination: str,
    route_stops: list[str],
    original_eta_minutes: float,
    vehicle_profile: str | None = None,
) -> dict[str, Any]:
    """Register an active route for continuous monitoring and ETA drift detection."""
    from app.domain.fleet import get_profile
    from app.routing import RouteCandidate, VehicleContext, get_route_monitor

    vehicle = get_profile(vehicle_profile) if vehicle_profile else None
    candidate = RouteCandidate(
        rank=1,
        stops=route_stops,
        total_distance_km=0.0,
        estimated_travel_minutes=original_eta_minutes,
    )
    route_id = get_route_monitor().register(
        candidate,
        vehicle=VehicleContext.from_profile(vehicle) if vehicle else None,
        original_eta=original_eta_minutes,
    )
    return {
        "status": "registered",
        "route_id": route_id,
        "origin": origin,
        "destination": destination,
        "monitored_stops": route_stops,
        "original_eta_minutes": original_eta_minutes,
    }


async def route_monitor_status() -> dict[str, Any]:
    """Retrieve active monitoring status and event log for all tracked routes."""
    from app.routing import get_route_monitor

    return get_route_monitor().status()


async def route_monitor_poll(route_id: str) -> dict[str, Any]:
    """Poll current network and traffic conditions for a monitored route."""
    from app.routing import get_route_monitor

    network = await load_network()
    event = await get_route_monitor().poll(route_id, network=network)
    return event.model_dump(mode="json")


async def generate_realtime_incidents() -> dict[str, Any]:
    """Generate fresh real-time incident data and reload directly into Neo4j."""
    from scripts.generate_realtime_incidents import generate_incidents_data, save_csvs, upload_to_neo4j

    node_rows, location_rows = generate_incidents_data()
    save_csvs(node_rows, location_rows)
    await upload_to_neo4j(node_rows, location_rows)
    return {
        "status": "ok",
        "message": f"Successfully generated and uploaded {len(node_rows)} real-time incidents to Neo4j.",
        "incidents_count": len(node_rows),
    }


async def evaluate_constraints(
    label: str,
    stops: list[str],
    vehicle_profile: str | None = None,
    payload_weight_kg: float | None = None,
    payload_volume_m3: float | None = None,
) -> dict[str, Any]:
    """Evaluate 19 enterprise logistics and vehicle constraints against a proposed route."""
    from app.domain.constraints import evaluate_candidate, get_constraint_profile
    from app.domain.fleet import apply_profile, get_profile, profile_constraint_overrides

    network = await load_network()
    path = network.build_path(stops)
    candidate = path_to_candidate(path) if path else None
    if not candidate and len(stops) >= 2:
        planned = network.plan(stops[0], stops[-1])
        if planned:
            candidate = path_to_candidate(planned[0])
    if not candidate:
        return {"feasible": False, "error": f"Could not build route candidate for stops: {stops}"}

    candidate.label = label
    vehicle = get_profile(vehicle_profile) if vehicle_profile else None
    if vehicle:
        apply_profile(
            candidate,
            vehicle,
            payload_weight_kg=payload_weight_kg,
            payload_volume_m3=payload_volume_m3,
        )
    profile = profile_constraint_overrides(vehicle) if vehicle else get_constraint_profile()
    report = evaluate_candidate(candidate, profile)
    return report.model_dump(mode="json")


async def algorithm_list() -> dict[str, Any]:
    """List all deterministic graph pathfinding algorithms and selection criteria."""
    return {
        "available_algorithms": ["auto", "dijkstra", "astar", "yen"],
        "selection_rules": {
            "dijkstra": "Used for small graphs (< 250 nodes) without geographic coordinates",
            "astar": "Used for large graphs (>= 250 nodes) with haversine heuristic coordinates",
            "yen": "Used when K > 1 route candidates (alternatives) are requested",
        },
    }


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------
BUILTIN_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="graph_schema",
        description="Inspect the knowledge graph schema: labels, relationships, indexes.",
        parameters=_schema({}),
        handler=graph_schema,
        tags=["knowledge"],
    ),
    ToolSpec(
        name="graph_query",
        description=(
            "Run a read-only Cypher query against the Neo4j enterprise graph. "
            "Use for entity lookup, relationship traversal and aggregation."
        ),
        parameters=_schema(
            {
                "cypher": {"type": "string", "description": "Read-only Cypher."},
                "parameters": {"type": "object", "description": "Query parameters."},
            },
            ["cypher"],
        ),
        handler=graph_query,
        tags=["knowledge"],
    ),
    ToolSpec(
        name="document_search",
        description="Keyword search across enterprise documents (SOPs, policies, manifests).",
        parameters=_schema(
            {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            ["query"],
        ),
        handler=document_search,
        tags=["search"],
    ),
    ToolSpec(
        name="web_search",
        description="Public web search for external advisories, notices and regulations.",
        parameters=_schema(
            {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            ["query"],
        ),
        handler=web_search,
        external=True,
        tags=["search"],
    ),
    ToolSpec(
        name="weather_lookup",
        description=(
            "Current conditions and 3-day outlook for a city, hub or depot — "
            "use when weather could affect a route, shipment or delivery slot."
        ),
        parameters=_schema(
            {"location": {"type": "string", "description": "City, hub or depot name."}},
            ["location"],
        ),
        handler=weather_lookup,
        external=True,
        tags=["logistics"],
    ),
    ToolSpec(
        name="maps_route",
        description=(
            "Driving distance, duration and alternatives between two locations — "
            "use for route optimisation and ETA estimation."
        ),
        parameters=_schema(
            {"origin": {"type": "string"}, "destination": {"type": "string"}},
            ["origin", "destination"],
        ),
        handler=maps_route,
        external=True,
        tags=["logistics"],
    ),
    ToolSpec(
        name="route_plan",
        description=(
            "Find routes between two locations in the road network graph. "
            "Every leg is verified against CONNECTED_TO edges and checked for "
            "active incidents — use this instead of reasoning about routes."
        ),
        parameters=_schema(
            {
                "origin": {"type": "string", "description": "Start location name."},
                "destination": {"type": "string", "description": "End location name."},
                "max_routes": {"type": "integer", "default": 4},
            },
            ["origin", "destination"],
        ),
        handler=route_plan,
        tags=["logistics", "knowledge"],
    ),
    ToolSpec(
        name="network_status",
        description=(
            "Road network overview: locations, road count and every active "
            "incident, including which locations are currently blocked."
        ),
        parameters=_schema(
            {"near_tvm_only": {"type": "boolean", "default": False}}
        ),
        handler=network_status,
        tags=["logistics", "knowledge"],
    ),
    ToolSpec(
        name="replan_route",
        description=(
            "Perform segment-level route replanning when a road segment is blocked "
            "or delayed, preserving the driven prefix."
        ),
        parameters=_schema(
            {
                "stops": {"type": "array", "items": {"type": "string"}},
                "current_node": {"type": "string"},
                "blocked_edge": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string", "default": "incident_detour"},
                "vehicle_profile": {"type": "string"},
            },
            ["stops", "current_node"],
        ),
        handler=replan_route,
        tags=["logistics", "routing"],
    ),
    ToolSpec(
        name="route_monitor_start",
        description="Register an active route for continuous monitoring and ETA drift detection.",
        parameters=_schema(
            {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "route_stops": {"type": "array", "items": {"type": "string"}},
                "original_eta_minutes": {"type": "number"},
                "vehicle_profile": {"type": "string"},
            },
            ["origin", "destination", "route_stops", "original_eta_minutes"],
        ),
        handler=route_monitor_start,
        tags=["logistics", "monitoring"],
    ),
    ToolSpec(
        name="route_monitor_status",
        description="Retrieve active monitoring status and event log for all tracked routes.",
        parameters=_schema({}),
        handler=route_monitor_status,
        tags=["logistics", "monitoring"],
    ),
    ToolSpec(
        name="route_monitor_poll",
        description="Poll current network and traffic conditions for a monitored route.",
        parameters=_schema(
            {"route_id": {"type": "string"}},
            ["route_id"],
        ),
        handler=route_monitor_poll,
        tags=["logistics", "monitoring"],
    ),
    ToolSpec(
        name="generate_realtime_incidents",
        description="Generate fresh real-time incident data and reload directly into Neo4j.",
        parameters=_schema({}),
        handler=generate_realtime_incidents,
        tags=["logistics", "incidents"],
    ),
    ToolSpec(
        name="evaluate_constraints",
        description="Evaluate 19 enterprise logistics and vehicle constraints against a proposed route.",
        parameters=_schema(
            {
                "label": {"type": "string"},
                "stops": {"type": "array", "items": {"type": "string"}},
                "vehicle_profile": {"type": "string"},
                "payload_weight_kg": {"type": "number"},
                "payload_volume_m3": {"type": "number"},
            },
            ["label", "stops"],
        ),
        handler=evaluate_constraints,
        tags=["logistics", "constraints"],
    ),
    ToolSpec(
        name="algorithm_list",
        description="List all deterministic graph pathfinding algorithms and selection criteria.",
        parameters=_schema({}),
        handler=algorithm_list,
        tags=["logistics", "routing"],
    ),
    ToolSpec(
        name="sql_query",
        description="Read-only SQL against the configured relational source (ERP/WMS extract).",
        parameters=_schema(
            {
                "query": {"type": "string"},
                "parameters": {"type": "array", "items": {}},
            },
            ["query"],
        ),
        handler=sql_query,
        tags=["data"],
    ),
    ToolSpec(
        name="rest_get",
        description="GET an allow-listed internal or partner REST endpoint.",
        parameters=_schema(
            {"url": {"type": "string"}, "params": {"type": "object"}},
            ["url"],
        ),
        handler=rest_get,
        external=True,
        tags=["integration"],
    ),
    ToolSpec(
        name="file_list",
        description="List files in the enterprise document sandbox.",
        parameters=_schema({"subdirectory": {"type": "string", "default": ""}}),
        handler=file_list,
        tags=["filesystem"],
    ),
    ToolSpec(
        name="file_read",
        description="Read a document from the enterprise document sandbox.",
        parameters=_schema(
            {
                "path": {"type": "string"},
                "max_chars": {"type": "integer", "default": 20000},
            },
            ["path"],
        ),
        handler=file_read,
        tags=["filesystem"],
    ),
)


#: Registered only when GOOGLE_MAPS_API_KEY is present, so the planner never
#: selects a tool that is guaranteed to fail.
GOOGLE_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="snap_to_roads",
        description=(
            "Snap a recorded vehicle GPS trace onto the road network, removing "
            "GPS scatter. Needs at least 2 points from an actual trace — not "
            "for drawing a road between two towns."
        ),
        parameters=_schema(
            {
                "path": {
                    "type": "array",
                    "description": "Ordered GPS fixes as [lat, lon] pairs.",
                    "items": {"type": "array", "items": {"type": "number"}},
                },
                "interpolate": {"type": "boolean", "default": True},
            },
            ["path"],
        ),
        handler=snap_to_roads,
        external=True,
        tags=["logistics", "gps"],
    ),
    ToolSpec(
        name="road_geometry",
        description=(
            "Driving polyline between two places from the Google Directions "
            "API — use to render the real shape of a road on the map."
        ),
        parameters=_schema(
            {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "waypoints": {"type": "array", "items": {"type": "string"}},
            },
            ["origin", "destination"],
        ),
        handler=road_geometry,
        external=True,
        tags=["logistics", "maps"],
    ),
)


def register_builtin_tools() -> None:
    registry = get_registry()
    for spec in BUILTIN_SPECS:
        registry.register(spec, replace=True)

    count = len(BUILTIN_SPECS)
    if settings.google_maps_enabled:
        for spec in GOOGLE_SPECS:
            registry.register(spec, replace=True)
            for role_name in ("admin", "ops_manager", "dispatcher"):
                role = ROLES.get(role_name)
                if role is not None:
                    role.tools.add(spec.name)
        count += len(GOOGLE_SPECS)
    else:
        logger.info(
            "Google Maps tools not registered — GOOGLE_MAPS_API_KEY is unset"
        )

    logger.info("Built-in tools registered", extra={"count": count})
