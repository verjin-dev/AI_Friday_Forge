"""Bridge between a verified graph path and a constraint-checkable candidate.

This lives in the domain layer rather than in an agent because it is pure data
mapping with no LLM, no I/O and no agent state: it takes the
:class:`~app.domain.network.RoutePath` the graph produced and restates it in the
shape :mod:`app.domain.constraints` evaluates. It has four consumers — the
routing API, the fleet view, the MCP tool surface and the Optimization Agent —
so placing it in the agent layer forced three of them into function-local
imports to escape a circular dependency (``app.domain`` → ``app.agents`` →
``app.mcp`` → ``app.agents``). Domain logic belongs in the domain layer.
"""

from __future__ import annotations

from app.domain.constraints import RouteCandidate
from app.domain.network import RoutePath


def path_to_candidate(path: RoutePath) -> RouteCandidate:
    """Convert a graph-derived path into a constraint-checkable candidate.

    Every field here originates in the knowledge graph, so the constraint
    engine is verifying data, not a model's assertion.
    """

    return RouteCandidate(
        label=path.label or " → ".join(path.stops),
        description=path.describe(),
        stops=path.stops,
        distance_km=path.total_distance_km,
        network_distance_km=path.total_distance_km,
        stop_count=len(path.stops),
        legs_verified=path.legs_verified,
        blocking_incidents=[item.describe() for item in path.blocking_incidents],
        endpoint_incidents=[item.describe() for item in path.endpoint_incidents],
        advisory_incidents=[item.describe() for item in path.advisory_incidents],
    )
