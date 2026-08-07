"""Generate realistic real-time incidents and load them directly into Neo4j and data/csv.

Usage:
    python scripts/generate_realtime_incidents.py
"""

from __future__ import annotations

import asyncio
import csv
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.logging import configure_logging, get_logger
from app.kg.client import get_kg_client

logger = get_logger("generate_realtime_incidents")

KERALA_LOCATIONS = [
    ("Kazhakkoottam", "TVM North", "NH66"),
    ("Attingal", "TVM North", "NH66"),
    ("Venjaramoodu", "TVM Inland", "MC Road"),
    ("East Fort", "TVM Core", "MG Road"),
    ("Neyyattinkara", "TVM South", "NH66"),
    ("Pattom", "TVM Core", "City Road"),
    ("Balaramapuram", "TVM South", "NH66"),
    ("Nedumangad", "TVM Inland", "SH2"),
    ("Sreekaryam", "TVM North", "Medical College Road"),
    ("Varkala", "TVM North", "NH66"),
    ("Paravur", "Kollam North", "NH66"),
    ("Kollam", "Kollam Core", "NH66"),
    ("Kottarakkara", "Kollam Inland", "MC Road"),
    ("Chathannoor", "Kollam North", "NH66"),
    ("Kundara", "Kollam Inland", "MC Road"),
    ("Karunagappally", "Kollam North", "NH66"),
    ("Kayamkulam", "Alappuzha North", "NH66"),
    ("Haripad", "Alappuzha North", "NH66"),
    ("Ambalappuzha", "Alappuzha Core", "NH66"),
    ("Alappuzha", "Alappuzha Core", "NH66"),
    ("Cherthala", "Alappuzha South", "NH66"),
    ("Aroor", "Alappuzha South", "NH66"),
    ("Thakazhy", "Alappuzha Inland", "AC Road"),
    ("Changanassery", "Kottayam West", "MC Road"),
    ("Kottayam", "Kottayam Core", "MC Road"),
    ("Ettumanoor", "Kottayam North", "MC Road"),
    ("Pala", "Kottayam East", "SH32"),
    ("Kalathipady", "Kottayam Core", "KK Road"),
    ("Mundakayam", "Kottayam East", "KK Road"),
    ("Kanjirappally", "Kottayam East", "KK Road"),
    ("Kumarakom", "Kottayam West", "Kumarakom Road"),
    ("Vaikom", "Kottayam West", "Vaikom Road"),
    ("Kaduthuruthy", "Kottayam North", "MC Road"),
    ("Thalayolaparambu", "Kottayam North", "SH15"),
    ("Mavelikara", "Alappuzha Inland", "MC Road"),
    ("Chengannur", "Alappuzha Inland", "MC Road"),
    ("Sasthamkotta", "Kollam Inland", "State Highway"),
    ("Parassala", "TVM South", "NH66"),
    ("Chirayinkeezhu", "TVM North", "Coastal Road"),
    ("Kilimanoor", "TVM Inland", "MC Road"),
    ("Kallambalam", "TVM North", "NH66"),
    ("Kottiyam", "Kollam North", "NH66"),
    ("Ochira", "Kollam North", "NH66"),
    ("Pathanapuram", "Kollam Inland", "State Highway"),
    ("Anchal", "Kollam Inland", "State Highway"),
    ("Pathanamthitta", "Pathanamthitta", "SH1"),
    ("Adoor", "Pathanamthitta", "MC Road"),
    ("Punalur", "Kollam Inland", "State Highway"),
    ("Thiruvananthapuram", "TVM Core", "City Network"),
]

INCIDENT_TYPES = [
    ("Accident", "Emergency Response", "Collision blocking major lane"),
    ("Heavy Rain", "Flood Risk", "Heavy downpour causing waterlogging"),
    ("Road Work", "Lane Diversion", "Resurfacing and expansion work"),
    ("Waterlogging", "Slow Movement", "Urban drainage overflow"),
    ("Vehicle Breakdown", "Partial Block", "Freight truck axle failure"),
    ("Landslip", "Route Closed", "Mudslide blocking ghat road"),
    ("Bridge Repair", "Restricted Flow", "Structural maintenance on bridge"),
    ("Flash Flood", "Severe Disruption", "Submerged roadway impassable"),
    ("Signal Failure", "Junction Delay", "Traffic signal outage"),
]

# Ensure a good mix of Critical and High severities so blocking is obvious
SEVERITIES = ["Critical", "Critical", "High", "High", "High", "Medium", "Medium"]
STATUSES = ["Active", "Active", "Active", "Active", "Active", "Planned", "Resolved"]
PEAK_PERIODS = ["High Peak", "Mid Peak", "Off Peak"]


def generate_incidents_data():
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    node_rows = []
    location_rows = []

    for idx, (loc, zone, route) in enumerate(KERALA_LOCATIONS, start=1):
        inc_id = f"I{idx:03d}"
        itype, impact, desc_prefix = random.choice(INCIDENT_TYPES)
        severity = random.choice(SEVERITIES)
        status = random.choice(STATUSES)
        peak = random.choice(PEAK_PERIODS)

        description = f"{desc_prefix} at {loc} on {route}"

        node_rows.append(
            {
                "incident_id": inc_id,
                "type": itype,
                "severity": severity,
                "status": status,
                "impact_level": impact,
            }
        )

        location_rows.append(
            {
                "incident_id": inc_id,
                "location": loc,
                "zone": zone,
                "affected_route": route,
                "incidents": description,
                "incident_time": now_iso,
                "traffic_peak_period": peak,
            }
        )

    return node_rows, location_rows


async def upload_to_neo4j(node_rows, location_rows):
    client = get_kg_client()
    driver = await client.driver()

    async with driver.session() as session:
        # 1. Update Incident nodes
        await session.run(
            """
            UNWIND $rows AS row
            MERGE (i:Incident {incident_id: row.incident_id})
            SET i.type = row.type,
                i.severity = row.severity,
                i.status = row.status,
                i.impact_level = row.impact_level
            """,
            {"rows": node_rows},
        )

        # 2. Link Incident to Location
        await session.run(
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
            {"rows": location_rows},
        )

    logger.info("Realtime incidents uploaded to Neo4j", extra={"incidents": len(node_rows)})


def save_csvs(node_rows, location_rows):
    csv_dir = PROJECT_ROOT / "data" / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    nodes_file = csv_dir / "incident_nodes.csv"
    with nodes_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["incident_id", "type", "severity", "status", "impact_level"]
        )
        writer.writeheader()
        writer.writerows(node_rows)

    locs_file = csv_dir / "incident_locations.csv"
    with locs_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "incident_id",
                "location",
                "zone",
                "affected_route",
                "incidents",
                "incident_time",
                "traffic_peak_period",
            ],
        )
        writer.writeheader()
        writer.writerows(locs_file_rows := location_rows)

    logger.info("CSVs updated", extra={"nodes": str(nodes_file), "locations": str(locs_file)})


async def main():
    print("Generating real-time incidents data...")
    node_rows, location_rows = generate_incidents_data()
    save_csvs(node_rows, location_rows)
    await upload_to_neo4j(node_rows, location_rows)
    print(f"Successfully generated and uploaded {len(node_rows)} real-time incidents to Neo4j and CSVs!")


if __name__ == "__main__":
    asyncio.run(main())
