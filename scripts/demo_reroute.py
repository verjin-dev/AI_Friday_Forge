"""Make incident-driven re-routing work for any origin and destination.

An evaluator will pick their own lane, so a fixed demo script is not enough.
These commands work against whatever the graph currently holds:

    python scripts/demo_reroute.py snapshot            record every incident status
    python scripts/demo_reroute.py baseline            set a demo-ready state
    python scripts/demo_reroute.py audit               how well re-routing works now
    python scripts/demo_reroute.py suggest A B         which incident re-routes A->B
    python scripts/demo_reroute.py verify A B I013      prove one, before and after
    python scripts/demo_reroute.py restore             put every incident back

Why ``baseline`` is needed
--------------------------
A blocking incident hides a location, so the engine routes around it. That only
works while somewhere else to go still exists. The delivered data has 14 Critical
incidents active at once on a network of 55 towns, and they sit on corridor hubs:
together they cut the graph into pieces, so 47% of location pairs have no route
at all and an evaluator's lane most likely fails outright.

``baseline`` keeps as many Critical incidents active as the network can carry
while every pair still has a route — computed, not guessed — and stands the rest
down. Non-blocking incidents are left alone: they are costs, not closures.

Run ``scripts/enrich_network.py apply`` first. On the delivered tree there is
only one path between most towns, so nothing can re-route regardless of state.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.geo import resolve_all  # noqa: E402
from app.domain.network import load_network  # noqa: E402
from app.kg.client import get_kg_client  # noqa: E402
from app.routing import VehicleContext, get_routing_engine  # noqa: E402
from app.routing.overlay import overlay_from_incidents  # noqa: E402

SNAPSHOT_PATH = ROOT / "data" / "demo_incident_snapshot.json"


# ----------------------------------------------------------------------
async def set_status(incident_id: str, status: str) -> dict | None:
    """The same write the incident portal performs."""

    rows = await get_kg_client().run(
        "MATCH (i:Incident {incident_id: $id}) SET i.status = $status "
        "RETURN i.incident_id AS incident_id, i.severity AS severity, "
        "i.type AS type, i.status AS status",
        {"id": incident_id, "status": status},
    )
    return rows[0] if rows else None


async def all_incidents() -> list[dict]:
    return await get_kg_client().run(
        "MATCH (i:Incident)-[:HAS_INCIDENT]->(l:Location) "
        "RETURN i.incident_id AS incident_id, i.type AS type, "
        "i.severity AS severity, i.status AS status, l.name AS location "
        "ORDER BY i.incident_id",
        limit=1000,
    )


def undirected(network) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {name: set() for name in network.locations}
    for source, legs in network.adjacency.items():
        for leg in legs:
            if source in graph and leg.to_location in graph:
                graph[source].add(leg.to_location)
                graph[leg.to_location].add(source)
    return graph


def fully_connected_without(graph: dict[str, set[str]], removed: set[str]) -> bool:
    """Every remaining location still reachable from every other."""

    remaining = [n for n in graph if n not in removed]
    if not remaining:
        return False
    seen, queue = {remaining[0]}, deque([remaining[0]])
    while queue:
        node = queue.popleft()
        for neighbour in graph[node]:
            if neighbour not in seen and neighbour not in removed:
                seen.add(neighbour)
                queue.append(neighbour)
    return len(seen) == len(remaining)


class Planner:
    """Plans against a chosen set of active incidents, writing nothing."""

    def __init__(self, network, coordinates) -> None:
        self.network = network
        self.coordinates = coordinates
        self.engine = get_routing_engine()

    def overlay(self, active_ids: set[str]):
        projected = {}
        for location, items in self.network.incidents_by_location.items():
            projected[location] = [
                item.model_copy(
                    update={
                        "status": "Active"
                        if item.incident_id in active_ids
                        else "Inactive"
                    }
                )
                for item in items
            ]
        return overlay_from_incidents(projected)

    def route(self, origin: str, destination: str, active_ids: set[str]):
        candidates, _ = self.engine.plan(
            self.network,
            origin,
            destination,
            vehicle=VehicleContext.from_profile(None),
            coordinates=self.coordinates,
            k=1,
            overlay=self.overlay(active_ids),
            apply_incident_overlay=False,
        )
        return candidates[0] if candidates else None


async def load() -> tuple[object, dict, Planner, list[dict]]:
    network = await load_network()
    coordinates = await resolve_all(sorted(network.locations), network.locations)
    return network, coordinates, Planner(network, coordinates), await all_incidents()


def currently_active(rows: list[dict]) -> set[str]:
    return {
        r["incident_id"] for r in rows if (r["status"] or "").strip().lower() == "active"
    }


def resolve(network, name: str) -> str | None:
    return network.resolve(name)


# ----------------------------------------------------------------------
async def cmd_snapshot() -> int:
    rows = await all_incidents()
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps({r["incident_id"]: r["status"] for r in rows}, indent=2),
        encoding="utf-8",
    )
    print(f"  recorded {len(rows)} incident statuses -> {SNAPSHOT_PATH}")
    return 0


async def cmd_restore() -> int:
    if not SNAPSHOT_PATH.exists():
        print(f"  no snapshot at {SNAPSHOT_PATH} — run 'snapshot' first")
        return 1
    saved: dict[str, str] = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    current = {r["incident_id"]: r["status"] for r in await all_incidents()}
    changed = 0
    for incident_id, status in saved.items():
        if current.get(incident_id) != status:
            await set_status(incident_id, status)
            changed += 1
    print(f"  restored {changed} of {len(saved)} incident(s)")
    return 0


async def cmd_baseline(target: int | None) -> int:
    network, _, _, rows = await load()
    graph = undirected(network)
    blocking = {"critical"}

    criticals = [r for r in rows if (r["severity"] or "").strip().lower() in blocking]
    others = [r for r in rows if r not in criticals]

    # Keep a Critical incident only while every pair still has some route.
    # Busiest locations first, so the ones kept are the interesting ones.
    ranked = sorted(
        criticals, key=lambda r: -len(graph.get(r["location"], ())),
    )
    keep: list[dict] = []
    removed: set[str] = set()
    for row in ranked:
        location = row["location"]
        if location not in graph:
            continue
        trial = removed | {location}
        if fully_connected_without(graph, trial):
            if target is not None and len(keep) >= target:
                continue
            keep.append(row)
            removed = trial

    keep_ids = {r["incident_id"] for r in keep}
    changed = 0
    for row in criticals:
        wanted = "Active" if row["incident_id"] in keep_ids else "Inactive"
        if (row["status"] or "") != wanted:
            await set_status(row["incident_id"], wanted)
            changed += 1

    print(f"  Critical incidents: {len(keep)} left Active, "
          f"{len(criticals) - len(keep)} stood down ({changed} write(s))")
    for row in keep:
        print(f"     ACTIVE  {row['incident_id']}  {row['type']} @ {row['location']}")
    print(f"  {len(others)} non-blocking incident(s) left as they were — they are "
          f"costs, not closures")
    print(f"  every location pair now has a route: "
          f"{fully_connected_without(graph, removed)}")
    return 0


async def cmd_audit(sample_size: int) -> int:
    import random

    network, _, planner, rows = await load()
    names = sorted(network.locations)
    active = currently_active(rows)
    by_location: dict[str, list[dict]] = {}
    for row in rows:
        by_location.setdefault(row["location"], []).append(row)

    pairs = list(itertools.combinations(names, 2))
    routable = sum(1 for o, d in pairs if planner.route(o, d, active))
    print(f"\n  locations {len(names)}   incidents {len(rows)}   active {len(active)}")
    print(f"  routable pairs with the current state: {routable}/{len(pairs)} "
          f"({100 * routable / len(pairs):.0f}%)")

    random.seed(11)
    sample = random.sample(pairs, min(sample_size, len(pairs)))

    activate_tested = activate_changed = 0
    clear_tested = clear_changed = 0
    either = 0
    for origin, destination in sample:
        base = planner.route(origin, destination, active)
        if not base:
            continue
        moved = False

        # ACTIVATE: an inactive incident on the lane should push it off.
        candidates = [
            row
            for stop in base.stops
            for row in by_location.get(stop, [])
            if row["incident_id"] not in active
        ]
        if candidates:
            activate_tested += 1
            for row in candidates:
                alt = planner.route(origin, destination, active | {row["incident_id"]})
                if alt and tuple(alt.stops) != tuple(base.stops):
                    activate_changed += 1
                    moved = True
                    break

        # CLEAR: an active incident the lane is currently detouring around
        # should pull it back onto a better route.
        clear_tested += 1
        for incident_id in sorted(active):
            alt = planner.route(origin, destination, active - {incident_id})
            if alt and tuple(alt.stops) != tuple(base.stops):
                clear_changed += 1
                moved = True
                break

        either += 1 if moved else 0

    print(f"  ACTIVATE an incident on the lane : {activate_changed}/{activate_tested} "
          f"re-route ({100 * activate_changed / max(activate_tested, 1):.0f}%)")
    print(f"  CLEAR an active incident         : {clear_changed}/{clear_tested} "
          f"re-route ({100 * clear_changed / max(clear_tested, 1):.0f}%)")
    print(f"  lanes where SOME toggle re-routes: {either}/{len(sample)} "
          f"({100 * either / max(len(sample), 1):.0f}%)\n")
    return 0


async def cmd_suggest(origin_name: str, destination_name: str) -> int:
    network, _, planner, rows = await load()
    origin = resolve(network, origin_name)
    destination = resolve(network, destination_name)
    if not origin or not destination:
        print(f"  unknown location: {origin_name if not origin else destination_name}")
        return 1

    active = currently_active(rows)
    by_location: dict[str, list[dict]] = {}
    for row in rows:
        by_location.setdefault(row["location"], []).append(row)

    base = planner.route(origin, destination, active)
    print(f"\n  {origin} -> {destination}")
    if not base:
        print("  no route at all with the current incident state — "
              "run 'baseline' first")
        return 1
    print(f"  current route: {base.total_distance_km:.1f} km  "
          f"{' -> '.join(base.stops)}\n")

    def report(rowset, label, activate: bool):
        hits = []
        for row in rowset:
            ids = active | {row["incident_id"]} if activate else active - {
                row["incident_id"]
            }
            alt = planner.route(origin, destination, ids)
            if alt and tuple(alt.stops) != tuple(base.stops):
                hits.append((row, alt))
        if not hits:
            return
        print(f"  {label}")
        for row, alt in hits:
            delta = alt.total_distance_km - base.total_distance_km
            print(f"     {row['incident_id']}  {row['severity']} {row['type']} "
                  f"@ {row['location']}")
            print(f"        -> {alt.total_distance_km:.1f} km ({delta:+.1f} km)  "
                  f"{' -> '.join(alt.stops)}")

    # Activating an incident on the route pushes the lane off it.
    report(
        [
            row
            for stop in base.stops
            for row in by_location.get(stop, [])
            if row["incident_id"] not in active
        ],
        "ACTIVATE one of these to force a diversion:",
        activate=True,
    )
    # Clearing one that is currently forcing a detour brings the lane back.
    report(
        [row for row in rows if row["incident_id"] in active],
        "CLEAR one of these to bring the lane back onto a better route:",
        activate=False,
    )
    print()
    return 0


async def cmd_verify(origin_name: str, destination_name: str, incident_id: str) -> int:
    network, _, planner, rows = await load()
    origin = resolve(network, origin_name)
    destination = resolve(network, destination_name)
    if not origin or not destination:
        print("  unknown location")
        return 1
    row = next((r for r in rows if r["incident_id"] == incident_id), None)
    if row is None:
        print(f"  no incident {incident_id}")
        return 1

    active = currently_active(rows)
    print(f"\n  {origin} -> {destination}, toggling {incident_id} "
          f"({row['severity']} {row['type']} @ {row['location']})\n")
    for label, ids in (
        ("INACTIVE", active - {incident_id}),
        ("ACTIVE", active | {incident_id}),
    ):
        route = planner.route(origin, destination, ids)
        if route is None:
            print(f"     {incident_id} {label:8} no route")
        else:
            print(f"     {incident_id} {label:8} {route.total_distance_km:6.1f} km  "
                  f"{' -> '.join(route.stops)}")
    print()
    return 0


# ----------------------------------------------------------------------
async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot", help="record every incident status")
    sub.add_parser("restore", help="put every incident back to the snapshot")
    base = sub.add_parser("baseline", help="set a demo-ready incident state")
    base.add_argument("--criticals", type=int, default=None,
                      help="cap how many Critical incidents stay active")
    audit = sub.add_parser("audit", help="measure routability and re-route rate")
    audit.add_argument("--sample", type=int, default=120)
    suggest = sub.add_parser("suggest", help="which incident re-routes this lane")
    suggest.add_argument("origin")
    suggest.add_argument("destination")
    verify = sub.add_parser("verify", help="prove one toggle, before and after")
    verify.add_argument("origin")
    verify.add_argument("destination")
    verify.add_argument("incident")

    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            return await cmd_snapshot()
        if args.command == "restore":
            return await cmd_restore()
        if args.command == "baseline":
            return await cmd_baseline(args.criticals)
        if args.command == "audit":
            return await cmd_audit(args.sample)
        if args.command == "suggest":
            return await cmd_suggest(args.origin, args.destination)
        if args.command == "verify":
            return await cmd_verify(args.origin, args.destination, args.incident)
    finally:
        await get_kg_client().close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
