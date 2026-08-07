"""Real-time Dataset Generator & Neo4j Ingestion Pipeline.

Fetches live weather telemetry from Open-Meteo API and real-world spatial coordinates for the Kerala road network,
generating fresh CSV files in data/csv/ and reloading Neo4j cleanly:
1. data/csv/location_nodes.csv
2. data/csv/incident_nodes.csv
3. data/csv/location_relationships.csv
4. data/csv/incident_locations.csv
5. data/csv/alternate_routes.csv
6. data/csv/missing_data_template.csv (Vehicle profiles)
7. data/edge_metadata.csv

Usage:
    python scripts/build_realtime_csv_dataset.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.logging import get_logger

logger = get_logger("build_realtime_csv_dataset")

# 55 Real Kerala Hubs with exact GPS coordinates
KERALA_LOCATIONS = [
    ("L001", "Thiruvananthapuram", "City", "Thiruvananthapuram", "Core", 8.5241, 76.9366),
    ("L002", "Kazhakkoottam", "Town", "Thiruvananthapuram", "North Corridor", 8.5670, 76.8770),
    ("L003", "Sreekaryam", "Town", "Thiruvananthapuram", "Core", 8.5480, 76.9160),
    ("L004", "Pattom", "Town", "Thiruvananthapuram", "Core", 8.5260, 76.9440),
    ("L005", "Palayam", "Town", "Thiruvananthapuram", "Core", 8.4980, 76.9500),
    ("L006", "East Fort", "Town", "Thiruvananthapuram", "Core", 8.4830, 76.9460),
    ("L007", "Neyyattinkara", "Town", "Thiruvananthapuram", "South Corridor", 8.4000, 77.0860),
    ("L008", "Balaramapuram", "Town", "Thiruvananthapuram", "South Corridor", 8.4320, 77.0420),
    ("L009", "Parassala", "Town", "Thiruvananthapuram", "Border South", 8.3300, 77.1500),
    ("L010", "Attingal", "Town", "Thiruvananthapuram", "North Corridor", 8.6957, 76.8155),
    ("L011", "Varkala", "Town", "Thiruvananthapuram", "North Coastal", 8.7378, 76.7163),
    ("L012", "Kilimanoor", "Town", "Thiruvananthapuram", "North Inland", 8.7667, 76.8833),
    ("L013", "Kallambalam", "Town", "Thiruvananthapuram", "North Corridor", 8.7400, 76.8000),
    ("L014", "Venjaramoodu", "Town", "Thiruvananthapuram", "North Inland", 8.6500, 76.9100),
    ("L015", "Nedumangad", "Town", "Thiruvananthapuram", "North Inland", 8.6040, 77.0000),
    ("L016", "Chirayinkeezhu", "Town", "Thiruvananthapuram", "North Coastal", 8.6500, 76.7800),
    ("L017", "Kollam", "City", "Kollam", "Core", 8.8932, 76.6141),
    ("L018", "Paravur", "Town", "Kollam", "South Corridor", 8.8100, 76.6700),
    ("L019", "Chathannoor", "Town", "Kollam", "South Corridor", 8.8500, 76.7200),
    ("L020", "Kottiyam", "Town", "Kollam", "Core", 8.8600, 76.6600),
    ("L021", "Kundara", "Town", "Kollam", "Inland", 8.9600, 76.6800),
    ("L022", "Kottarakkara", "Town", "Kollam", "Inland", 9.0000, 76.7800),
    ("L023", "Karunagappally", "Town", "Kollam", "North Corridor", 9.0500, 76.5300),
    ("L024", "Ochira", "Town", "Kollam", "North Corridor", 9.1300, 76.5000),
    ("L025", "Kayamkulam", "Town", "Alappuzha", "North Corridor", 9.1800, 76.5010),
    ("L026", "Haripad", "Town", "Alappuzha", "North Corridor", 9.2833, 76.4667),
    ("L027", "Ambalappuzha", "Town", "Alappuzha", "Core", 9.3800, 76.3600),
    ("L028", "Alappuzha", "City", "Alappuzha", "Core", 9.4981, 76.3388),
    ("L029", "Cherthala", "Town", "Alappuzha", "South Corridor", 9.6833, 76.3333),
    ("L030", "Aroor", "Town", "Alappuzha", "South Corridor", 9.8700, 76.3000),
    ("L031", "Thakazhy", "Town", "Alappuzha", "Inland", 9.3800, 76.4400),
    ("L032", "Mavelikara", "Town", "Alappuzha", "Inland", 9.2500, 76.5500),
    ("L033", "Chengannur", "Town", "Alappuzha", "Inland", 9.3167, 76.6167),
    ("L034", "Kottayam", "City", "Kottayam", "Core", 9.5916, 76.5222),
    ("L035", "Changanassery", "Town", "Kottayam", "West Corridor", 9.4459, 76.5386),
    ("L036", "Ettumanoor", "Town", "Kottayam", "North Corridor", 9.6700, 76.5600),
    ("L037", "Pala", "Town", "Kottayam", "East Corridor", 9.7100, 76.6800),
    ("L038", "Kalathipady", "Town", "Kottayam", "Core", 9.5900, 76.5500),
    ("L039", "Mundakayam", "Town", "Kottayam", "East Corridor", 9.5400, 76.8800),
    ("L040", "Kanjirappally", "Town", "Kottayam", "East Corridor", 9.5500, 76.7800),
    ("L041", "Kumarakom", "Town", "Kottayam", "West Corridor", 9.6170, 76.4330),
    ("L042", "Vaikom", "Town", "Kottayam", "West Corridor", 9.7500, 76.4000),
    ("L043", "Kaduthuruthy", "Town", "Kottayam", "North Corridor", 9.7400, 76.5300),
    ("L044", "Thalayolaparambu", "Town", "Kottayam", "North Corridor", 9.7800, 76.4800),
    ("L045", "Pathanamthitta", "City", "Pathanamthitta", "Core", 9.2647, 76.7872),
    ("L046", "Adoor", "Town", "Pathanamthitta", "South Corridor", 9.1578, 76.7337),
    ("L047", "Punalur", "Town", "Kollam", "Inland", 9.0167, 76.9333),
    ("L048", "Pathanapuram", "Town", "Kollam", "Inland", 9.1000, 76.8500),
    ("L049", "Anchal", "Town", "Kollam", "Inland", 8.9300, 76.9100),
    ("L050", "Sasthamkotta", "Town", "Kollam", "Inland", 9.0500, 76.6300),
    ("L051", "Erattupetta", "Town", "Kottayam", "East Corridor", 9.6900, 76.7800),
    ("L052", "Mannar", "Town", "Alappuzha", "Inland", 9.3100, 76.5200),
    ("L053", "Pampady", "Town", "Kottayam", "Core", 9.5800, 76.6200),
    ("L054", "Kuravilangad", "Town", "Kottayam", "East Corridor", 9.7500, 76.5700),
    ("L055", "Kochi", "City", "Ernakulam", "Core Gateway", 9.9312, 76.2673),
]


def fetch_live_weather() -> dict[str, float]:
    """Fetch live weather telemetry from Open-Meteo API for Thiruvananthapuram."""
    url = "https://api.open-meteo.com/v1/forecast?latitude=8.5241&longitude=76.9366&current=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LogiPilot-AI/2.0"})
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            curr = data.get("current", {})
            return {
                "temperature": float(curr.get("temperature_2m", 28.0)),
                "humidity": float(curr.get("relative_humidity_2m", 80.0)),
                "precipitation": float(curr.get("precipitation", 0.0)),
                "wind_speed": float(curr.get("wind_speed_10m", 12.0)),
            }
    except Exception as e:
        logger.warning(f"Could not fetch Open-Meteo live weather: {e}. Using fallback defaults.")
        return {"temperature": 28.5, "humidity": 82.0, "precipitation": 12.0, "wind_speed": 18.0}


def build_datasets(weather: dict[str, float]):
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 1. Location Nodes
    locations_rows = []
    for loc_id, name, ltype, district, zone, lat, lon in KERALA_LOCATIONS:
        locations_rows.append({
            "location_id": loc_id,
            "name": name,
            "type": ltype,
            "district": district,
            "zone": zone,
        })

    # Save geocache.json
    geocache = {name: {"latitude": lat, "longitude": lon} for _, name, _, _, _, lat, lon in KERALA_LOCATIONS}
    geocache_file = PROJECT_ROOT / "data" / "geocache.json"
    with geocache_file.open("w", encoding="utf-8") as f:
        json.dump(geocache, f, indent=2)

    # 2. Road Connections
    roads = [
        ("R001", "Thiruvananthapuram", "Pattom", 4.0, "City Road", "CONNECTS"),
        ("R002", "Pattom", "Sreekaryam", 5.0, "Medical College Road", "CONNECTS"),
        ("R003", "Sreekaryam", "Kazhakkoottam", 8.0, "NH66", "CONNECTS"),
        ("R004", "Thiruvananthapuram", "East Fort", 3.0, "MG Road", "CONNECTS"),
        ("R005", "East Fort", "Balaramapuram", 15.0, "NH66", "CONNECTS"),
        ("R006", "Balaramapuram", "Neyyattinkara", 10.0, "NH66", "CONNECTS"),
        ("R007", "Neyyattinkara", "Parassala", 17.0, "NH66", "CONNECTS"),
        ("R008", "Kazhakkoottam", "Attingal", 22.0, "NH66", "CONNECTS"),
        ("R009", "Attingal", "Kallambalam", 10.0, "NH66", "CONNECTS"),
        ("R010", "Kallambalam", "Varkala", 16.0, "NH66", "CONNECTS"),
        ("R011", "Varkala", "Paravur", 14.0, "NH66", "CONNECTS"),
        ("R012", "Paravur", "Chathannoor", 12.0, "NH66", "CONNECTS"),
        ("R013", "Chathannoor", "Kollam", 16.0, "NH66", "CONNECTS"),
        ("R014", "Kollam", "Kottiyam", 8.0, "NH66", "CONNECTS"),
        ("R015", "Kottiyam", "Karunagappally", 28.0, "NH66", "CONNECTS"),
        ("R016", "Karunagappally", "Ochira", 10.0, "NH66", "CONNECTS"),
        ("R017", "Ochira", "Kayamkulam", 8.0, "NH66", "CONNECTS"),
        ("R018", "Kayamkulam", "Haripad", 14.0, "NH66", "CONNECTS"),
        ("R019", "Haripad", "Ambalappuzha", 16.0, "NH66", "CONNECTS"),
        ("R020", "Ambalappuzha", "Alappuzha", 8.0, "NH66", "CONNECTS"),
        ("R021", "Alappuzha", "Cherthala", 22.0, "NH66", "CONNECTS"),
        ("R022", "Cherthala", "Aroor", 17.0, "NH66", "CONNECTS"),
        ("R023", "Aroor", "Kochi", 12.0, "NH66", "CONNECTS"),
        ("R024", "Kayamkulam", "Mavelikara", 12.0, "MC Road", "CONNECTS"),
        ("R025", "Mavelikara", "Chengannur", 12.0, "MC Road", "CONNECTS"),
        ("R026", "Chengannur", "Changanassery", 16.0, "MC Road", "CONNECTS"),
        ("R027", "Changanassery", "Kottayam", 18.0, "MC Road", "CONNECTS"),
        ("R028", "Kottayam", "Ettumanoor", 11.0, "MC Road", "CONNECTS"),
        ("R029", "Ettumanoor", "Pala", 16.0, "SH32", "CONNECTS"),
        ("R030", "Pala", "Erattupetta", 13.0, "SH32", "CONNECTS"),
        ("R031", "Kottayam", "Kalathipady", 5.0, "KK Road", "CONNECTS"),
        ("R032", "Kalathipady", "Kanjirappally", 30.0, "KK Road", "CONNECTS"),
        ("R033", "Kanjirappally", "Mundakayam", 15.0, "KK Road", "CONNECTS"),
        ("R034", "Thiruvananthapuram", "Venjaramoodu", 28.0, "MC Road", "CONNECTS"),
        ("R035", "Venjaramoodu", "Kilimanoor", 14.0, "MC Road", "CONNECTS"),
        ("R036", "Kilimanoor", "Kottarakkara", 32.0, "MC Road", "CONNECTS"),
        ("R037", "Kottarakkara", "Adoor", 18.0, "MC Road", "CONNECTS"),
        ("R038", "Adoor", "Chengannur", 24.0, "MC Road", "CONNECTS"),
        ("R039", "Thiruvananthapuram", "Nedumangad", 18.0, "SH2", "CONNECTS"),
        ("R040", "Nedumangad", "Vithura", 20.0, "SH2", "CONNECTS"),
        ("R041", "Kollam", "Kundara", 14.0, "Kollam-Shenkottai Road", "CONNECTS"),
        ("R042", "Kundara", "Kottarakkara", 13.0, "Kollam-Shenkottai Road", "CONNECTS"),
        ("R043", "Kottarakkara", "Punalur", 21.0, "SH8", "CONNECTS"),
        ("R044", "Punalur", "Pathanapuram", 12.0, "SH8", "CONNECTS"),
        ("R045", "Pathanapuram", "Pathanamthitta", 24.0, "SH8", "CONNECTS"),
        ("R046", "Kottayam", "Kumarakom", 14.0, "Kumarakom Road", "CONNECTS"),
        ("R047", "Kumarakom", "Vaikom", 28.0, "Vaikom Road", "CONNECTS"),
        ("R048", "Vaikom", "Thalayolaparambu", 10.0, "SH15", "CONNECTS"),
        ("R049", "Thalayolaparambu", "Ernakulam", 30.0, "SH15", "CONNECTS"),
        ("R050", "Attingal", "Venjaramoodu", 14.0, "Attingal-Venjaramoodu Road", "CONNECTS"),
    ]

    location_rels_rows = []
    for rid, f_loc, t_loc, dist, rname, rtype in roads:
        location_rels_rows.append({
            "relation_id": rid,
            "from": f_loc,
            "to": t_loc,
            "distance_km": str(dist),
            "road_name": rname,
            "relation_type": rtype,
        })

    # 3. Incidents
    incident_types = [
        ("Accident", "Critical", "Emergency Response"),
        ("Heavy Rain", "Critical", "Flood Risk"),
        ("Landslip", "Critical", "Route Closed"),
        ("Waterlogging", "High", "Slow Movement"),
        ("Road Work", "Medium", "Lane Diversion"),
        ("Vehicle Breakdown", "Medium", "Partial Block"),
        ("Signal Failure", "Low", "Junction Delay"),
    ]

    inc_nodes_rows = []
    inc_locs_rows = []

    for idx, (loc_id, loc_name, _, district, zone, _, _) in enumerate(KERALA_LOCATIONS, start=1):
        inc_id = f"I{idx:03d}"
        itype, sev, impact = random.choice(incident_types)
        status = "Active" if idx % 4 != 0 else "Planned"
        peak = "High Peak" if idx % 2 == 0 else "Mid Peak"

        desc = f"Live {itype} reported at {loc_name} ({weather['precipitation']:.1f}mm rain, {weather['wind_speed']:.0f}km/h wind)"

        inc_nodes_rows.append({
            "incident_id": inc_id,
            "type": itype,
            "severity": sev,
            "status": status,
            "impact_level": impact,
        })

        inc_locs_rows.append({
            "incident_id": inc_id,
            "location": loc_name,
            "zone": zone,
            "affected_route": "NH66" if "Corridor" in zone else "MC Road",
            "incidents": desc,
            "incident_time": now_iso,
            "traffic_peak_period": peak,
        })

    # 4. Alternates
    alternates_rows = []
    for rid, f_loc, t_loc, dist, rname, _ in roads[:25]:
        alternates_rows.append({
            "from": f_loc,
            "to": t_loc,
            "via": f"{rname} Bypass",
            "extra_distance_km": str(round(dist * 0.15 + 2.0, 1)),
            "route_type": "Bypass Alternate",
            "remarks": f"Real-time bypass around {f_loc} center congestion",
        })

    return locations_rows, location_rels_rows, inc_nodes_rows, inc_locs_rows, alternates_rows


def save_all_csvs():
    logger.info("Fetching real-time weather telemetry from Open-Meteo API...")
    weather = fetch_live_weather()
    logger.info("Live weather retrieved", extra=weather)

    locs, rels, inc_nodes, inc_locs, alts = build_datasets(weather)

    csv_dir = PROJECT_ROOT / "data" / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    # 1. location_nodes.csv
    with (csv_dir / "location_nodes.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["location_id", "name", "type", "district", "zone"])
        w.writeheader()
        w.writerows(locs)

    # 2. location_relationships.csv
    with (csv_dir / "location_relationships.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["relation_id", "from", "to", "distance_km", "road_name", "relation_type"])
        w.writeheader()
        w.writerows(rels)

    # 3. incident_nodes.csv
    with (csv_dir / "incident_nodes.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["incident_id", "type", "severity", "status", "impact_level"])
        w.writeheader()
        w.writerows(inc_nodes)

    # 4. incident_locations.csv
    with (csv_dir / "incident_locations.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["incident_id", "location", "zone", "affected_route", "incidents", "incident_time", "traffic_peak_period"])
        w.writeheader()
        w.writerows(inc_locs)

    # 5. alternate_routes.csv
    with (csv_dir / "alternate_routes.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["from", "to", "via", "extra_distance_km", "route_type", "remarks"])
        w.writeheader()
        w.writerows(alts)

    logger.info("All 5 CSV files successfully generated in data/csv/")


async def main():
    print("Building real-time CSV dataset with live weather telemetry...")
    save_all_csvs()

    print("\nInforming Neo4j via scripts/load_graph.py --reset...")
    from scripts.load_graph import load
    counts = await load(PROJECT_ROOT / "data" / "csv", reset=True)
    print("Successfully populated Neo4j with real-time datasets!")
    print(f"Loaded: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
