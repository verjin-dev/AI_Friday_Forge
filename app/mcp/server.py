"""Model Context Protocol (MCP) Server for LogiPilot AI built with FastMCP.

Exposes all enterprise logistics, graph pathfinding, segment replanning, active monitoring,
constraint evaluation, weather lookup, document search, and SQL tools as standard MCP tools.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from fastmcp import FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

from app.mcp import builtin

mcp = FastMCP(
    "LogiPilot AI Engine",
    instructions=(
        "Enterprise Travel & Logistics Route Optimization Platform MCP Server. "
        "Provides tools for knowledge graph reasoning, deterministic route planning, "
        "segment-level replanning, active route monitoring, constraint checking, "
        "weather risk analysis, document search, and telemetry."
    ),
)


@mcp.tool()
async def graph_schema() -> str:
    """Inspect the live Neo4j knowledge graph schema (labels, relationships, indexes)."""
    res = await builtin.graph_schema()
    return json.dumps(res, indent=2)


@mcp.tool()
async def graph_query(cypher: str, parameters: dict[str, Any] | None = None) -> str:
    """Execute a read-only Cypher query against the Neo4j enterprise transportation graph."""
    res = await builtin.graph_query(cypher, parameters)
    return json.dumps(res, indent=2)


@mcp.tool()
async def route_plan(origin: str, destination: str, max_routes: int = 4) -> str:
    """Find deterministic routes between locations in the graph with live traffic delay predictions."""
    res = await builtin.route_plan(origin, destination, max_routes=max_routes)
    return json.dumps(res, indent=2)


@mcp.tool()
async def network_status(near_tvm_only: bool = False) -> str:
    """Road network overview: location count, road count, active and blocking incidents."""
    res = await builtin.network_status(near_tvm_only=near_tvm_only)
    return json.dumps(res, indent=2)


@mcp.tool()
async def replan_route(
    stops: list[str],
    current_node: str,
    blocked_edge: list[str] | None = None,
    reason: str = "incident_detour",
    vehicle_profile: str | None = None,
) -> str:
    """Perform segment-level route replanning when a road segment is blocked, preserving driven prefix."""
    res = await builtin.replan_route(
        stops=stops,
        current_node=current_node,
        blocked_edge=blocked_edge,
        reason=reason,
        vehicle_profile=vehicle_profile,
    )
    return json.dumps(res, indent=2)


@mcp.tool()
async def route_monitor_start(
    origin: str,
    destination: str,
    route_stops: list[str],
    original_eta_minutes: float,
    vehicle_profile: str | None = None,
) -> str:
    """Register an active route for continuous monitoring and ETA drift detection."""
    res = await builtin.route_monitor_start(
        origin=origin,
        destination=destination,
        route_stops=route_stops,
        original_eta_minutes=original_eta_minutes,
        vehicle_profile=vehicle_profile,
    )
    return json.dumps(res, indent=2)


@mcp.tool()
async def route_monitor_status() -> str:
    """Retrieve active monitoring status and event log for all tracked routes."""
    res = await builtin.route_monitor_status()
    return json.dumps(res, indent=2)


@mcp.tool()
async def route_monitor_poll(route_id: str) -> str:
    """Poll current network and traffic conditions for a monitored route."""
    res = await builtin.route_monitor_poll(route_id)
    return json.dumps(res, indent=2)


@mcp.tool()
async def generate_realtime_incidents() -> str:
    """Generate fresh real-time incident data and reload directly into Neo4j."""
    res = await builtin.generate_realtime_incidents()
    return json.dumps(res, indent=2)


@mcp.tool()
async def evaluate_constraints(
    label: str,
    stops: list[str],
    vehicle_profile: str | None = None,
    payload_weight_kg: float | None = None,
    payload_volume_m3: float | None = None,
) -> str:
    """Evaluate 19 enterprise logistics and vehicle constraints against a proposed route."""
    res = await builtin.evaluate_constraints(
        label=label,
        stops=stops,
        vehicle_profile=vehicle_profile,
        payload_weight_kg=payload_weight_kg,
        payload_volume_m3=payload_volume_m3,
    )
    return json.dumps(res, indent=2)


@mcp.tool()
async def algorithm_list() -> str:
    """List all deterministic graph pathfinding algorithms (Dijkstra, A*, Yen's K-Shortest) and selection criteria."""
    res = await builtin.algorithm_list()
    return json.dumps(res, indent=2)


@mcp.tool()
async def document_search(query: str, limit: int = 5) -> str:
    """Keyword search across enterprise documents (SOPs, policies, manifests)."""
    res = await builtin.document_search(query, limit=limit)
    return json.dumps(res, indent=2)


@mcp.tool()
async def web_search(query: str, limit: int = 5) -> str:
    """Public web search for external advisories, notices and regulations."""
    res = await builtin.web_search(query, limit=limit)
    return json.dumps(res, indent=2)


@mcp.tool()
async def weather_lookup(location: str) -> str:
    """Current conditions and 3-day outlook for a city, hub or depot — route risk input."""
    res = await builtin.weather_lookup(location)
    return json.dumps(res, indent=2)


@mcp.tool()
async def maps_route(origin: str, destination: str) -> str:
    """Driving distance, duration and alternatives between two locations."""
    res = await builtin.maps_route(origin, destination)
    return json.dumps(res, indent=2)


@mcp.tool()
async def sql_query(query: str, parameters: list[Any] | None = None) -> str:
    """Read-only SQL query against the configured relational source (ERP/WMS extract)."""
    res = await builtin.sql_query(query, parameters)
    return json.dumps(res, indent=2)


@mcp.tool()
async def rest_get(url: str, params: dict[str, Any] | None = None) -> str:
    """GET an allow-listed internal or partner REST endpoint."""
    res = await builtin.rest_get(url, params)
    return json.dumps(res, indent=2)


@mcp.tool()
async def file_list(subdirectory: str = "") -> str:
    """List files in the enterprise document sandbox."""
    res = await builtin.file_list(subdirectory)
    return json.dumps(res, indent=2)


@mcp.tool()
async def file_read(path: str, max_chars: int = 20000) -> str:
    """Read a document from the enterprise document sandbox."""
    res = await builtin.file_read(path, max_chars=max_chars)
    return json.dumps(res, indent=2)


@mcp.tool()
async def snap_to_roads(path: list[list[float]], interpolate: bool = True) -> str:
    """Snap a recorded vehicle GPS trace onto the road network, removing scatter."""
    res = await builtin.snap_to_roads(path, interpolate=interpolate)
    return json.dumps(res, indent=2)


@mcp.tool()
async def road_geometry(
    origin: str, destination: str, waypoints: list[str] | None = None
) -> str:
    """Driving polyline geometry between two places from Google Directions API."""
    res = await builtin.road_geometry(origin, destination, waypoints=waypoints)
    return json.dumps(res, indent=2)


def run_mcp_server(
    transport: str = "streamable-http",
    host: str = "localhost",
    port: int = 8020,
    path: str = "/mcp_server",
) -> None:
    """Run the FastMCP server with configured transport."""
    mcp.run(transport=transport, host=host, port=port, path=path)


if __name__ == "__main__":
    run_mcp_server()
