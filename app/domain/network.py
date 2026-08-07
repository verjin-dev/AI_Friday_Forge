from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.kg.client import get_kg_client


logger = get_logger(__name__)


#: Incident severities that make a location impassable when the incident is
#: Active. Medium incidents are advisory — they raise risk, not a hard block.
#: Severities that make a location impassable when the incident is Active.
#:
#: Only Critical blocks by default. A High-severity accident or waterlogging
#: causes significant delay, but the road is still passable — treating it as a
#: hard block declares most of a busy network unreachable, which is both
#: operationally wrong and useless to a dispatcher. High and below feed the
#: delay model as advisories instead. Override via BLOCKING_SEVERITIES in the
#: environment if your operation is stricter.
BLOCKING_SEVERITIES = {
    value.strip().lower()
    for value in (settings.blocking_severities or ["critical"])
}
ADVISORY_SEVERITIES = {"high", "medium", "low"} - BLOCKING_SEVERITIES

#: Only Active incidents affect routing. "Planned" and "Resolved" are recorded
#: in the graph but do not constrain a journey happening now.
_ACTIVE = "active"

#: Safety valve for the alternate-path enumeration. The guaranteed shortest
#: path comes from Dijkstra, which is unaffected by these limits — they only
#: bound how hard the search works to find *additional* distinct routes.
_MAX_PATHS_EXPLORED = 60000
_MAX_HOPS = 30


class Incident(BaseModel):
    incident_id: str
    type: str = ""
    severity: str = ""
    status: str = ""
    location: str = ""

    @property
    def is_active(self) -> bool:
        return self.status.strip().lower() == _ACTIVE

    @property
    def is_blocking(self) -> bool:
        return self.is_active and self.severity.strip().lower() in BLOCKING_SEVERITIES

    def describe(self) -> str:
        return (
            f"{self.severity} {self.type} at {self.location} "
            f"({self.incident_id}, {self.status})"
        )


class Leg(BaseModel):
    """One traversal between two adjacent locations, sourced from the graph."""

    from_location: str
    to_location: str
    distance_km: float
    road_name: str = ""
    kind: str = "primary"  # "primary" (CONNECTED_TO) or "alternate" (ALTERNATE_ROUTE)
    via: str | None = None

    def describe(self) -> str:
        if self.kind == "alternate":
            return (
                f"{self.from_location} → {self.to_location} via {self.via} "
                f"({self.distance_km:.0f} km, alternate route)"
            )
        return (
            f"{self.from_location} → {self.to_location} on {self.road_name or 'road'} "
            f"({self.distance_km:.0f} km)"
        )


class RoutePath(BaseModel):
    """A complete origin-to-destination path, verified against the graph."""

    label: str = ""
    stops: list[str] = Field(default_factory=list)
    legs: list[Leg] = Field(default_factory=list)
    total_distance_km: float = 0.0
    uses_alternate: bool = False

    # --- verification results (populated by the network, never by the model) ---
    legs_verified: bool = True
    #: Blocking incidents at intermediate stops — these disqualify the route.
    blocking_incidents: list[Incident] = Field(default_factory=list)
    #: Blocking incidents at the origin or destination. A route cannot avoid its
    #: own endpoints, so these are disclosed as warnings rather than treated as
    #: a feasibility failure.
    endpoint_incidents: list[Incident] = Field(default_factory=list)
    advisory_incidents: list[Incident] = Field(default_factory=list)

    @property
    def is_clear(self) -> bool:
        return self.legs_verified and not self.blocking_incidents

    def describe(self) -> str:
        path = " → ".join(self.stops)
        detail = f"{path} · {self.total_distance_km:.0f} km"
        if self.uses_alternate:
            detail += " · uses alternate route"
        if self.blocking_incidents:
            detail += (
                " · BLOCKED by "
                + "; ".join(item.describe() for item in self.blocking_incidents)
            )
        if self.endpoint_incidents:
            detail += (
                " · endpoint warning: "
                + "; ".join(item.describe() for item in self.endpoint_incidents)
            )
        if self.advisory_incidents:
            detail += (
                " · advisory: "
                + "; ".join(item.describe() for item in self.advisory_incidents)
            )
        return detail


@dataclass(slots=True)
class RoadNetwork:
    """In-memory projection of the road graph, loaded from Neo4j.

    Pathfinding is deterministic and runs entirely over graph data — the LLM
    never proposes a road that does not exist.
    """

    locations: dict[str, dict[str, Any]] = field(default_factory=dict)
    adjacency: dict[str, list[Leg]] = field(default_factory=dict)
    alternates: list[Leg] = field(default_factory=list)
    incidents_by_location: dict[str, list[Incident]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def resolve(self, name: str) -> str | None:
        """Case-insensitive / partial location name resolution."""

        if not name:
            return None
        target = name.strip().lower()
        for known in self.locations:
            if known.lower() == target:
                return known
        matches = [known for known in self.locations if target in known.lower()]
        if len(matches) == 1:
            return matches[0]
        for known in self.locations:
            if known.lower().startswith(target):
                return known
        return None

    def blocking_at(self, location: str) -> list[Incident]:
        return [
            incident
            for incident in self.incidents_by_location.get(location, [])
            if incident.is_blocking
        ]

    def advisory_at(self, location: str) -> list[Incident]:
        return [
            incident
            for incident in self.incidents_by_location.get(location, [])
            if incident.is_active and not incident.is_blocking
        ]

    def leg_exists(self, origin: str, destination: str) -> Leg | None:
        for leg in self.adjacency.get(origin, []):
            if leg.to_location == destination:
                return leg
        return None

    # ------------------------------------------------------------------
    def dijkstra(self, origin: str, destination: str) -> RoutePath | None:
        """The shortest path, guaranteed to be found if one exists.

        The enumeration below is bounded so it cannot run away on a large
        network, which means it can miss a long route. This does not: if the
        two locations are connected at all, this returns a path.
        """

        if origin not in self.adjacency or destination not in self.adjacency:
            return None
        if origin == destination:
            return None

        best: dict[str, float] = {origin: 0.0}
        previous: dict[str, tuple[str, Leg]] = {}
        queue: list[tuple[float, str]] = [(0.0, origin)]
        settled: set[str] = set()

        while queue:
            distance, node = heapq.heappop(queue)
            if node in settled:
                continue
            settled.add(node)
            if node == destination:
                break

            for leg in self.adjacency.get(node, []):
                candidate = distance + leg.distance_km
                if candidate < best.get(leg.to_location, float("inf")):
                    best[leg.to_location] = candidate
                    previous[leg.to_location] = (node, leg)
                    heapq.heappush(queue, (candidate, leg.to_location))

        if destination not in best:
            return None

        stops: list[str] = [destination]
        legs: list[Leg] = []
        cursor = destination
        while cursor != origin:
            parent, leg = previous[cursor]
            legs.append(leg)
            stops.append(parent)
            cursor = parent

        stops.reverse()
        legs.reverse()
        return RoutePath(
            stops=stops,
            legs=legs,
            total_distance_km=round(best[destination], 2),
            uses_alternate=any(leg.kind == "alternate" for leg in legs),
        )

    def shortest_paths(
        self, origin: str, destination: str, *, k: int = 4
    ) -> list[RoutePath]:
        """Enumerate the k shortest simple paths using only CONNECTED_TO edges."""

        if origin not in self.adjacency and origin not in self.locations:
            return []

        found: list[RoutePath] = []
        # (cumulative distance, sequence counter, stops, legs)
        counter = 0
        queue: list[tuple[float, int, list[str], list[Leg]]] = [
            (0.0, counter, [origin], [])
        ]
        explored = 0

        while queue and len(found) < k and explored < _MAX_PATHS_EXPLORED:
            distance, _, stops, legs = heapq.heappop(queue)
            explored += 1
            current = stops[-1]

            if current == destination and legs:
                found.append(
                    RoutePath(
                        stops=list(stops),
                        legs=list(legs),
                        total_distance_km=round(distance, 2),
                        uses_alternate=any(leg.kind == "alternate" for leg in legs),
                    )
                )
                continue

            if len(stops) > _MAX_HOPS:
                continue

            for leg in self.adjacency.get(current, []):
                if leg.to_location in stops:
                    continue  # simple paths only — no cycles
                counter += 1
                heapq.heappush(
                    queue,
                    (
                        distance + leg.distance_km,
                        counter,
                        [*stops, leg.to_location],
                        [*legs, leg],
                    ),
                )

        return found

    def attach_alternates(self) -> None:
        """Promote ALTERNATE_ROUTE relationships into traversable edges.

        ``extra_distance`` is defined relative to the primary route, so an
        alternate's true length is the primary shortest distance between its
        endpoints plus that extra. Once resolved, alternates become ordinary
        legs, which lets pathfinding use them mid-route — the diversion around
        one blocked town inside a much longer corridor.
        """

        resolved: list[tuple[Leg, float]] = []
        for alternate in self.alternates:
            anchor = self.dijkstra(alternate.from_location, alternate.to_location)
            primary = [anchor] if anchor else []
            if not primary:
                # Nothing to measure the detour against; skip rather than guess.
                logger.warning(
                    "Alternate route has no primary path to anchor against",
                    extra={
                        "from": alternate.from_location,
                        "to": alternate.to_location,
                    },
                )
                continue
            resolved.append(
                (alternate, primary[0].total_distance_km + alternate.distance_km)
            )

        for alternate, total in resolved:
            for source, target in (
                (alternate.from_location, alternate.to_location),
                (alternate.to_location, alternate.from_location),
            ):
                self.adjacency.setdefault(source, []).append(
                    Leg(
                        from_location=source,
                        to_location=target,
                        distance_km=round(total, 2),
                        kind="alternate",
                        via=alternate.via,
                        road_name=alternate.via or "",
                    )
                )

    # ------------------------------------------------------------------
    def annotate(self, path: RoutePath) -> RoutePath:
        """Attach incident verdicts for every location the path touches.

        Endpoints are separated from intermediate stops: you can route around a
        blocked town in the middle, but not around where you are starting or
        where you must arrive.
        """

        blocking: list[Incident] = []
        endpoint: list[Incident] = []
        advisory: list[Incident] = []

        for index, stop in enumerate(path.stops):
            resolved = self.resolve(stop) or stop
            is_endpoint = index == 0 or index == len(path.stops) - 1
            if is_endpoint:
                endpoint.extend(self.blocking_at(resolved))
            else:
                blocking.extend(self.blocking_at(resolved))
            advisory.extend(self.advisory_at(resolved))

        path.blocking_incidents = blocking
        path.endpoint_incidents = endpoint
        path.advisory_incidents = advisory
        return path

    def verify_legs(self, stops: list[str]) -> tuple[bool, list[Leg], list[str]]:
        """Confirm a proposed stop sequence corresponds to real graph edges."""

        legs: list[Leg] = []
        missing: list[str] = []
        for origin, destination in zip(stops, stops[1:]):
            resolved_from = self.resolve(origin) or origin
            resolved_to = self.resolve(destination) or destination
            leg = self.leg_exists(resolved_from, resolved_to)
            if leg is None:
                missing.append(f"{resolved_from} → {resolved_to}")
            else:
                legs.append(leg)
        return (not missing), legs, missing

    def plan(
        self, origin: str, destination: str, *, k: int = 4
    ) -> list[RoutePath]:
        """All candidate routes between two locations, annotated and ranked.

        Clear routes rank ahead of blocked ones; within each group, shorter
        distance wins. Blocked routes are retained so the platform can explain
        *why* they were rejected rather than silently omitting them.
        """

        resolved_origin = self.resolve(origin)
        resolved_destination = self.resolve(destination)
        if not resolved_origin or not resolved_destination:
            return []

        candidates = self.shortest_paths(resolved_origin, resolved_destination, k=k)

        # Guarantee at least the true shortest path even when the bounded
        # enumeration above gave up on a long corridor.
        guaranteed = self.dijkstra(resolved_origin, resolved_destination)
        if guaranteed and not any(
            path.stops == guaranteed.stops for path in candidates
        ):
            candidates.append(guaranteed)

        seen: set[tuple] = set()
        unique: list[RoutePath] = []
        for path in candidates:
            key = (
                tuple(path.stops),
                tuple(leg.kind for leg in path.legs),
                tuple(leg.via or "" for leg in path.legs),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(self.annotate(path))

        unique.sort(key=lambda p: (not p.is_clear, p.total_distance_km))
        for index, path in enumerate(unique, start=1):
            path.label = f"Route {index}: {self._label_for(path)}"
        return unique

    def _label_for(self, path: RoutePath) -> str:
        detours = [leg.via for leg in path.legs if leg.kind == "alternate" and leg.via]
        if detours:
            via = f" via {', '.join(dict.fromkeys(detours))}"
        elif len(path.stops) > 2:
            via = f" via {', '.join(path.stops[1:-1])}"
        else:
            via = " direct"
        return (
            f"{path.stops[0]} → {path.stops[-1]}{via} "
            f"({path.total_distance_km:.0f} km)"
        )


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------
async def load_network() -> RoadNetwork:
    """Project the road network out of Neo4j.

    Reads the schema exactly as delivered: ``(:Location)-[:CONNECTED_TO]->``,
    ``(:Incident)-[:HAS_INCIDENT]->(:Location)`` and ``[:ALTERNATE_ROUTE]``.
    """

    client = get_kg_client()
    network = RoadNetwork()

    location_rows = await client.try_run(
        "MATCH (l:Location) RETURN l.name AS name, l.location_id AS location_id, "
        "l.type AS type, l.is_near_tvm AS is_near_tvm"
    )
    for row in location_rows:
        name = row.get("name")
        if not name:
            continue
        network.locations[name] = {
            "location_id": row.get("location_id"),
            "type": row.get("type"),
            "is_near_tvm": row.get("is_near_tvm"),
        }
        network.adjacency.setdefault(name, [])

    road_rows = await client.try_run(
        "MATCH (a:Location)-[r:CONNECTED_TO]->(b:Location) "
        "RETURN a.name AS source, b.name AS target, "
        "r.distance_km AS distance_km, r.road_name AS road_name"
    )
    for row in road_rows:
        source, target = row.get("source"), row.get("target")
        if not source or not target:
            continue
        try:
            distance = float(row.get("distance_km") or 0.0)
        except (TypeError, ValueError):
            distance = 0.0
        leg = Leg(
            from_location=source,
            to_location=target,
            distance_km=distance,
            road_name=row.get("road_name") or "",
        )
        network.adjacency.setdefault(source, []).append(leg)
        # Roads are traversable both ways unless the graph says otherwise.
        network.adjacency.setdefault(target, []).append(
            Leg(
                from_location=target,
                to_location=source,
                distance_km=distance,
                road_name=leg.road_name,
            )
        )

    alternate_rows = await client.try_run(
        "MATCH (a:Location)-[r:ALTERNATE_ROUTE]->(b:Location) "
        "RETURN a.name AS source, b.name AS target, "
        "r.via AS via, r.extra_distance AS extra_distance"
    )
    for row in alternate_rows:
        source, target = row.get("source"), row.get("target")
        if not source or not target:
            continue
        try:
            extra = float(row.get("extra_distance") or 0.0)
        except (TypeError, ValueError):
            extra = 0.0
        network.alternates.append(
            Leg(
                from_location=source,
                to_location=target,
                distance_km=extra,
                kind="alternate",
                via=row.get("via"),
            )
        )

    incident_rows = await client.try_run(
        "MATCH (i:Incident)-[:HAS_INCIDENT]->(l:Location) "
        "RETURN i.incident_id AS incident_id, i.type AS type, "
        "i.severity AS severity, i.status AS status, l.name AS location"
    )
    for row in incident_rows:
        location = row.get("location")
        if not location:
            continue
        incident = Incident(
            incident_id=row.get("incident_id") or "",
            type=row.get("type") or "",
            severity=row.get("severity") or "",
            status=row.get("status") or "",
            location=location,
        )
        network.incidents_by_location.setdefault(location, []).append(incident)

    network.attach_alternates()

    logger.info(
        "Road network loaded",
        extra={
            "locations": len(network.locations),
            "roads": sum(len(legs) for legs in network.adjacency.values()) // 2,
            "alternates": len(network.alternates),
            "incidents": sum(
                len(items) for items in network.incidents_by_location.values()
            ),
        },
    )
    return network


def describe_paths(paths: Iterable[RoutePath]) -> str:
    lines = [path.describe() for path in paths]
    return "\n".join(f"  - {line}" for line in lines) if lines else "  (no routes found)"
