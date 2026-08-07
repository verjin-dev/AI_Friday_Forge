from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.agents.optimization import _path_to_candidate
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.constraints import evaluate_candidate, get_constraint_profile
from app.domain.fleet import apply_profile, get_profile, profile_constraint_overrides
from app.domain.delay import model_card, predict_with_live_traffic
from app.domain.geo import resolve_all
from app.domain.network import load_network
from app.kg.client import get_kg_client
from app.observability.kpis import record_decision
from app.routing import (
    ReplanRequest,
    VehicleContext,
    get_replanner,
    get_routing_engine,
    get_strategy_factory,
)
from app.mcp.builtin import weather_lookup


logger = get_logger(__name__)

router = APIRouter(prefix="/api/routes", tags=["routing"])


class ScenarioRequest(BaseModel):
    """Toggle an incident to demonstrate dynamic re-planning."""

    incident_id: str
    status: str = Field(description="Active or Inactive")


def _parse_departure(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _network_payload() -> dict[str, Any]:
    network = await load_network()
    names = sorted(network.locations)
    coordinates = await resolve_all(names, network.locations)

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for source, legs in network.adjacency.items():
        for leg in legs:
            key = tuple(sorted([source, leg.to_location])) + (leg.kind,)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                {
                    "from": source,
                    "to": leg.to_location,
                    "distance_km": leg.distance_km,
                    "road_name": leg.road_name,
                    "kind": leg.kind,
                    "via": leg.via,
                }
            )

    incidents = [
        {
            "incident_id": incident.incident_id,
            "type": incident.type,
            "severity": incident.severity,
            "status": incident.status,
            "location": incident.location,
            "is_active": incident.is_active,
            "is_blocking": incident.is_blocking,
        }
        for items in network.incidents_by_location.values()
        for incident in items
    ]

    return {
        "locations": [
            {
                "name": name,
                "type": network.locations[name].get("type"),
                "is_near_tvm": network.locations[name].get("is_near_tvm"),
                **(coordinates.get(name) or {}),
                "has_coordinates": name in coordinates,
                "incidents": [
                    item.incident_id
                    for item in network.incidents_by_location.get(name, [])
                    if item.is_active
                ],
            }
            for name in names
        ],
        "edges": edges,
        "incidents": incidents,
        "blocked_locations": sorted(
            {item["location"] for item in incidents if item["is_blocking"]}
        ),
    }


@router.get("/network")
async def network() -> dict[str, Any]:
    """Base map data: locations with coordinates, road edges and incidents."""

    return await _network_payload()


@router.get("/model-card")
async def delay_model_card() -> dict[str, Any]:
    """Transparency record for the delay prediction model."""

    return model_card()


@router.get("/plan")
async def plan(
    origin: str = Query(description="Start location name."),
    destination: str = Query(description="End location name."),
    departure: str | None = Query(default=None, description="ISO departure time."),
    include_weather: bool = Query(default=True),
    max_routes: int = Query(default=4, ge=1, le=8),
    profile: str | None = Query(
        default=None,
        description="Vehicle profile id (e.g. P001) — activates the fleet constraints.",
    ),
    payload_weight_kg: float | None = Query(default=None),
    payload_volume_m3: float | None = Query(default=None),
    algorithm: str | None = Query(
        default=None, description="auto | dijkstra | astar | yen"
    ),
) -> dict[str, Any]:
    """Plan routes with hard-constraint verdicts and delay predictions.

    This is the deterministic path the map UI calls directly — same engine the
    Optimization Agent uses, without the LLM in the loop.
    """

    network_model = await load_network()
    if not network_model.locations:
        raise HTTPException(
            status_code=503,
            detail="Road network is empty — load the CSVs with scripts/load_graph.py.",
        )

    resolved_origin = network_model.resolve(origin)
    resolved_destination = network_model.resolve(destination)
    if not resolved_origin or not resolved_destination:
        unknown = origin if not resolved_origin else destination
        raise HTTPException(
            status_code=404,
            detail=(
                f"Location '{unknown}' is not in the road network. "
                f"Known: {', '.join(sorted(network_model.locations))}"
            ),
        )

    paths = network_model.plan(
        resolved_origin, resolved_destination, k=max_routes
    )

    weather: dict[str, Any] | None = None
    if include_weather:
        try:
            weather = await weather_lookup(resolved_destination)
        except Exception as exc:  # noqa: BLE001 - weather is optional context
            logger.info("Weather lookup failed", extra={"error": str(exc)[:160]})

    vehicle = get_profile(profile)
    if profile and vehicle is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown vehicle profile '{profile}'."
        )

    constraint_profile = (
        profile_constraint_overrides(vehicle) if vehicle else get_constraint_profile()
    )
    departure_at = _parse_departure(departure)

    coordinates = await resolve_all(
        sorted(network_model.locations), network_model.locations
    )

    # Phases 1–3: the deterministic engine generates the candidates. Google is
    # not consulted until after this point, and only to enrich what the graph
    # has already chosen — it never selects the corridor.
    engine_report = None
    if settings.routing_engine_drives_plan:
        engine_candidates, engine_report = get_routing_engine().plan(
            network_model,
            resolved_origin,
            resolved_destination,
            vehicle=VehicleContext.from_profile(vehicle),
            coordinates=coordinates,
            k=max_routes,
            algorithm=algorithm,
        )
        rebuilt = [
            path
            for path in (
                network_model.build_path(candidate.stops)
                for candidate in engine_candidates
            )
            if path is not None
        ]
        if rebuilt:
            paths = rebuilt

    # Live traffic per route, in parallel; failures degrade to the graph baseline.
    predictions = await predict_with_live_traffic(
        paths,
        weather=weather,
        departure=departure_at,
        coordinates=coordinates,
    )

    results: list[dict[str, Any]] = []
    for path, prediction in zip(paths, predictions):
        candidate = _path_to_candidate(path)
        # Transit time drives the driver-hours and SLA rules, so it must be set
        # before the profile is applied.
        candidate.duration_minutes = prediction.predicted_total_minutes
        if vehicle:
            apply_profile(
                candidate,
                vehicle,
                departure=departure_at,
                payload_weight_kg=payload_weight_kg,
                payload_volume_m3=payload_volume_m3,
            )
        report = evaluate_candidate(candidate, constraint_profile)

        results.append(
            {
                "label": path.label,
                "stops": path.stops,
                "coordinates": [
                    coordinates[stop] for stop in path.stops if stop in coordinates
                ],
                "legs": [
                    {
                        "from": leg.from_location,
                        "to": leg.to_location,
                        "distance_km": leg.distance_km,
                        "road_name": leg.road_name,
                        "kind": leg.kind,
                        "via": leg.via,
                        "description": leg.describe(),
                    }
                    for leg in path.legs
                ],
                "total_distance_km": path.total_distance_km,
                "uses_alternate": path.uses_alternate,
                "feasible": report.feasible,
                "constraint_report": report.model_dump(mode="json"),
                "hard_violations": [
                    check.detail for check in report.hard_violations
                ],
                "soft_violations": [
                    check.detail for check in report.soft_violations
                ],
                "delay": prediction.model_dump(mode="json"),
            }
        )

    feasible = [item for item in results if item["feasible"]]
    recommended = None
    if feasible:
        # Least predicted total time among compliant routes.
        recommended = min(
            feasible, key=lambda item: item["delay"]["predicted_total_minutes"]
        )

    payload = {
        "origin": resolved_origin,
        "destination": resolved_destination,
        "departure": departure_at.isoformat() if departure_at else None,
        "profile": vehicle.profile_id if vehicle else None,
        "vehicle": vehicle.label() if vehicle else None,
        "planning": engine_report.as_dict() if engine_report else None,
        "route_count": len(results),
        "feasible_count": len(feasible),
        "all_infeasible": bool(results) and not feasible,
        "recommended_label": recommended["label"] if recommended else None,
        "routes": results,
        "weather": weather,
        "alerts": _build_alerts(results),
    }

    record_decision(payload)
    return payload


def _build_alerts(routes: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Operator-facing alerts derived from the constraint and delay verdicts."""

    alerts: list[dict[str, str]] = []
    seen: set[str] = set()

    for route in routes:
        for detail in route["hard_violations"]:
            if detail not in seen:
                seen.add(detail)
                alerts.append({"level": "critical", "message": detail})

        delay = route["delay"]
        if delay["risk"] in ("high", "severe") and route["feasible"]:
            message = (
                f"{route['label']}: {delay['risk']} delay risk, "
                f"+{delay['predicted_delay_minutes']:.0f} min predicted"
            )
            if message not in seen:
                seen.add(message)
                alerts.append({"level": "warning", "message": message})

    if routes and not any(route["feasible"] for route in routes):
        alerts.insert(
            0,
            {
                "level": "critical",
                "message": (
                    "No compliant route exists between these locations under the "
                    "current incident state."
                ),
            },
        )

    return alerts


@router.post("/scenario")
async def scenario(request: ScenarioRequest) -> dict[str, Any]:
    """Activate or clear an incident, then show the re-planned network.

    This is an operator/demo control, deliberately outside the agent path: the
    Knowledge Agent stays read-only and only this endpoint may change incident
    state.
    """

    status = request.status.strip().capitalize()
    if status not in {"Active", "Inactive"}:
        raise HTTPException(
            status_code=400, detail="status must be 'Active' or 'Inactive'"
        )

    client = get_kg_client()
    rows = await client.run(
        "MATCH (i:Incident {incident_id: $id}) SET i.status = $status "
        "RETURN i.incident_id AS incident_id, i.type AS type, "
        "i.severity AS severity, i.status AS status",
        {"id": request.incident_id, "status": status},
    )
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"Incident '{request.incident_id}' not found."
        )

    logger.info(
        "Scenario applied",
        extra={"incident": request.incident_id, "status": status},
    )

    return {"incident": rows[0], "network": await _network_payload()}


@router.get("/locations")
async def locations() -> list[str]:
    network_model = await load_network()
    return sorted(network_model.locations)


# ----------------------------------------------------------------------
# Deterministic routing engine (graph algorithms, before any Google call)
# ----------------------------------------------------------------------
@router.get("/candidates")
async def candidates(
    origin: str = Query(description="Start location name."),
    destination: str = Query(description="End location name."),
    k: int = Query(default=4, ge=1, le=8, description="How many candidates."),
    algorithm: str | None = Query(
        default=None, description="auto | dijkstra | astar | yen"
    ),
    profile: str | None = Query(default=None, description="Vehicle profile id."),
    criticality: float = Query(default=0.5, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Top-K enterprise-scored candidates straight from the graph.

    This is the deterministic half of the pipeline in isolation — no Google,
    no LLM. Useful for verifying that routing is reproducible and for
    debugging why a particular corridor was or was not offered.
    """

    network_model = await load_network()
    if not network_model.locations:
        raise HTTPException(status_code=503, detail="Road network is empty.")

    resolved_origin = network_model.resolve(origin)
    resolved_destination = network_model.resolve(destination)
    if not resolved_origin or not resolved_destination:
        unknown = origin if not resolved_origin else destination
        raise HTTPException(
            status_code=404, detail=f"Location '{unknown}' is not in the road network."
        )

    vehicle = get_profile(profile)
    if profile and vehicle is None:
        raise HTTPException(status_code=404, detail=f"Unknown profile '{profile}'.")

    coordinates = await resolve_all(
        sorted(network_model.locations), network_model.locations
    )

    routes, report = get_routing_engine().plan(
        network_model,
        resolved_origin,
        resolved_destination,
        vehicle=VehicleContext.from_profile(vehicle, criticality),
        coordinates=coordinates,
        k=k,
        algorithm=algorithm,
    )

    return {
        "origin": resolved_origin,
        "destination": resolved_destination,
        "profile": vehicle.profile_id if vehicle else None,
        "planning": report.as_dict(),
        "candidates": [
            {**candidate.model_dump(mode="json"), "label": candidate.label}
            for candidate in routes
        ],
    }


@router.post("/replan")
async def replan(
    request: ReplanRequest,
    profile: str | None = Query(default=None),
    algorithm: str | None = Query(default=None),
) -> dict[str, Any]:
    """Segment-level replanning — keeps the driven prefix, replans the rest.

    Give it the route being driven plus what changed; it returns the smallest
    diversion that resolves the disruption, rather than rewriting the journey.
    """

    if not settings.enable_segment_replanning:
        raise HTTPException(
            status_code=503, detail="Segment replanning is disabled by feature flag."
        )

    network_model = await load_network()
    if not network_model.locations:
        raise HTTPException(status_code=503, detail="Road network is empty.")

    vehicle = get_profile(profile)
    coordinates = await resolve_all(
        sorted(network_model.locations), network_model.locations
    )

    outcome = get_replanner().replan(
        network_model,
        request,
        vehicle=VehicleContext.from_profile(vehicle),
        coordinates=coordinates,
        algorithm=algorithm,
    )
    payload = outcome.model_dump(mode="json")
    if outcome.route:
        payload["route"]["label"] = outcome.route.label
    return payload


@router.get("/algorithms")
async def algorithms() -> dict[str, Any]:
    """What the engine can do and how it chooses — for the UI and for auditors."""

    network_model = await load_network()
    node_count = len(network_model.locations)
    choice = get_strategy_factory().create(
        node_count=node_count, want_alternatives=True, has_coordinates=True
    )
    return {
        "graph_nodes": node_count,
        "configured": settings.routing_algorithm,
        "astar_node_threshold": settings.astar_node_threshold,
        "candidate_count": settings.route_candidate_count,
        "would_select": {"algorithm": choice.name, "reason": choice.reason},
        "available": [
            {
                "name": "dijkstra",
                "use": "exact shortest path; the reference result",
                "selected_when": "small graphs, or when no coordinates exist",
            },
            {
                "name": "astar",
                "use": "same optimum as Dijkstra, fewer expansions",
                "selected_when": f"graphs of {settings.astar_node_threshold}+ nodes with coordinates",
            },
            {
                "name": "yen",
                "use": "K loopless shortest paths for genuine alternatives",
                "selected_when": "more than one candidate is requested",
            },
        ],
        "feature_flags": {
            "incident_overlay": settings.enable_incident_overlay,
            "live_traffic_enrichment": settings.enable_live_traffic_enrichment,
            "segment_replanning": settings.enable_segment_replanning,
            "route_monitoring": settings.enable_route_monitoring,
        },
    }
