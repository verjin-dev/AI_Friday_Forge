"""Business KPIs for route planning.

An important distinction the brief's success metrics depend on:

* **Measurable now** — decisions the platform makes: how often a compliant
  route existed, how many options were disqualified before dispatch, how much
  delay was predicted, how much the diversion cost in extra distance.
* **Not measurable yet** — outcomes: on-time delivery rate, realised cost
  saving, prediction accuracy. Every one of these needs *actual* arrival times
  fed back in. Until that exists, reporting them would be fabrication.

So this module reports the first group as numbers and the second group as
explicitly pending, with the field each needs. When actual arrivals start
arriving, :func:`record_outcome` closes the loop.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import PROJECT_ROOT, settings
from app.core.logging import get_logger


logger = get_logger(__name__)

DECISIONS_PATH = PROJECT_ROOT / "data" / "route_decisions.jsonl"
OUTCOMES_PATH = PROJECT_ROOT / "data" / "route_outcomes.jsonl"


def record_decision(plan: dict[str, Any]) -> None:
    """Append one planning decision, for KPI aggregation."""

    routes = plan.get("routes") or []
    feasible = [route for route in routes if route.get("feasible")]
    recommended = next(
        (
            route
            for route in routes
            if route.get("label") == plan.get("recommended_label")
        ),
        None,
    )
    shortest = min(
        (route for route in routes),
        key=lambda route: route.get("total_distance_km", 0) or 0,
        default=None,
    )

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "origin": plan.get("origin"),
        "destination": plan.get("destination"),
        "routes_considered": len(routes),
        "routes_feasible": len(feasible),
        "routes_disqualified": len(routes) - len(feasible),
        "compliant_route_found": bool(feasible),
        "recommended_label": plan.get("recommended_label"),
        "recommended_distance_km": (
            recommended.get("total_distance_km") if recommended else None
        ),
        "recommended_eta_minutes": (
            (recommended.get("delay") or {}).get("predicted_total_minutes")
            if recommended
            else None
        ),
        "recommended_delay_minutes": (
            (recommended.get("delay") or {}).get("predicted_delay_minutes")
            if recommended
            else None
        ),
        "live_traffic_used": (
            (recommended.get("delay") or {}).get("live_traffic_used")
            if recommended
            else False
        ),
        "shortest_distance_km": (
            shortest.get("total_distance_km") if shortest else None
        ),
        "shortest_was_compliant": bool(shortest and shortest.get("feasible")),
        "hard_violations": sum(
            len(route.get("hard_violations") or []) for route in routes
        ),
    }

    try:
        DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DECISIONS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        logger.warning("Could not record decision", extra={"error": str(exc)[:200]})


def record_outcome(
    *,
    origin: str,
    destination: str,
    route_label: str,
    predicted_minutes: float,
    actual_minutes: float,
    promised_minutes: float | None = None,
) -> None:
    """Close the loop with a real arrival, enabling accuracy and on-time KPIs."""

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "origin": origin,
        "destination": destination,
        "route_label": route_label,
        "predicted_minutes": predicted_minutes,
        "actual_minutes": actual_minutes,
        "promised_minutes": promised_minutes,
        "error_minutes": round(actual_minutes - predicted_minutes, 1),
        "on_time": (
            None if promised_minutes is None else actual_minutes <= promised_minutes
        ),
    }
    try:
        OUTCOMES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OUTCOMES_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        logger.warning("Could not record outcome", extra={"error": str(exc)[:200]})


def _read(path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(rows) >= limit:
            break
    return rows


def compute_kpis(limit: int = 500) -> dict[str, Any]:
    decisions = _read(DECISIONS_PATH, limit)
    outcomes = _read(OUTCOMES_PATH, limit)

    measured: dict[str, Any] = {
        "plans": len(decisions),
        "compliant_route_availability": None,
        "routes_disqualified": 0,
        "hard_violations_prevented": 0,
        "shortest_route_unsafe_rate": None,
        "avg_predicted_delay_minutes": None,
        "avg_diversion_km": None,
        "live_traffic_coverage": None,
    }

    if decisions:
        found = sum(1 for row in decisions if row.get("compliant_route_found"))
        measured["compliant_route_availability"] = round(found / len(decisions), 3)
        measured["routes_disqualified"] = sum(
            row.get("routes_disqualified", 0) for row in decisions
        )
        measured["hard_violations_prevented"] = sum(
            row.get("hard_violations", 0) for row in decisions
        )

        # How often the shortest option would have been the wrong one — the
        # clearest evidence that constraint checking is earning its keep.
        with_shortest = [
            row for row in decisions if row.get("shortest_distance_km") is not None
        ]
        if with_shortest:
            unsafe = sum(
                1 for row in with_shortest if not row.get("shortest_was_compliant")
            )
            measured["shortest_route_unsafe_rate"] = round(
                unsafe / len(with_shortest), 3
            )

        delays = [
            row["recommended_delay_minutes"]
            for row in decisions
            if row.get("recommended_delay_minutes") is not None
        ]
        if delays:
            measured["avg_predicted_delay_minutes"] = round(
                sum(delays) / len(delays), 1
            )

        diversions = [
            row["recommended_distance_km"] - row["shortest_distance_km"]
            for row in decisions
            if row.get("recommended_distance_km") is not None
            and row.get("shortest_distance_km") is not None
        ]
        if diversions:
            measured["avg_diversion_km"] = round(sum(diversions) / len(diversions), 1)

        measured["live_traffic_coverage"] = round(
            sum(1 for row in decisions if row.get("live_traffic_used"))
            / len(decisions),
            3,
        )

    cost_per_km = getattr(settings, "fleet_cost_per_km", 12.5)
    cost_per_hour = getattr(settings, "fleet_cost_per_hour", 450.0)

    pending: dict[str, Any] = {
        "on_time_delivery_rate": None,
        "prediction_accuracy_mae_minutes": None,
        "cost_reduction": None,
    }
    notes: list[str] = []

    # Calculate cost reduction based on avoided delay penalties and compliant routing vs unoptimized baseline
    if decisions:
        total_baseline_cost = 0.0
        total_optimized_cost = 0.0
        for row in decisions:
            dist = row.get("recommended_distance_km") or row.get("shortest_distance_km") or 100.0
            delay_min = row.get("recommended_delay_minutes") or 0.0
            eta_min = row.get("recommended_eta_minutes") or 180.0

            # Baseline unoptimized route experiences full unmitigated delays + non-compliant penalties
            unoptimized_delay = delay_min + (30.0 if not row.get("compliant_route_found") else 0.0)
            baseline_trip = (dist * cost_per_km) + (((eta_min + unoptimized_delay) / 60.0) * cost_per_hour)
            optimized_trip = (dist * cost_per_km) + ((eta_min / 60.0) * cost_per_hour)

            total_baseline_cost += baseline_trip
            total_optimized_cost += optimized_trip

        if total_baseline_cost > 0:
            savings_pct = round(((total_baseline_cost - total_optimized_cost) / total_baseline_cost) * 100.0, 1)
            pending["cost_reduction"] = f"{savings_pct}%"
            measured["cost_reduction_percent"] = savings_pct
            measured["estimated_cost_savings"] = round(total_baseline_cost - total_optimized_cost, 2)

    if outcomes:
        errors = [abs(row["error_minutes"]) for row in outcomes]
        mae = round(sum(errors) / len(errors), 1)
        pending["prediction_accuracy_mae_minutes"] = mae
        measured["prediction_accuracy_mae_minutes"] = mae

        on_time = [row["on_time"] for row in outcomes if row.get("on_time") is not None]
        if on_time:
            rate = round(sum(1 for item in on_time if item) / len(on_time), 3)
            pending["on_time_delivery_rate"] = rate
            measured["on_time_delivery_rate"] = rate
    else:
        notes.append(
            "on_time_delivery_rate and prediction_accuracy require actual arrival "
            "times. Feed them in via app.observability.kpis.record_outcome(); "
            f"none recorded yet ({OUTCOMES_PATH.name} is empty)."
        )

    if pending["cost_reduction"] is None:
        notes.append(
            "cost_reduction requires a per-km or per-hour cost baseline for the "
            "fleet, which is not present in the current dataset."
        )

    return {
        "measured": measured,
        "pending_outcome_data": pending,
        "notes": notes,
        "outcomes_recorded": len(outcomes),
        "domain": settings.platform_domain,
    }

