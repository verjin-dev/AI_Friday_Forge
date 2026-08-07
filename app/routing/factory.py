"""Algorithm selection.

Picking a strategy is a policy decision, not something the caller should have
to reason about — so it lives here rather than being scattered through the
agents. The rule is deliberately simple and observable:

* need alternatives          → Yen's, layered over the best single-path search
* small graph                → Dijkstra (no heuristic overhead, exact)
* large graph                → A* (same optimum, far fewer expansions)
* no coordinates available   → Dijkstra, because A*'s heuristic would be zero
                               everywhere and it would only add overhead

Every selection is logged with its reason, so an operator can always answer
"why did it use that algorithm?".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger
from app.routing.strategies import (
    AStarStrategy,
    DijkstraStrategy,
    RouteStrategy,
    YenKShortestStrategy,
)


logger = get_logger(__name__)


@dataclass(slots=True)
class AlgorithmChoice:
    strategy: RouteStrategy
    name: str
    reason: str


class RoutingStrategyFactory:
    """Creates a strategy for a given problem shape."""

    def __init__(self, *, large_graph_threshold: int | None = None) -> None:
        self.large_graph_threshold = (
            large_graph_threshold
            if large_graph_threshold is not None
            else settings.astar_node_threshold
        )

    def create(
        self,
        *,
        node_count: int,
        want_alternatives: bool = False,
        has_coordinates: bool = True,
        override: str | None = None,
    ) -> AlgorithmChoice:
        requested = (override or settings.routing_algorithm or "auto").strip().lower()

        if requested in {"dijkstra", "astar", "a*", "yen"}:
            return self._explicit(requested, has_coordinates)

        base_name, base, reason = self._auto_base(node_count, has_coordinates)

        if want_alternatives:
            return AlgorithmChoice(
                strategy=YenKShortestStrategy(base=base, k=settings.route_candidate_count),
                name="yen",
                reason=f"alternatives requested; Yen's over {base_name} ({reason})",
            )

        choice = AlgorithmChoice(strategy=base, name=base_name, reason=reason)
        logger.debug(
            "Routing algorithm selected",
            extra={"algorithm": choice.name, "reason": choice.reason},
        )
        return choice

    # ------------------------------------------------------------------
    def _auto_base(
        self, node_count: int, has_coordinates: bool
    ) -> tuple[str, RouteStrategy, str]:
        if not has_coordinates:
            return (
                "dijkstra",
                DijkstraStrategy(),
                "no coordinates, so an A* heuristic would add cost without pruning",
            )
        if node_count >= self.large_graph_threshold:
            return (
                "astar",
                AStarStrategy(),
                f"{node_count} nodes at or above the {self.large_graph_threshold} threshold",
            )
        return (
            "dijkstra",
            DijkstraStrategy(),
            f"{node_count} nodes below the {self.large_graph_threshold} threshold",
        )

    def _explicit(self, requested: str, has_coordinates: bool) -> AlgorithmChoice:
        if requested == "yen":
            return AlgorithmChoice(
                strategy=YenKShortestStrategy(
                    base=AStarStrategy() if has_coordinates else DijkstraStrategy(),
                    k=settings.route_candidate_count,
                ),
                name="yen",
                reason="explicitly requested",
            )
        if requested in {"astar", "a*"}:
            if not has_coordinates:
                return AlgorithmChoice(
                    strategy=DijkstraStrategy(),
                    name="dijkstra",
                    reason="A* requested but no coordinates are available",
                )
            return AlgorithmChoice(
                strategy=AStarStrategy(), name="astar", reason="explicitly requested"
            )
        return AlgorithmChoice(
            strategy=DijkstraStrategy(), name="dijkstra", reason="explicitly requested"
        )


_factory = RoutingStrategyFactory()


def get_strategy_factory() -> RoutingStrategyFactory:
    """Injection point — swap this in tests or for a different policy."""

    return _factory
