"""Temporary graph modifications for live conditions.

Phase 7 requires live incidents to change routing weights without corrupting
the historical record. Writing an inflated `distance_km` back to Neo4j would do
exactly that — the next person to query the graph would get a number that was
never true of the road.

So the overlay is a **read-time projection**. The stored graph stays pristine;
an overlay carries the disabled edges and added penalties, each with an expiry,
and a projection applies them as the search traverses. Clearing an incident is
just dropping its entry — there is nothing to restore.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger(__name__)

Edge = tuple[str, str]

#: Extra effective-minutes applied to every edge touching a location with an
#: active incident of this severity. Blocking severities disable instead.
SEVERITY_PENALTY_MINUTES: dict[str, float] = {
    "critical": 90.0,
    "high": 30.0,
    "medium": 12.0,
    "low": 4.0,
}


@dataclass(slots=True)
class OverlayEntry:
    reason: str
    penalty_minutes: float = 0.0
    disabled: bool = False
    expires_at: float | None = None
    source: str = "incident"

    def active(self, now: float) -> bool:
        return self.expires_at is None or self.expires_at > now


@dataclass(slots=True)
class GraphOverlay:
    """Live, expiring modifications layered over the stored graph."""

    edges: dict[Edge, OverlayEntry] = field(default_factory=dict)
    nodes: dict[str, OverlayEntry] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def disable_edge(self, source: str, target: str, reason: str, ttl: float | None = None) -> None:
        self.edges[(source, target)] = OverlayEntry(
            reason=reason,
            disabled=True,
            expires_at=None if ttl is None else time.monotonic() + ttl,
        )

    def penalise_edge(
        self, source: str, target: str, minutes: float, reason: str, ttl: float | None = None
    ) -> None:
        self.edges[(source, target)] = OverlayEntry(
            reason=reason,
            penalty_minutes=minutes,
            expires_at=None if ttl is None else time.monotonic() + ttl,
        )

    def penalise_node(
        self, node: str, minutes: float, reason: str, ttl: float | None = None
    ) -> None:
        self.nodes[node] = OverlayEntry(
            reason=reason,
            penalty_minutes=minutes,
            expires_at=None if ttl is None else time.monotonic() + ttl,
        )

    def disable_node(self, node: str, reason: str, ttl: float | None = None) -> None:
        self.nodes[node] = OverlayEntry(
            reason=reason,
            disabled=True,
            expires_at=None if ttl is None else time.monotonic() + ttl,
        )

    def clear(self, *, node: str | None = None, edge: Edge | None = None) -> None:
        """Incident cleared — drop the entry. Nothing needs restoring."""

        if node is not None:
            self.nodes.pop(node, None)
        if edge is not None:
            self.edges.pop(edge, None)

    def prune(self) -> int:
        now = time.monotonic()
        before = len(self.edges) + len(self.nodes)
        self.edges = {key: entry for key, entry in self.edges.items() if entry.active(now)}
        self.nodes = {key: entry for key, entry in self.nodes.items() if entry.active(now)}
        return before - (len(self.edges) + len(self.nodes))

    # ------------------------------------------------------------------
    def edge_state(self, source: str, target: str) -> OverlayEntry | None:
        now = time.monotonic()
        entry = self.edges.get((source, target))
        if entry and entry.active(now):
            return entry
        return None

    def node_state(self, node: str) -> OverlayEntry | None:
        now = time.monotonic()
        entry = self.nodes.get(node)
        if entry and entry.active(now):
            return entry
        return None

    @property
    def is_empty(self) -> bool:
        return not self.edges and not self.nodes

    def describe(self) -> list[str]:
        now = time.monotonic()
        lines = [
            f"node {node} {'disabled' if entry.disabled else f'+{entry.penalty_minutes:.0f} min'}: {entry.reason}"
            for node, entry in self.nodes.items()
            if entry.active(now)
        ]
        lines += [
            f"edge {a} -> {b} {'disabled' if entry.disabled else f'+{entry.penalty_minutes:.0f} min'}: {entry.reason}"
            for (a, b), entry in self.edges.items()
            if entry.active(now)
        ]
        return lines


def overlay_from_incidents(
    incidents_by_location: dict[str, list[Any]],
    *,
    blocking_severities: Iterable[str] | None = None,
) -> GraphOverlay:
    """Project active incidents onto the graph as penalties and disablements.

    A blocking incident disables the location; anything else adds a
    severity-scaled penalty so the search prefers to route around it without
    being forbidden from passing through.
    """

    blocking = {
        value.strip().lower()
        for value in (blocking_severities or settings.blocking_severities or ["critical"])
    }

    overlay = GraphOverlay()
    for location, incidents in incidents_by_location.items():
        for incident in incidents:
            if not getattr(incident, "is_active", False):
                continue
            severity = (getattr(incident, "severity", "") or "").strip().lower()
            reason = (
                f"{getattr(incident, 'severity', '?')} "
                f"{getattr(incident, 'type', 'incident')} at {location} "
                f"({getattr(incident, 'incident_id', '?')})"
            )
            if severity in blocking:
                overlay.disable_node(location, reason)
            else:
                overlay.penalise_node(
                    location, SEVERITY_PENALTY_MINUTES.get(severity, 5.0), reason
                )

    if not overlay.is_empty:
        logger.debug(
            "Incident overlay built",
            extra={"nodes": len(overlay.nodes), "edges": len(overlay.edges)},
        )
    return overlay


class GraphProjection:
    """A :class:`~app.routing.strategies.GraphView` with an overlay applied.

    Disabled elements are hidden from traversal entirely; penalised ones are
    surfaced through :meth:`penalty_for` so the cost model adds them without the
    stored edge ever changing.
    """

    def __init__(
        self,
        network: Any,
        overlay: GraphOverlay | None = None,
        coordinates: dict[str, dict[str, float]] | None = None,
        *,
        protected_nodes: Iterable[str] = (),
    ) -> None:
        self.network = network
        self.overlay = overlay or GraphOverlay()
        self._coordinates = coordinates or {}
        # Origin and destination are never hidden: you cannot route around
        # where you already are or where you must arrive.
        self.protected = set(protected_nodes)

    # --- GraphView -----------------------------------------------------
    def nodes(self):
        return self.network.locations.keys()

    def neighbours(self, node: str):
        for leg in self.network.adjacency.get(node, []):
            target = leg.to_location
            if target in self.protected:
                yield leg
                continue
            node_state = self.overlay.node_state(target)
            if node_state and node_state.disabled:
                continue
            edge_state = self.overlay.edge_state(node, target)
            if edge_state and edge_state.disabled:
                continue
            yield leg

    def coordinates(self, node: str) -> tuple[float, float] | None:
        point = self._coordinates.get(node)
        if not point:
            return None
        latitude, longitude = point.get("latitude"), point.get("longitude")
        if latitude is None or longitude is None:
            return None
        return float(latitude), float(longitude)

    # --- overlay -------------------------------------------------------
    def penalty_for(self, source: str, target: str) -> float:
        total = 0.0
        edge_state = self.overlay.edge_state(source, target)
        if edge_state:
            total += edge_state.penalty_minutes
        node_state = self.overlay.node_state(target)
        if node_state and target not in self.protected:
            total += node_state.penalty_minutes
        return total

    def blocked_reasons(self, stops: Iterable[str]) -> list[str]:
        reasons = []
        for stop in stops:
            entry = self.overlay.node_state(stop)
            if entry and entry.disabled:
                reasons.append(entry.reason)
        return reasons


class OverlayPersistence:
    @staticmethod
    async def sync_to_graph(overlay: GraphOverlay, kg_client: Any) -> int:
        count = 0
        from datetime import datetime, timezone
        for node_id, entry in overlay.nodes.items():
            entry_id = f"node_{node_id}_{int(time.time())}"
            expires_at_iso = datetime.fromtimestamp(entry.expires_at, tz=timezone.utc).isoformat() if entry.expires_at else None
            
            query = """
            MERGE (e:OverlayEntry {entry_id: $entry_id})
            SET e.reason = $reason, e.penalty_minutes = $penalty, e.disabled = $disabled,
                e.expires_at = CASE WHEN $expires_at IS NOT NULL THEN datetime($expires_at) ELSE NULL END, 
                e.source = $source, e.created_at = datetime()
            MERGE (loc:Location {name: $node_id})
            MERGE (e)-[:OVERLAY_AFFECTS]->(loc)
            """
            
            await kg_client.execute(query, {
                "entry_id": entry_id,
                "reason": entry.reason,
                "penalty": entry.penalty_minutes,
                "disabled": entry.disabled,
                "expires_at": expires_at_iso,
                "source": entry.source,
                "node_id": node_id
            })
            count += 1
            
        return count

    @staticmethod
    async def load_from_graph(kg_client: Any) -> GraphOverlay:
        query = """
        MATCH (e:OverlayEntry)
        WHERE e.expires_at IS NULL OR e.expires_at > datetime()
        OPTIONAL MATCH (e)-[:OVERLAY_AFFECTS]->(loc:Location)
        RETURN e, loc.name AS location
        """
        
        records = await kg_client.execute(query)
        overlay = GraphOverlay()
        
        for record in records:
            e = record["e"]
            loc = record["location"]
            if not loc:
                continue
                
            entry = OverlayEntry(
                reason=e.get("reason", "unknown"),
                penalty_minutes=e.get("penalty_minutes", 0.0),
                disabled=e.get("disabled", False),
                expires_at=e.get("expires_at").to_native().timestamp() if e.get("expires_at") else None,
                source=e.get("source", "graph")
            )
            
            overlay.nodes[loc] = entry
            
        return overlay

    @staticmethod
    async def cleanup_expired(kg_client: Any) -> int:
        query = """
        MATCH (e:OverlayEntry)
        WHERE e.expires_at IS NOT NULL AND e.expires_at <= datetime()
        DETACH DELETE e
        RETURN count(e) as removed_count
        """
        records = await kg_client.execute(query)
        if records:
            return records[0]["removed_count"]
        return 0
