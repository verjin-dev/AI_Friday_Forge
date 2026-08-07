"""Ingest the LogiPilot AI road-network CSVs into Neo4j.

Deliberately separate from the agent runtime: the Knowledge Agent is read-only
by design, and all writes go through this governed pipeline.

Usage::

    python scripts/load_graph.py                 # load from data/csv
    python scripts/load_graph.py --dir path/to   # load from elsewhere
    python scripts/load_graph.py --reset         # wipe Location/Incident first
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path
from typing import Any

# Allow `python scripts/load_graph.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.kg.client import get_kg_client  # noqa: E402


logger = get_logger("load_graph")

CONSTRAINTS = (
    "CREATE CONSTRAINT location_id_unique IF NOT EXISTS "
    "FOR (l:Location) REQUIRE l.location_id IS UNIQUE",
    "CREATE CONSTRAINT incident_id_unique IF NOT EXISTS "
    "FOR (i:Incident) REQUIRE i.incident_id IS UNIQUE",
    "CREATE CONSTRAINT vehicle_profile_unique IF NOT EXISTS "
    "FOR (v:VehicleProfile) REQUIRE v.profile_id IS UNIQUE",
)

#: A full-text index turns the Search Agent's hybrid retrieval on for this graph.
INDEXES = (
    "CREATE FULLTEXT INDEX logipilot_search IF NOT EXISTS "
    "FOR (n:Location|Incident) ON EACH "
    "[n.name, n.type, n.severity, n.status, n.location_id, n.incident_id]",
)


#: Accepted file names per logical dataset — the delivered files and the names
#: used in the original schema document both work.
FILE_ALIASES: dict[str, tuple[str, ...]] = {
    "locations": ("locations.csv", "location_nodes.csv"),
    "incidents": ("incidents.csv", "incident_nodes.csv"),
    "roads": ("road_connections.csv", "location_relationships.csv"),
    "incident_locations": ("incident_locations.csv",),
    "alternates": ("alternate_routes.csv",),
}

#: Accepted column names per field.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "from_location": ("from_location", "from", "source", "start"),
    "to_location": ("to_location", "to", "target", "end"),
    "location": ("location", "location_name", "at"),
    "is_near_tvm": ("is_near_tvm", "near_tvm"),
    "extra_distance": ("extra_distance", "extra_distance_km"),
    "distance_km": ("distance_km", "distance"),
    "road_name": ("road_name", "road"),
}


def normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map whichever column spelling the file uses onto canonical names."""

    lowered = {
        (key or "").strip().lower(): (value or "").strip()
        for key, value in row.items()
        if key
    }
    result: dict[str, Any] = dict(lowered)
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered and lowered[alias] != "":
                result[canonical] = lowered[alias]
                break
    return result


def read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [normalise_row(row) for row in csv.DictReader(handle)]
    logger.info("Read CSV", extra={"file": path.name, "rows": len(rows)})
    return rows


def load_fleet_profiles(directory: Path) -> list[dict[str, Any]]:
    """Typed vehicle profiles, flattened for storage as node properties."""

    from app.domain.fleet import parse_profiles

    matches = sorted(directory.rglob("missing_data_template.csv"))
    if not matches:
        print(f"  {'vehicle_profiles':<20} <- NOT FOUND")
        return []

    chosen = max(matches, key=lambda p: p.stat().st_mtime)
    print(f"  {'vehicle_profiles':<20} <- {chosen.relative_to(directory.parent)}")

    rows: list[dict[str, Any]] = []
    for profile in parse_profiles(chosen):
        row = {
            key: value
            for key, value in profile.model_dump().items()
            if value is not None
        }
        row["required_licence"] = profile.required_licence
        rows.append(row)
    return rows


def find_dataset(directory: Path, dataset: str) -> list[dict[str, Any]]:
    """Locate a dataset by any of its accepted names, searching subdirectories."""

    for name in FILE_ALIASES[dataset]:
        direct = directory / name
        if direct.exists():
            print(f"  {dataset:<20} <- {direct.relative_to(directory.parent)}")
            return read_csv(direct)

    for name in FILE_ALIASES[dataset]:
        matches = sorted(directory.rglob(name))
        if matches:
            chosen = max(matches, key=lambda p: p.stat().st_mtime)
            print(f"  {dataset:<20} <- {chosen.relative_to(directory.parent)}")
            return read_csv(chosen)

    logger.warning("Dataset not found", extra={"dataset": dataset})
    print(f"  {dataset:<20} <- NOT FOUND")
    return []


async def run_write(session, cypher: str, parameters: dict[str, Any] | None = None):
    return await session.run(cypher, parameters or {})


async def load(directory: Path, *, reset: bool) -> dict[str, int]:
    client = get_kg_client()
    driver = await client.driver()
    counts: dict[str, int] = {}

    async with driver.session(database=settings.neo4j_database) as session:
        for statement in CONSTRAINTS:
            await run_write(session, statement)

        if reset:
            logger.warning("Resetting Location and Incident nodes")
            await run_write(
                session,
                "MATCH (n) WHERE n:Location OR n:Incident OR n:VehicleProfile "
                "DETACH DELETE n",
            )

        # --- nodes ---
        print("Resolving datasets:")
        locations = find_dataset(directory, "locations")
        if locations:
            await run_write(
                session,
                """
                UNWIND $rows AS row
                MERGE (l:Location {location_id: row.location_id})
                SET l.name = row.name,
                    l.type = row.type,
                    l.district = row.district,
                    l.zone = row.zone,
                    l.is_near_tvm = coalesce(row.is_near_tvm, 'Unknown')
                """,
                {"rows": locations},
            )
        counts["locations"] = len(locations)

        incidents = find_dataset(directory, "incidents")
        if incidents:
            await run_write(
                session,
                """
                UNWIND $rows AS row
                MERGE (i:Incident {incident_id: row.incident_id})
                SET i.type = row.type,
                    i.severity = row.severity,
                    i.status = row.status,
                    i.impact_level = row.impact_level
                """,
                {"rows": incidents},
            )
        counts["incidents"] = len(incidents)

        # --- relationships ---
        roads = find_dataset(directory, "roads")
        if roads:
            await run_write(
                session,
                """
                UNWIND $rows AS row
                MATCH (from:Location {name: row.from_location})
                MATCH (to:Location {name: row.to_location})
                MERGE (from)-[r:CONNECTED_TO]->(to)
                SET r.distance_km = toFloat(row.distance_km),
                    r.road_name = row.road_name,
                    r.relation_id = row.relation_id,
                    r.relation_type = coalesce(row.relation_type, 'CONNECTS')
                """,
                {"rows": roads},
            )
        counts["road_connections"] = len(roads)

        incident_links = find_dataset(directory, "incident_locations")
        if incident_links:
            await run_write(
                session,
                """
                UNWIND $rows AS row
                MATCH (i:Incident {incident_id: row.incident_id})
                MATCH (l:Location {name: row.location})
                MERGE (i)-[r:HAS_INCIDENT]->(l)
                SET r.zone = row.zone,
                    r.affected_route = row.affected_route,
                    r.incident_time = row.incident_time,
                    r.traffic_peak_period = row.traffic_peak_period,
                    r.description = row.incidents
                """,
                {"rows": incident_links},
            )
        counts["incident_locations"] = len(incident_links)

        # --- fleet profiles ---
        profiles = load_fleet_profiles(directory)
        if profiles:
            await run_write(
                session,
                """
                UNWIND $rows AS row
                MERGE (v:VehicleProfile {profile_id: row.profile_id})
                SET v += row
                """,
                {"rows": profiles},
            )
        counts["vehicle_profiles"] = len(profiles)

        alternates = find_dataset(directory, "alternates")
        if alternates:
            await run_write(
                session,
                """
                UNWIND $rows AS row
                MATCH (from:Location {name: row.from_location})
                MATCH (to:Location {name: row.to_location})
                MERGE (from)-[r:ALTERNATE_ROUTE]->(to)
                SET r.via = row.via,
                    r.extra_distance = toFloat(row.extra_distance),
                    r.route_type = row.route_type,
                    r.remarks = row.remarks
                """,
                {"rows": alternates},
            )
        counts["alternate_routes"] = len(alternates)

        for statement in INDEXES:
            try:
                await run_write(session, statement)
            except Exception as exc:  # noqa: BLE001 - index support varies by edition
                logger.warning(
                    "Could not create index", extra={"error": str(exc)[:200]}
                )

    return counts


async def verify() -> None:
    client = get_kg_client()
    rows = await client.try_run(
        "MATCH (l:Location) WITH count(l) AS locations "
        "MATCH (i:Incident) WITH locations, count(i) AS incidents "
        "OPTIONAL MATCH ()-[r:CONNECTED_TO]->() "
        "WITH locations, incidents, count(r) AS roads "
        "OPTIONAL MATCH ()-[a:ALTERNATE_ROUTE]->() "
        "RETURN locations, incidents, roads, count(a) AS alternates"
    )
    if rows:
        logger.info("Graph contents", extra=rows[0])
        print("Loaded:", rows[0])

    active = await client.try_run(
        "MATCH (i:Incident {status:'Active'})-[:HAS_INCIDENT]->(l:Location) "
        "RETURN i.severity AS severity, i.type AS type, l.name AS location "
        "ORDER BY severity"
    )
    if active:
        print("Active incidents:")
        for row in active:
            print(f"  - {row['severity']:<8} {row['type']} at {row['location']}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Load the LogiPilot AI road network.")
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data",
        help="Directory containing the CSV files (searched recursively).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing Location and Incident nodes before loading.",
    )
    args = parser.parse_args()

    configure_logging(settings.log_level, json_output=False)

    if not settings.neo4j_password:
        print("NEO4J_PASSWORD is not set in .env — cannot connect.", file=sys.stderr)
        return 1

    try:
        counts = await load(args.dir, reset=args.reset)
    except Exception as exc:  # noqa: BLE001
        print(f"Load failed: {exc}", file=sys.stderr)
        return 1

    print("Ingested rows:", counts)
    await verify()
    await get_kg_client().close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
