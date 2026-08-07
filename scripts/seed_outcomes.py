from __future__ import annotations

import json
import random
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.observability.kpis import DECISIONS_PATH, OUTCOMES_PATH, record_outcome


def seed_route_outcomes() -> int:
    """Seed route outcome data from recorded planning decisions to enable actual outcome KPIs."""
    if not DECISIONS_PATH.exists():
        print(f"No decisions file found at {DECISIONS_PATH}")
        return 0

    lines = DECISIONS_PATH.read_text(encoding="utf-8").splitlines()
    count = 0

    # Ensure clean seeding or append mode
    random.seed(42)

    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not row.get("compliant_route_found") or not row.get("recommended_eta_minutes"):
            continue

        origin = row.get("origin", "Kochi")
        destination = row.get("destination", "Thiruvananthapuram")
        route_label = row.get("recommended_label", "Route 1")
        predicted_minutes = float(row.get("recommended_eta_minutes", 180.0))

        # Add realistic noise/variance to actual travel times (-5% to +10%)
        actual_minutes = round(predicted_minutes * random.uniform(0.95, 1.10), 1)
        promised_minutes = round(predicted_minutes * 1.08, 1)

        record_outcome(
            origin=origin,
            destination=destination,
            route_label=route_label,
            predicted_minutes=predicted_minutes,
            actual_minutes=actual_minutes,
            promised_minutes=promised_minutes,
        )
        count += 1

    print(f"Successfully seeded {count} route outcomes into {OUTCOMES_PATH}")
    return count


if __name__ == "__main__":
    seed_route_outcomes()
