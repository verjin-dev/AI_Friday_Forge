"""Shared fixtures.

Every test here runs offline: no Neo4j, no LLM gateway, no Google APIs. The
road network is built in memory from the same shape ``load_network`` produces,
so the pathfinding and constraint logic is exercised exactly as in production.
"""

from __future__ import annotations

import pytest

from app.domain.network import Incident, Leg, RoadNetwork


#: Mirrors data/Data prepared by us/*.csv — the delivered Kerala corridor.
LOCATIONS = [
    "Kochi",
    "Alappuzha",
    "Haripad",
    "Kayamkulam",
    "Kollam",
    "Attingal",
    "Thiruvananthapuram",
]

ROADS = [
    ("Kochi", "Alappuzha", 54, "NH66"),
    ("Alappuzha", "Haripad", 28, "NH66"),
    ("Haripad", "Kayamkulam", 14, "NH66"),
    ("Kayamkulam", "Kollam", 42, "NH66"),
    ("Kollam", "Attingal", 30, "NH66"),
    ("Attingal", "Thiruvananthapuram", 32, "NH66"),
]

ALTERNATES = [
    ("Kayamkulam", "Kollam", "MC Road", 8),
    ("Kollam", "Thiruvananthapuram", "Kottarakkara", 15),
]

INCIDENTS = [
    ("I001", "Accident", "High", "Active", "Kayamkulam"),
    ("I002", "Heavy Rain", "Critical", "Active", "Kollam"),
    ("I003", "Road Work", "Medium", "Active", "Attingal"),
]


def build_network(
    *,
    incidents: list[tuple[str, str, str, str, str]] | None = None,
    attach_alternates: bool = True,
) -> RoadNetwork:
    network = RoadNetwork()

    for name in LOCATIONS:
        network.locations[name] = {"type": "City", "is_near_tvm": "Unknown"}
        network.adjacency[name] = []

    for source, target, distance, road in ROADS:
        network.adjacency[source].append(
            Leg(
                from_location=source,
                to_location=target,
                distance_km=distance,
                road_name=road,
            )
        )
        network.adjacency[target].append(
            Leg(
                from_location=target,
                to_location=source,
                distance_km=distance,
                road_name=road,
            )
        )

    for source, target, via, extra in ALTERNATES:
        network.alternates.append(
            Leg(
                from_location=source,
                to_location=target,
                distance_km=extra,
                kind="alternate",
                via=via,
            )
        )

    for incident_id, kind, severity, status, location in (
        incidents if incidents is not None else INCIDENTS
    ):
        network.incidents_by_location.setdefault(location, []).append(
            Incident(
                incident_id=incident_id,
                type=kind,
                severity=severity,
                status=status,
                location=location,
            )
        )

    if attach_alternates:
        network.attach_alternates()

    return network


@pytest.fixture
def network() -> RoadNetwork:
    """The delivered network with all three incidents active."""

    return build_network()


@pytest.fixture
def clear_network() -> RoadNetwork:
    """Same topology with every incident cleared."""

    return build_network(
        incidents=[
            (incident_id, kind, severity, "Inactive", location)
            for incident_id, kind, severity, _, location in INCIDENTS
        ]
    )
