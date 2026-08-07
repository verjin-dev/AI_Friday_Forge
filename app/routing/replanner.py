"""Segment-level replanning.

When a corridor blocks mid-journey, replacing the whole route is the wrong
answer: the vehicle has already covered part of it, and a full replan will
happily rewrite roads it has already driven. Phase 5 asks for the surgical
version instead.

    Warehouse -> A -> B -> C -> Destination,  B->C blocked
    keep       Warehouse..B
    replan     B -> Destination
    splice     the two halves back together

The kept prefix is frozen, the blocked edge is disabled in an overlay, and the
search is re-run only from the affected node. If the detour turns out worse
than a clean full replan by a configurable margin, that is reported so the
caller can choose — but the default is to stay close to the committed plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.routing.cost import VehicleContext
from app.routing.engine import (
    PlanningReport,
    RouteCandidate,
    RoutingEngine,
    get_routing_engine,
)
from app.routing.overlay import GraphOverlay, overlay_from_incidents


logger = get_logger(__name__)


class ReplanRequest(BaseModel):
    stops: list[str] = Field(description="The route currently being driven.")
    #: Where the vehicle actually is — everything before it is already driven.
    current_node: str | None = None
    blocked_edge: tuple[str, str] | None = None
    blocked_nodes: list[str] = Field(default_factory=list)
    reason: str = "condition change"


class ReplanOutcome(BaseModel):
    replanned: bool
    reason: str
    kept_prefix: list[str] = Field(default_factory=list)
    replanned_from: str | None = None
    route: RouteCandidate | None = None
    original_distance_km: float = 0.0
    new_distance_km: float = 0.0
    added_distance_km: float = 0.0
    segments_reused: int = 0
    segments_changed: int = 0
    report: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


@dataclass(slots=True)
class SegmentReplanner:
    """Replans the smallest suffix that resolves the disruption."""

    engine: RoutingEngine = field(default_factory=get_routing_engine)

    def replan(
        self,
        network: Any,
        request: ReplanRequest,
        *,
        vehicle: VehicleContext | None = None,
        coordinates: dict[str, dict[str, float]] | None = None,
        algorithm: str | None = None,
    ) -> ReplanOutcome:
        stops = [network.resolve(stop) or stop for stop in request.stops]
        if len(stops) < 2:
            return ReplanOutcome(
                replanned=False, reason="route has fewer than two stops"
            )

        destination = stops[-1]
        original_distance = self._distance(network, stops)

        # Endpoint protection exists so an incident cannot hide the place you
        # must reach. But an explicit caller-supplied block is different: it is
        # a statement that the node is impassable, and routing into it anyway
        # would be dishonest.
        blocked_nodes = {network.resolve(node) or node for node in request.blocked_nodes}
        if destination in blocked_nodes:
            return ReplanOutcome(
                replanned=False,
                reason=f"the destination {destination} is itself blocked",
                kept_prefix=stops,
                original_distance_km=original_distance,
                note="Dispatch must choose a different destination or wait for it to clear.",
            )

        # Where do we branch from? The vehicle's position if known, otherwise
        # the tail of the blocked edge — never earlier, because those roads are
        # already behind us.
        anchor = self._anchor(stops, request)
        if anchor is None:
            return ReplanOutcome(
                replanned=False,
                reason="the disruption is not on the current route",
                kept_prefix=stops,
            )

        index = stops.index(anchor)
        if anchor == destination:
            return ReplanOutcome(
                replanned=False,
                reason="the vehicle has already reached the destination",
                kept_prefix=stops,
            )

        overlay = overlay_from_incidents(network.incidents_by_location)
        self._apply_disruption(overlay, request)

        # Branch as late as possible so the most of the committed route is kept.
        # If nothing routes from there, walk the branch point back one stop at a
        # time — but never behind the vehicle, which cannot un-drive a road.
        floor = stops.index(request.current_node) if request.current_node in stops else 0
        candidates: list[RouteCandidate] = []
        report = None

        while index >= floor:
            anchor = stops[index]
            candidates, report = self.engine.plan(
                network,
                anchor,
                destination,
                vehicle=vehicle,
                coordinates=coordinates,
                k=1,
                algorithm=algorithm or settings.replan_algorithm,
                overlay=overlay,
            )
            if candidates:
                break
            index -= 1

        anchor = stops[max(index, floor)]
        kept = stops[: max(index, floor) + 1]

        if not candidates:
            return ReplanOutcome(
                replanned=False,
                reason="no alternative exists from the affected node",
                kept_prefix=kept,
                replanned_from=anchor,
                original_distance_km=original_distance,
                report=report.as_dict() if report else {},
                note=(
                    f"No diversion exists from {anchor} or any earlier stop still "
                    "ahead of the vehicle. Dispatch must hold or re-time."
                ),
            )

        suffix = candidates[0]
        spliced = self._splice(network, kept, suffix, report)

        added = round(spliced.total_distance_km - original_distance, 2)
        outcome = ReplanOutcome(
            replanned=True,
            reason=request.reason,
            kept_prefix=kept,
            replanned_from=anchor,
            route=spliced,
            original_distance_km=original_distance,
            new_distance_km=spliced.total_distance_km,
            added_distance_km=added,
            segments_reused=max(len(kept) - 1, 0),
            segments_changed=max(len(suffix.stops) - 1, 0),
            report=report.as_dict(),
        )

        if added > settings.replan_detour_warn_km:
            outcome.note = (
                f"The diversion adds {added:.0f} km. A full replan from the "
                "origin may be shorter, but would discard roads already driven."
            )

        logger.info(
            "Segment replanned",
            extra={
                "from": anchor,
                "reused": outcome.segments_reused,
                "changed": outcome.segments_changed,
                "added_km": added,
            },
        )
        return outcome

    # ------------------------------------------------------------------
    def _anchor(self, stops: list[str], request: ReplanRequest) -> str | None:
        """The last stop we are committed to before the disruption."""

        position = None
        if request.current_node and request.current_node in stops:
            position = stops.index(request.current_node)

        candidate_index = None
        if request.blocked_edge:
            source = request.blocked_edge[0]
            if source in stops:
                candidate_index = stops.index(source)

        for node in request.blocked_nodes:
            if node in stops:
                # Branch from the stop *before* the blocked one.
                blocked_index = max(stops.index(node) - 1, 0)
                candidate_index = (
                    blocked_index
                    if candidate_index is None
                    else min(candidate_index, blocked_index)
                )

        if candidate_index is None:
            return stops[position] if position is not None else None

        # Never branch behind the vehicle.
        if position is not None:
            candidate_index = max(candidate_index, position)
        return stops[candidate_index]

    def _apply_disruption(self, overlay: GraphOverlay, request: ReplanRequest) -> None:
        if request.blocked_edge:
            source, target = request.blocked_edge
            overlay.disable_edge(source, target, request.reason)
            overlay.disable_edge(target, source, request.reason)
        for node in request.blocked_nodes:
            overlay.disable_node(node, request.reason)

    def _distance(self, network: Any, stops: list[str]) -> float:
        total = 0.0
        for source, target in zip(stops, stops[1:]):
            leg = network.leg_exists(source, target)
            if leg:
                total += leg.distance_km
        return round(total, 2)

    def _splice(
        self,
        network: Any,
        kept: list[str],
        suffix: RouteCandidate,
        report: PlanningReport,
    ) -> RouteCandidate:
        """Join the driven prefix onto the newly planned suffix."""

        prefix_legs: list[dict[str, Any]] = []
        prefix_distance = 0.0
        for source, target in zip(kept, kept[1:]):
            leg = network.leg_exists(source, target)
            if not leg:
                continue
            prefix_distance += leg.distance_km
            prefix_legs.append(
                {
                    "from": source,
                    "to": target,
                    "distance_km": leg.distance_km,
                    "road_name": getattr(leg, "road_name", ""),
                    "kind": getattr(leg, "kind", "primary"),
                    "via": getattr(leg, "via", None),
                    "cost": None,
                    "committed": True,
                }
            )

        return RouteCandidate(
            rank=1,
            stops=[*kept[:-1], *suffix.stops],
            legs=[*prefix_legs, *suffix.legs],
            total_distance_km=round(prefix_distance + suffix.total_distance_km, 2),
            estimated_travel_minutes=suffix.estimated_travel_minutes,
            route_cost=suffix.route_cost,
            route_score=suffix.route_score,
            logistics_score=suffix.logistics_score,
            confidence=suffix.confidence,
            blocked_edges=suffix.blocked_edges,
            cost_breakdown=suffix.cost_breakdown,
            uses_alternate=suffix.uses_alternate,
            algorithm=report.algorithm,
        )


_replanner = SegmentReplanner()


def get_replanner() -> SegmentReplanner:
    return _replanner
