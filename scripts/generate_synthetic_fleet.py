"""Generate brand-new synthetic Driver and Vehicle Profile data and load cleanly into Neo4j.

Removes old vehicle profiles and populates data/csv/missing_data_template.csv with
40 new, realistic driver & vehicle profile datasets across LMV, HMV, and TRANS license classes.
"""

from __future__ import annotations

import asyncio
import csv
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.logging import get_logger

logger = get_logger("generate_synthetic_fleet")

SYNTHETIC_DRIVERS_AND_VEHICLES = [
    # (CAP_WEIGHT, CAP_VOLUME, HOS_DAILY, HOS_BREAK, DRV_LICENCE, TW_WINDOW, SLA_PROMISE, WH_CUTOFF, COLD_CHAIN, HAZMAT_CERT, RTE_HEIGHT, RTE_AXLE, RTE_ZONE, PREF_COST, PREF_DURATION)
    (1200, 14, 9, 45, "LMV", "08:00-12:00", "Same Day", "17:00", "No", "No", 3.5, 2, "Urban", "Low", "Short"),
    (2500, 22, 10, 30, "HMV", "09:00-13:00", "Next Day", "18:00", "Yes", "No", 4.2, 3, "Industrial", "Medium", "Medium"),
    (1800, 18, 8, 45, "LMV", "10:00-14:00", "48 Hours", "16:30", "No", "No", 3.8, 2, "Rural", "Low", "Medium"),
    (3200, 28, 11, 60, "HMV", "07:00-11:00", "Same Day", "19:00", "Yes", "Yes", 4.5, 4, "Hazard", "High", "Short"),
    (900, 10, 8, 30, "MCWG", "12:00-16:00", "Next Day", "15:00", "No", "No", 2.8, 1, "Urban", "Low", "Short"),
    (4100, 35, 9, 45, "TRANS", "06:00-10:00", "72 Hours", "20:00", "Yes", "Yes", 4.8, 5, "Industrial", "High", "Long"),
    (1500, 16, 10, 30, "LMV", "13:00-17:00", "48 Hours", "17:30", "No", "No", 3.6, 2, "Urban", "Medium", "Medium"),
    (2750, 24, 9, 45, "HMV", "08:30-12:30", "Same Day", "18:30", "Yes", "No", 4.1, 3, "Rural", "Medium", "Short"),
    (3600, 30, 11, 60, "TRANS", "09:00-15:00", "72 Hours", "19:30", "No", "Yes", 4.6, 4, "Hazard", "High", "Long"),
    (1100, 12, 8, 30, "LMV", "07:30-11:30", "Next Day", "16:00", "No", "No", 3.2, 2, "Urban", "Low", "Short"),
    (2200, 20, 9, 45, "HMV", "11:00-15:00", "48 Hours", "17:00", "Yes", "No", 4.0, 3, "Industrial", "Medium", "Medium"),
    (800, 8, 7, 30, "MCWG", "14:00-18:00", "Same Day", "14:30", "No", "No", 2.5, 1, "Urban", "Low", "Short"),
    (4700, 38, 10, 60, "TRANS", "05:00-09:00", "72 Hours", "21:00", "Yes", "Yes", 5.0, 5, "Hazard", "High", "Long"),
    (1950, 19, 9, 45, "LMV", "10:30-14:30", "Next Day", "18:00", "No", "No", 3.9, 2, "Rural", "Medium", "Medium"),
    (3050, 27, 11, 60, "HMV", "06:30-10:30", "Same Day", "19:00", "Yes", "No", 4.4, 4, "Industrial", "High", "Short"),
    (1300, 15, 8, 30, "LMV", "12:30-16:30", "48 Hours", "16:30", "No", "No", 3.4, 2, "Urban", "Low", "Medium"),
    (2850, 26, 10, 45, "HMV", "09:30-13:30", "Next Day", "18:30", "Yes", "No", 4.2, 3, "Rural", "Medium", "Medium"),
    (3900, 32, 11, 60, "TRANS", "07:00-13:00", "72 Hours", "20:00", "Yes", "Yes", 4.7, 4, "Hazard", "High", "Long"),
    (1000, 11, 8, 30, "LMV", "08:00-10:00", "Same Day", "15:30", "No", "No", 3.1, 1, "Urban", "Low", "Short"),
    (2400, 21, 9, 45, "HMV", "13:00-17:00", "48 Hours", "17:30", "Yes", "No", 4.0, 3, "Industrial", "Medium", "Medium"),
    (1750, 17, 8, 30, "LMV", "11:30-15:30", "Next Day", "16:00", "No", "No", 3.7, 2, "Rural", "Low", "Medium"),
    (4300, 36, 10, 60, "TRANS", "06:00-12:00", "72 Hours", "20:30", "Yes", "Yes", 4.9, 5, "Hazard", "High", "Long"),
    (1450, 13, 9, 45, "LMV", "09:00-12:00", "Same Day", "17:00", "No", "No", 3.3, 2, "Urban", "Low", "Short"),
    (2600, 23, 10, 30, "HMV", "10:00-14:00", "Next Day", "18:00", "Yes", "No", 4.1, 3, "Industrial", "Medium", "Medium"),
    (3400, 29, 11, 60, "TRANS", "08:00-14:00", "48 Hours", "19:00", "No", "Yes", 4.5, 4, "Hazard", "High", "Long"),
    (1650, 16, 8, 45, "LMV", "12:00-16:00", "Next Day", "16:30", "No", "No", 3.6, 2, "Rural", "Low", "Medium"),
    (2900, 25, 10, 45, "HMV", "07:30-11:30", "Same Day", "18:30", "Yes", "No", 4.3, 3, "Industrial", "Medium", "Short"),
    (4000, 34, 11, 60, "TRANS", "05:30-09:30", "72 Hours", "20:00", "Yes", "Yes", 4.8, 5, "Hazard", "High", "Long"),
    (1150, 12, 8, 30, "LMV", "09:30-13:30", "48 Hours", "17:00", "No", "No", 3.2, 2, "Urban", "Low", "Medium"),
    (2300, 20, 9, 45, "HMV", "14:00-18:00", "Next Day", "17:30", "Yes", "No", 4.0, 3, "Rural", "Medium", "Medium"),
    (5000, 42, 11, 60, "TRANS", "05:00-11:00", "Same Day", "21:00", "Yes", "Yes", 5.2, 6, "Hazard", "High", "Long"),
    (1400, 15, 8, 30, "LMV", "08:30-12:30", "48 Hours", "16:30", "No", "No", 3.4, 2, "Urban", "Low", "Medium"),
    (3100, 28, 10, 45, "HMV", "09:00-15:00", "Next Day", "19:00", "Yes", "No", 4.4, 4, "Industrial", "High", "Medium"),
    (4500, 38, 11, 60, "TRANS", "06:00-12:00", "72 Hours", "20:30", "Yes", "Yes", 5.0, 5, "Hazard", "High", "Long"),
    (1250, 13, 8, 30, "LMV", "10:00-14:00", "Same Day", "16:00", "No", "No", 3.3, 2, "Urban", "Low", "Short"),
    (2700, 24, 9, 45, "HMV", "11:00-15:00", "48 Hours", "18:00", "Yes", "No", 4.2, 3, "Rural", "Medium", "Medium"),
    (3800, 31, 10, 60, "TRANS", "07:30-13:30", "72 Hours", "19:30", "No", "Yes", 4.6, 4, "Hazard", "High", "Long"),
    (1600, 16, 9, 45, "LMV", "13:30-17:30", "Next Day", "17:30", "No", "No", 3.5, 2, "Urban", "Medium", "Medium"),
    (3300, 30, 11, 60, "HMV", "08:00-12:00", "Same Day", "19:00", "Yes", "No", 4.5, 4, "Industrial", "High", "Short"),
    (4800, 40, 11, 60, "TRANS", "05:00-10:00", "72 Hours", "21:00", "Yes", "Yes", 5.1, 5, "Hazard", "High", "Long"),
]


def generate_fleet_csv():
    csv_file = PROJECT_ROOT / "data" / "csv" / "missing_data_template.csv"

    headers = [
        "CAP_WEIGHT",
        "CAP_VOLUME",
        "HOS_DAILY",
        "HOS_BREAK",
        "DRV_LICENCE",
        "TW_WINDOW",
        "SLA_PROMISE",
        "WH_CUTOFF",
        "COLD_CHAIN",
        "HAZMAT_CERT",
        "RTE_HEIGHT",
        "RTE_AXLE",
        "RTE_ZONE",
        "PREF_COST",
        "PREF_DURATION",
    ]

    with csv_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in SYNTHETIC_DRIVERS_AND_VEHICLES:
            writer.writerow(list(row))

    logger.info("Generated 40 brand-new synthetic driver and vehicle profiles in missing_data_template.csv")


async def main():
    print("Generating new synthetic driver and vehicle dataset...")
    generate_fleet_csv()

    print("Reloading vehicle profiles into Neo4j via scripts/load_graph.py --reset...")
    from scripts.load_graph import load
    counts = await load(PROJECT_ROOT / "data" / "csv", reset=True)
    print(f"Successfully reloaded Neo4j! Counts: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
