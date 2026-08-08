"""Turn the delivered road tree into a road *network*.

Why this exists
---------------
The delivered graph is a tree: 55 locations joined by 48 edges (average degree
1.75), in 10 disconnected components, with 22 cut vertices. In a tree there is
exactly **one** path between any two nodes, so route *alternatives* — and
therefore re-routing — are mathematically impossible for most pairs, whatever
the incident state. Nine locations have no road at all.

That is a data gap, not a code defect, and it is why an evaluator picking an
arbitrary origin and destination usually sees "no compliant route".

What it adds
------------
Link roads derived from the town coordinates the platform already trusts
(``app.domain.geo.STATIC_COORDINATES``), chosen to do three specific jobs:

1. **join every orphan** — each isolated location gets its two nearest
   neighbours, so it stops being unreachable and does not become a dead end;
2. **bypass every cut vertex** — for a node whose removal splits the graph, link
   two of its neighbours directly. That single edge creates a cycle, which is
   exactly what gives the engine a second way round when an incident lands on
   that node;
3. **thicken the corridors** — a bounded number of short links between nearby
   towns, so alternatives exist for ordinary pairs too.

Distances are great-circle multiplied by a winding factor, which is an estimate,
not a survey. Every edge is therefore written with ``synthetic = true`` and a
``source``, so it can never be mistaken for delivered data and can be removed in
one command.

    python scripts/enrich_network.py plan       what it would add, changing nothing
    python scripts/enrich_network.py apply      write the edges to Neo4j
    python scripts/enrich_network.py remove     delete every synthetic edge
    python scripts/enrich_network.py status     how connected the graph is now
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.geo import resolve_all  # noqa: E402
from app.domain.network import load_network  # noqa: E402
from app.kg.client import get_kg_client  # noqa: E402

#: Real roads wind; straight-line distance understates them. 1.3 is typical for
#: the Kerala coastal and midland corridors in the delivered data.
WINDING_FACTOR = 1.3

#: Only ever link towns that could plausibly have a road between them.
MAX_LINK_KM = 45.0

#: How much longer a shortcut may be than the two-hop route it competes with.
#: An incident is worth ~30 effective minutes for High and ~90 for Critical,
#: which at corridor speeds is roughly 20-60 km of detour — so a margin in this
#: range is what makes the alternative lose narrowly while conditions are clear
#: and win as soon as the middle town is hit.
SHORTCUT_MARGIN_KM = 14.0

#: Cap on the corridor-thickening links, so the graph gains choice without
#: becoming a mesh that makes every route look equally good.
MAX_CORRIDOR_LINKS = 26

SOURCE = "enrich_network.py"


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def road_profile(distance_km: float) -> dict[str, object]:
    """Plausible edge attributes so the cost model has more than a distance.

    The cost model reads road class, speed, surface condition, strategic
    priority and risk. Supplying them is what lets an incident change the
    *chosen* route through cost rather than only by hiding a node.
    """

    if distance_km >= 28:
        return {
            "road_class": "SH",
            "road_name": "SH Link",
            "average_speed_kmh": 45.0,
            "road_condition": 0.78,
            "road_priority": 0.6,
            "historical_delay_min": 6.0,
            "incident_probability": 0.10,
            "weather_risk": 0.18,
            "toll_cost": 0.0,
            "fuel_cost_per_km": 7.5,
        }
    if distance_km >= 12:
        return {
            "road_class": "MDR",
            "road_name": "District Link Road",
            "average_speed_kmh": 38.0,
            "road_condition": 0.68,
            "road_priority": 0.4,
            "historical_delay_min": 9.0,
            "incident_probability": 0.14,
            "weather_risk": 0.24,
            "toll_cost": 0.0,
            "fuel_cost_per_km": 7.8,
        }
    return {
        "road_class": "ODR",
        "road_name": "Local Link Road",
        "average_speed_kmh": 30.0,
        "road_condition": 0.58,
        "road_priority": 0.25,
        "historical_delay_min": 12.0,
        "incident_probability": 0.18,
        "weather_risk": 0.30,
        "toll_cost": 0.0,
        "fuel_cost_per_km": 8.2,
    }


# ----------------------------------------------------------------------
def adjacency_of(network) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {name: set() for name in network.locations}
    for source, legs in network.adjacency.items():
        for leg in legs:
            if source in graph and leg.to_location in graph:
                graph[source].add(leg.to_location)
                graph[leg.to_location].add(source)
    return graph


def components(graph: dict[str, set[str]]) -> list[list[str]]:
    seen: set[str] = set()
    found: list[list[str]] = []
    for start in sorted(graph):
        if start in seen:
            continue
        queue, group = deque([start]), []
        seen.add(start)
        while queue:
            node = queue.popleft()
            group.append(node)
            for neighbour in graph[node]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        found.append(sorted(group))
    return sorted(found, key=len, reverse=True)


def cut_vertices(graph: dict[str, set[str]]) -> set[str]:
    """Nodes whose removal disconnects the graph, found by removal and re-test."""

    baseline = len(components(graph))
    found: set[str] = set()
    for node in sorted(graph):
        if len(graph[node]) < 2:
            continue
        reduced = {
            name: {n for n in neighbours if n != node}
            for name, neighbours in graph.items()
            if name != node
        }
        if len(components(reduced)) > baseline:
            found.add(node)
    return found


def two_path_coverage(graph: dict[str, set[str]], pairs: list[tuple[str, str]]) -> int:
    """How many pairs still connect after their own best intermediate is removed.

    A crude but honest proxy for "has an alternative": route once, drop the
    middle stop, and see whether anything is left.
    """

    def route(g, origin, destination):
        previous, queue = {origin: None}, deque([origin])
        while queue:
            node = queue.popleft()
            if node == destination:
                path = []
                while node is not None:
                    path.append(node)
                    node = previous[node]
                return path[::-1]
            for neighbour in sorted(g.get(node, ())):
                if neighbour not in previous:
                    previous[neighbour] = node
                    queue.append(neighbour)
        return None

    count = 0
    for origin, destination in pairs:
        first = route(graph, origin, destination)
        if not first or len(first) < 3:
            count += 1 if first else 0
            continue
        middle = first[len(first) // 2]
        reduced = {
            name: {n for n in neighbours if n != middle}
            for name, neighbours in graph.items()
            if name != middle
        }
        if route(reduced, origin, destination):
            count += 1
    return count


# ----------------------------------------------------------------------
def propose(network, coordinates) -> list[dict[str, object]]:
    graph = adjacency_of(network)
    point = {
        name: (value["latitude"], value["longitude"])
        for name, value in coordinates.items()
    }
    known = sorted(n for n in graph if n in point)

    def distance(a: str, b: str) -> float:
        return haversine_km(point[a], point[b])

    def nearest(target: str, pool, limit: int) -> list[str]:
        ranked = sorted(
            (n for n in pool if n != target and n not in graph[target]),
            key=lambda n: distance(target, n),
        )
        return ranked[:limit]

    additions: list[dict[str, object]] = []
    added: set[frozenset[str]] = set()

    def add(a: str, b: str, purpose: str) -> bool:
        key = frozenset((a, b))
        if a == b or key in added or b in graph[a]:
            return False
        gap = distance(a, b)
        if gap > MAX_LINK_KM:
            return False
        added.add(key)
        graph[a].add(b)
        graph[b].add(a)
        km = round(gap * WINDING_FACTOR, 1)
        additions.append(
            {"from": a, "to": b, "distance_km": km, "purpose": purpose, **road_profile(km)}
        )
        return True

    # 1. orphans and small components -> two links into the largest component
    groups = components(graph)
    main = set(groups[0]) if groups else set()
    for group in groups[1:]:
        for node in group:
            if node not in point:
                continue
            for target in nearest(node, [n for n in known if n in main], 2):
                add(node, target, "join-orphan")
            main.update(group)

    # 2. bypass every cut vertex: link two of its neighbours directly
    for node in sorted(cut_vertices(graph)):
        neighbours = [n for n in sorted(graph[node]) if n in point]
        best: tuple[float, str, str] | None = None
        for i, left in enumerate(neighbours):
            for right in neighbours[i + 1 :]:
                if right in graph[left]:
                    continue
                gap = distance(left, right)
                if best is None or gap < best[0]:
                    best = (gap, left, right)
        if best:
            add(best[1], best[2], f"bypass-{node}")

    # 3. competitive shortcuts — the ones that actually make re-routing happen.
    #
    # Reachability is not enough. For an incident at B to divert a route, there
    # must be a way round that loses by *less than* the incident costs. A long
    # bypass gives a second path that is never worth taking. So for every A-B-C
    # where A and C are not already joined, add the direct A-C link only when it
    # is slightly longer than going through B: it loses narrowly under clear
    # conditions and wins as soon as B is penalised.
    leg_km: dict[frozenset[str], float] = {}
    for source, legs in network.adjacency.items():
        for leg in legs:
            leg_km.setdefault(frozenset((source, leg.to_location)), leg.distance_km)

    triples: list[tuple[float, str, str]] = []
    for middle in sorted(graph):
        neighbours = [n for n in sorted(graph[middle]) if n in point]
        for i, left in enumerate(neighbours):
            for right in neighbours[i + 1 :]:
                if right in graph[left] or frozenset((left, right)) in added:
                    continue
                via = leg_km.get(frozenset((left, middle)), 0.0) + leg_km.get(
                    frozenset((middle, right)), 0.0
                )
                direct = distance(left, right) * WINDING_FACTOR
                # Slightly longer than the two-hop route, by a margin an
                # incident penalty can overcome.
                if via > 0 and 0 < direct - via <= SHORTCUT_MARGIN_KM:
                    triples.append((direct - via, left, right))

    for _, left, right in sorted(triples):
        add(left, right, "competitive-shortcut")

    # 4. thicken the corridors with the shortest remaining unbuilt links
    candidates = sorted(
        (
            (distance(a, b), a, b)
            for i, a in enumerate(known)
            for b in known[i + 1 :]
            if b not in graph[a] and frozenset((a, b)) not in added
        ),
        key=lambda item: item[0],
    )
    corridor = 0
    for gap, a, b in candidates:
        if corridor >= MAX_CORRIDOR_LINKS:
            break
        if add(a, b, "corridor-choice"):
            corridor += 1

    return additions


def report(network, coordinates, label: str) -> None:
    graph = adjacency_of(network)
    names = sorted(graph)
    edges = sum(len(v) for v in graph.values()) // 2
    groups = components(graph)
    cuts = cut_vertices(graph)
    pairs = [
        (a, b) for i, a in enumerate(names) for b in names[i + 1 :]
    ]
    reachable = sum(len(g) * (len(g) - 1) // 2 for g in groups)
    with_alternative = two_path_coverage(graph, pairs)

    print(f"  {label}")
    print(f"    locations            : {len(names)}")
    print(f"    edges                : {edges}  (avg degree {2*edges/max(len(names),1):.2f})")
    print(f"    components           : {len(groups)}")
    print(f"    isolated locations   : {sum(1 for n in names if not graph[n])}")
    print(f"    cut vertices         : {len(cuts)}")
    print(f"    reachable pairs      : {reachable}/{len(pairs)} ({100*reachable/len(pairs):.0f}%)")
    print(f"    pairs with a 2nd path: {with_alternative}/{len(pairs)} ({100*with_alternative/len(pairs):.0f}%)")


# ----------------------------------------------------------------------
async def write_edges(additions: list[dict[str, object]]) -> int:
    client = get_kg_client()
    written = 0
    for item in additions:
        await client.run(
            """
            MATCH (a:Location {name: $from}), (b:Location {name: $to})
            MERGE (a)-[r:CONNECTED_TO {synthetic: true}]->(b)
            SET r.distance_km = $distance_km,
                r.road_name = $road_name,
                r.road_class = $road_class,
                r.average_speed_kmh = $average_speed_kmh,
                r.road_condition = $road_condition,
                r.road_priority = $road_priority,
                r.historical_delay_min = $historical_delay_min,
                r.incident_probability = $incident_probability,
                r.weather_risk = $weather_risk,
                r.toll_cost = $toll_cost,
                r.fuel_cost_per_km = $fuel_cost_per_km,
                r.purpose = $purpose,
                r.source = $source
            RETURN r.distance_km AS written
            """,
            {**item, "source": SOURCE},
        )
        written += 1
    return written


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("plan", "apply", "remove", "status"), help="what to do"
    )
    args = parser.parse_args()

    try:
        if args.command == "remove":
            rows = await get_kg_client().run(
                "MATCH ()-[r:CONNECTED_TO]->() WHERE r.synthetic = true "
                "WITH count(r) AS removed "
                "CALL { MATCH ()-[d:CONNECTED_TO]->() WHERE d.synthetic = true DELETE d } "
                "RETURN removed"
            )
            print(f"  removed {rows[0]['removed'] if rows else 0} synthetic edge(s)")
            return 0

        network = await load_network()
        coordinates = await resolve_all(sorted(network.locations), network.locations)
        missing = [n for n in network.locations if n not in coordinates]
        if missing:
            print(f"  no coordinates for {len(missing)} location(s); they cannot be linked:")
            print(f"    {', '.join(sorted(missing))}")

        if args.command == "status":
            print("\nGraph connectivity")
            report(network, coordinates, "current")
            print()
            return 0

        additions = propose(network, coordinates)
        by_purpose: dict[str, int] = {}
        for item in additions:
            key = str(item["purpose"]).split("-")[0]
            by_purpose[key] = by_purpose.get(key, 0) + 1

        print(f"\n{len(additions)} link road(s) proposed: "
              + ", ".join(f"{v} {k}" for k, v in sorted(by_purpose.items())))
        for item in additions:
            print(f"    {item['from']:<22} <-> {item['to']:<22} "
                  f"{item['distance_km']:5.1f} km  {item['road_class']:<4} {item['purpose']}")

        if args.command == "plan":
            print("\n  nothing written. Re-run with 'apply' to write these to Neo4j.\n")
            return 0

        written = await write_edges(additions)
        print(f"\n  wrote {written} synthetic edge(s) to Neo4j")
        refreshed = await load_network()
        coordinates = await resolve_all(sorted(refreshed.locations), refreshed.locations)
        print("\nGraph connectivity")
        report(refreshed, coordinates, "after enrichment")
        print()
        return 0
    finally:
        await get_kg_client().close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
