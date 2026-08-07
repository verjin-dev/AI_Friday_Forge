"""Routing algorithms behind a single Strategy interface.

Three implementations, all operating on the same read-only graph view and the
same :class:`~app.routing.cost.CostModel`:

``DijkstraStrategy``   exact single-source shortest path; no heuristic needed.
``AStarStrategy``      Dijkstra plus an admissible geographic heuristic; same
                       optimum, far fewer nodes expanded on a large graph.
``YenKShortestStrategy`` builds on either of the above to produce genuinely
                       distinct alternatives, which is what the Optimization
                       Agent compares.

The LLM is never involved. These functions cannot invent an edge because they
only ever traverse what the graph view hands them.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

from app.core.logging import get_logger
from app.routing.cost import CostModel


logger = get_logger(__name__)

EARTH_RADIUS_KM = 6371.0


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    return (
        2
        * EARTH_RADIUS_KM
        * math.asin(
            math.sqrt(
                math.sin((lat2 - lat1) / 2) ** 2
                + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
            )
        )
    )


class GraphView(Protocol):
    """Everything a strategy is allowed to see.

    Deliberately narrow: strategies cannot reach into Neo4j, mutate the
    network, or read anything the cost model has not been given.
    """

    def neighbours(self, node: str) -> Iterable[Any]:
        """Outgoing legs from ``node``."""

    def coordinates(self, node: str) -> tuple[float, float] | None:
        """Latitude/longitude, or None when unknown."""

    def nodes(self) -> Iterable[str]:
        ...


@dataclass(slots=True)
class SearchResult:
    """One path plus the search telemetry the Explanation Agent quotes."""

    stops: list[str]
    legs: list[Any]
    cost: float
    algorithm: str
    nodes_expanded: int = 0
    blocked_edges: list[str] = field(default_factory=list)

    @property
    def distance_km(self) -> float:
        return round(sum(float(leg.distance_km) for leg in self.legs), 2)


class RouteStrategy(Protocol):
    """Contract every routing algorithm satisfies."""

    name: str

    def find(
        self,
        graph: GraphView,
        origin: str,
        destination: str,
        cost_model: CostModel,
        *,
        excluded_edges: set[tuple[str, str]] | None = None,
        excluded_nodes: set[str] | None = None,
    ) -> SearchResult | None:
        ...


# ----------------------------------------------------------------------
def _reconstruct(
    previous: dict[str, tuple[str, Any]], origin: str, destination: str
) -> tuple[list[str], list[Any]]:
    stops = [destination]
    legs: list[Any] = []
    cursor = destination
    while cursor != origin:
        parent, leg = previous[cursor]
        legs.append(leg)
        stops.append(parent)
        cursor = parent
    stops.reverse()
    legs.reverse()
    return stops, legs


def _edge_allowed(
    leg: Any,
    source: str,
    excluded_edges: set[tuple[str, str]] | None,
    excluded_nodes: set[str] | None,
) -> bool:
    if excluded_nodes and leg.to_location in excluded_nodes:
        return False
    if excluded_edges and (source, leg.to_location) in excluded_edges:
        return False
    return True


class _BestFirstSearch:
    """Shared machinery for Dijkstra and A*; they differ only in the heuristic."""

    name = "best-first"

    def __init__(self, heuristic: Callable[[str], float] | None = None) -> None:
        self._heuristic = heuristic

    def _make_heuristic(
        self, graph: GraphView, destination: str, cost_model: CostModel
    ) -> Callable[[str], float]:
        return lambda _node: 0.0

    def find(
        self,
        graph: GraphView,
        origin: str,
        destination: str,
        cost_model: CostModel,
        *,
        excluded_edges: set[tuple[str, str]] | None = None,
        excluded_nodes: set[str] | None = None,
    ) -> SearchResult | None:
        if origin == destination:
            return None
        if excluded_nodes and (origin in excluded_nodes or destination in excluded_nodes):
            return None

        heuristic = self._make_heuristic(graph, destination, cost_model)

        best: dict[str, float] = {origin: 0.0}
        previous: dict[str, tuple[str, Any]] = {}
        settled: set[str] = set()
        blocked: list[str] = []
        expanded = 0

        queue: list[tuple[float, float, str]] = [(heuristic(origin), 0.0, origin)]

        while queue:
            _priority, distance, node = heapq.heappop(queue)
            if node in settled:
                continue
            settled.add(node)
            expanded += 1

            if node == destination:
                stops, legs = _reconstruct(previous, origin, destination)
                return SearchResult(
                    stops=stops,
                    legs=legs,
                    cost=round(distance, 4),
                    algorithm=self.name,
                    nodes_expanded=expanded,
                    blocked_edges=blocked,
                )

            for leg in graph.neighbours(node):
                if not _edge_allowed(leg, node, excluded_edges, excluded_nodes):
                    continue

                breakdown = cost_model.evaluate(leg)
                if math.isinf(breakdown.total):
                    # Vehicle cannot legally use this edge — record why once.
                    label = f"{node} -> {leg.to_location}: {breakdown.blocked_reason}"
                    if label not in blocked:
                        blocked.append(label)
                    continue

                candidate = distance + breakdown.total
                if candidate < best.get(leg.to_location, math.inf):
                    best[leg.to_location] = candidate
                    previous[leg.to_location] = (node, leg)
                    heapq.heappush(
                        queue,
                        (candidate + heuristic(leg.to_location), candidate, leg.to_location),
                    )

        return None


class AStarStrategy(_BestFirstSearch):
    """Default single-path routing engine: A* with an admissible geographic lower bound.

    The heuristic is straight-line distance at the fastest road speed, which
    can never over-estimate the true remaining cost. Where coordinates
    are missing the heuristic returns zero, degrading gracefully to uniform-cost
    search for that node.
    """

    name = "astar"

    def _make_heuristic(
        self, graph: GraphView, destination: str, cost_model: CostModel
    ) -> Callable[[str], float]:
        target = graph.coordinates(destination)
        if target is None:
            logger.debug(
                "A* un-geocoded fallback — destination has no coordinates",
                extra={"destination": destination},
            )
            return lambda _node: 0.0

        cache: dict[str, float] = {}

        def heuristic(node: str) -> float:
            if node in cache:
                return cache[node]
            point = graph.coordinates(node)
            value = (
                0.0
                if point is None
                else cost_model.heuristic_minutes(haversine_km(point, target))
            )
            cache[node] = value
            return value

        return heuristic


class DijkstraStrategy(AStarStrategy):
    """Alias for AStarStrategy for backward compatibility."""

    name = "dijkstra"


class YenKShortestStrategy:
    """Yen's algorithm for K loopless shortest paths.

    Alternatives matter operationally: an option that is 6% longer but avoids a
    corridor with a bad incident history is frequently the right dispatch. Yen's
    guarantees the alternatives are genuinely distinct rather than trivial
    reorderings of the same road.
    """

    name = "yen"

    def __init__(self, base: RouteStrategy | None = None, k: int = 4) -> None:
        self.base = base or AStarStrategy()
        self.k = k

    def find(
        self,
        graph: GraphView,
        origin: str,
        destination: str,
        cost_model: CostModel,
        *,
        excluded_edges: set[tuple[str, str]] | None = None,
        excluded_nodes: set[str] | None = None,
    ) -> SearchResult | None:
        paths = self.find_k(
            graph,
            origin,
            destination,
            cost_model,
            k=1,
            excluded_edges=excluded_edges,
            excluded_nodes=excluded_nodes,
        )
        return paths[0] if paths else None

    def find_k(
        self,
        graph: GraphView,
        origin: str,
        destination: str,
        cost_model: CostModel,
        *,
        k: int | None = None,
        excluded_edges: set[tuple[str, str]] | None = None,
        excluded_nodes: set[str] | None = None,
    ) -> list[SearchResult]:
        wanted = k or self.k
        first = self.base.find(
            graph,
            origin,
            destination,
            cost_model,
            excluded_edges=excluded_edges,
            excluded_nodes=excluded_nodes,
        )
        if first is None:
            return []

        accepted: list[SearchResult] = [first]
        # Candidate heap keyed by cost; the counter keeps the sort total.
        candidates: list[tuple[float, int, SearchResult]] = []
        counter = 0
        expanded = first.nodes_expanded

        while len(accepted) < wanted:
            previous = accepted[-1]

            for index in range(len(previous.stops) - 1):
                spur_node = previous.stops[index]
                root_stops = previous.stops[: index + 1]
                root_legs = previous.legs[:index]

                banned_edges = set(excluded_edges or ())
                # Remove the edges that would simply reproduce a known path.
                for path in accepted:
                    if path.stops[: index + 1] == root_stops and len(path.stops) > index + 1:
                        banned_edges.add((path.stops[index], path.stops[index + 1]))

                # Root nodes are off limits, which is what keeps spurs loopless.
                banned_nodes = set(excluded_nodes or ()) | set(root_stops[:-1])

                spur = self.base.find(
                    graph,
                    spur_node,
                    destination,
                    cost_model,
                    excluded_edges=banned_edges,
                    excluded_nodes=banned_nodes,
                )
                if spur is None:
                    continue

                expanded += spur.nodes_expanded
                total_legs = [*root_legs, *spur.legs]
                total_stops = [*root_stops[:-1], *spur.stops]

                if any(
                    path.stops == total_stops for path in accepted
                ) or any(item[2].stops == total_stops for item in candidates):
                    continue

                cost = sum(cost_model.cost(leg) for leg in total_legs)
                if math.isinf(cost):
                    continue

                counter += 1
                heapq.heappush(
                    candidates,
                    (
                        cost,
                        counter,
                        SearchResult(
                            stops=total_stops,
                            legs=total_legs,
                            cost=round(cost, 4),
                            algorithm=self.name,
                            nodes_expanded=spur.nodes_expanded,
                            blocked_edges=spur.blocked_edges,
                        ),
                    ),
                )

            if not candidates:
                break
            accepted.append(heapq.heappop(candidates)[2])

        accepted[0].nodes_expanded = expanded
        for path in accepted:
            path.algorithm = self.name
        return accepted
