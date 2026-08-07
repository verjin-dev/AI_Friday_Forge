"""The routing engine.

Owns the deterministic half of the pipeline:

    knowledge graph -> overlay -> algorithm -> top-K candidates -> scoring

and nothing else. It never calls Google, never calls an LLM, and never decides
which route wins — that is the Optimization Agent's job, downstream, once live
traffic has been layered on. Keeping those responsibilities apart is what makes
the routing deterministic and reproducible: the same graph and the same
overlay always produce the same candidates.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.routing.cost import CostBreakdown, CostModel, CostWeights, VehicleContext
from app.routing.factory import RoutingStrategyFactory, get_strategy_factory
from app.routing.overlay import GraphOverlay, GraphProjection, overlay_from_incidents
from app.routing.strategies import SearchResult, YenKShortestStrategy


logger = get_logger(__name__)


class RouteCandidate(BaseModel):
    """One enterprise-scored candidate, ready for the Optimization Agent."""

    rank: int
    stops: list[str]
    legs: list[dict[str, Any]] = Field(default_factory=list)

    total_distance_km: float = 0.0
    estimated_travel_minutes: float = 0.0
    route_cost: float = 0.0

    #: 0..1, higher is better. Normalised against the best candidate.
    route_score: float = 0.0
    #: 0..1 penalising incident exposure and poor road quality.
    logistics_score: float = 0.0
    confidence: float = 0.5

    constraint_violations: list[str] = Field(default_factory=list)
    blocked_edges: list[str] = Field(default_factory=list)
    cost_breakdown: dict[str, Any] = Field(default_factory=dict)
    uses_alternate: bool = False

    algorithm: str = ""

    @property
    def label(self) -> str:
        if len(self.stops) > 2:
            via = f" via {', '.join(self.stops[1:-1][:3])}"
            if len(self.stops) > 5:
                via += ", …"
        else:
            via = " direct"
        return (
            f"Route {self.rank}: {self.stops[0]} → {self.stops[-1]}{via} "
            f"({self.total_distance_km:.0f} km)"
        )


@dataclass(slots=True)
class PlanningReport:
    """Telemetry the Explanation and Validation agents quote verbatim."""

    algorithm: str
    algorithm_reason: str
    candidates_requested: int
    candidates_found: int
    nodes_expanded: int
    duration_ms: float
    overlay_applied: list[str] = field(default_factory=list)
    graph_nodes: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "algorithm_reason": self.algorithm_reason,
            "candidates_requested": self.candidates_requested,
            "candidates_found": self.candidates_found,
            "nodes_expanded": self.nodes_expanded,
            "duration_ms": round(self.duration_ms, 2),
            "overlay_applied": self.overlay_applied,
            "graph_nodes": self.graph_nodes,
            "notes": self.notes,
        }


class _OverlayCostModel(CostModel):
    """Cost model that folds live overlay penalties into each edge.

    Subclassed rather than parameterised so the strategies stay unaware of
    overlays entirely — they just ask for a cost.
    """

    def __init__(self, base: CostModel, projection: GraphProjection) -> None:
        super().__init__(base.weights, base.vehicle)
        self._projection = projection

    def evaluate(self, leg: Any, *, extra_penalty: float = 0.0) -> CostBreakdown:
        penalty = self._projection.penalty_for(
            getattr(leg, "from_location", ""), getattr(leg, "to_location", "")
        )
        return super().evaluate(leg, extra_penalty=extra_penalty + penalty)


class EnterpriseRouteOptimizationEngine:
    """Enterprise Route Optimization Engine.

    Owns the deterministic, multi-dimensional logistics optimization pipeline:
    Knowledge Graph -> Incident Overlay -> A* Search / Yen's Alternatives -> 7-Dimension Cost Model -> Enterprise Candidate Scoring.
    """

    def __init__(
        self,
        factory: RoutingStrategyFactory | None = None,
        weights: CostWeights | None = None,
    ) -> None:
        self._factory = factory or get_strategy_factory()
        self._weights = weights

    # ------------------------------------------------------------------
    def plan(
        self,
        network: Any,
        origin: str,
        destination: str,
        *,
        vehicle: VehicleContext | None = None,
        shipment: ShipmentContext | None = None,
        mode: Any = "balanced",
        environment: Any = None,
        coordinates: dict[str, dict[str, float]] | None = None,
        k: int | None = None,
        algorithm: str | None = None,
        overlay: GraphOverlay | None = None,
        apply_incident_overlay: bool = True,
    ) -> tuple[list[RouteCandidate], PlanningReport]:
        started = time.perf_counter()
        wanted = k or settings.route_candidate_count

        resolved_origin = network.resolve(origin) or origin
        resolved_destination = network.resolve(destination) or destination

        if overlay is None and apply_incident_overlay and settings.enable_incident_overlay:
            overlay = overlay_from_incidents(network.incidents_by_location)
        overlay = overlay or GraphOverlay()

        projection = GraphProjection(
            network,
            overlay,
            coordinates,
            protected_nodes=(resolved_origin, resolved_destination),
        )

        has_coordinates = projection.coordinates(resolved_destination) is not None
        choice = self._factory.create(
            node_count=len(network.locations),
            want_alternatives=wanted > 1,
            has_coordinates=has_coordinates,
            override=algorithm,
        )

        # ML Feature Engineering & ML Prediction Model Pipeline
        from app.domain.ml_prediction import get_ml_predictor
        ml_predictor = get_ml_predictor()
        features = ml_predictor.feature_pipeline.extract_features(
            distance_km=0.0,
            free_flow_minutes=0.0,
            active_incidents_count=len(getattr(network, "incidents_by_location", {}) or {}),
        )
        ml_pred = ml_predictor.predict(features)

        cost_model = _OverlayCostModel(
            CostModel(
                weights=self._weights,
                vehicle=vehicle,
                shipment=shipment,
                ml_prediction=ml_pred,
                mode=mode,
                environment=environment,
            ),
            projection,
        )

        if isinstance(choice.strategy, YenKShortestStrategy):
            results = choice.strategy.find_k(
                projection, resolved_origin, resolved_destination, cost_model, k=wanted
            )
        else:
            single = choice.strategy.find(
                projection, resolved_origin, resolved_destination, cost_model
            )
            results = [single] if single else []

        report = PlanningReport(
            algorithm=choice.name,
            algorithm_reason=choice.reason,
            candidates_requested=wanted,
            candidates_found=len(results),
            nodes_expanded=sum(item.nodes_expanded for item in results),
            duration_ms=(time.perf_counter() - started) * 1000,
            overlay_applied=overlay.describe(),
            graph_nodes=len(network.locations),
        )

        if not results:
            report.notes.append(
                f"No path from {resolved_origin} to {resolved_destination} once "
                "active incidents and vehicle restrictions were applied."
            )
            logger.info("No route found", extra=report.as_dict())
            return [], report

        candidates = self._score(results, cost_model, projection)

        logger.info(
            "Routes planned",
            extra={
                "algorithm": choice.name,
                "candidates": len(candidates),
                "nodes_expanded": report.nodes_expanded,
                "duration_ms": round(report.duration_ms, 1),
            },
        )
        return candidates, report

    # ------------------------------------------------------------------
    def _score(
        self,
        results: list[SearchResult],
        cost_model: CostModel,
        projection: GraphProjection,
    ) -> list[RouteCandidate]:
        """Turn raw searches into comparable, normalised candidates."""

        candidates: list[RouteCandidate] = []
        best_cost = min(item.cost for item in results) or 1.0

        for rank, result in enumerate(results, start=1):
            totals: dict[str, float] = {}
            travel_minutes = 0.0
            quality_penalty = 0.0

            leg_payloads: list[dict[str, Any]] = []
            for leg in result.legs:
                breakdown = cost_model.evaluate(leg)
                for key, value in breakdown.as_dict().items():
                    if isinstance(value, (int, float)):
                        totals[key] = totals.get(key, 0.0) + value
                travel_minutes += breakdown.travel_time
                quality_penalty += breakdown.quality
                leg_payloads.append(
                    {
                        "from": leg.from_location,
                        "to": leg.to_location,
                        "distance_km": leg.distance_km,
                        "road_name": getattr(leg, "road_name", ""),
                        "kind": getattr(leg, "kind", "primary"),
                        "via": getattr(leg, "via", None),
                        "cost": round(breakdown.total, 2),
                    }
                )

            # route_score: how close this is to the cheapest option.
            route_score = round(best_cost / result.cost, 4) if result.cost else 0.0

            # logistics_score: penalises time lost to poor surface and to
            # overlay penalties, which is where incident exposure shows up.
            overhead = quality_penalty + totals.get("risk", 0.0)
            logistics_score = round(
                max(0.0, 1.0 - overhead / max(travel_minutes + overhead, 1.0)), 4
            )

            # Confidence rises when the graph told us enough to judge: known
            # road classes and no unverifiable gaps.
            known_roads = sum(
                1 for leg in result.legs if getattr(leg, "road_name", "")
            )
            confidence = round(
                0.55
                + 0.25 * (known_roads / max(len(result.legs), 1))
                + (0.15 if projection.coordinates(result.stops[-1]) else 0.0),
                3,
            )

            candidates.append(
                RouteCandidate(
                    rank=rank,
                    stops=result.stops,
                    legs=leg_payloads,
                    total_distance_km=result.distance_km,
                    estimated_travel_minutes=round(travel_minutes, 1),
                    route_cost=round(result.cost, 2),
                    route_score=route_score,
                    logistics_score=logistics_score,
                    confidence=min(confidence, 0.95),
                    blocked_edges=result.blocked_edges,
                    cost_breakdown={
                        key: round(value, 2) for key, value in totals.items()
                    },
                    uses_alternate=any(
                        getattr(leg, "kind", "primary") == "alternate"
                        for leg in result.legs
                    ),
                    algorithm=result.algorithm,
                )
            )

        return candidates


RoutingEngine = EnterpriseRouteOptimizationEngine
_engine = EnterpriseRouteOptimizationEngine()


def get_routing_engine() -> EnterpriseRouteOptimizationEngine:
    """Injection point for the API layer and the agents."""

    return _engine
