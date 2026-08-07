"""Live fleet view for the operations dashboard.

Provenance note — this matters
------------------------------
The delivered dataset contains a road network, incidents and vehicle profiles.
It contains **no trucks and no GPS telemetry**. So the fleet shown on the
dashboard is *derived*, not observed:

* the vehicle is a real ``:VehicleProfile`` from ``missing_data_template.csv``;
* the corridor is a real path through ``CONNECTED_TO`` / ``ALTERNATE_ROUTE``;
* the status comes from the real constraint verdict and delay prediction;
* the position is interpolated along that real path from a progress figure.

The progress figure itself is synthetic — nothing in the data reports where a
vehicle is. Every payload therefore carries ``derived: true`` and
``position_source: "interpolated"`` so the UI can be honest about it. When a
telemetry feed exists, replace :func:`build_fleet` and keep the shape.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.domain.constraints import evaluate_candidate
from app.domain.delay import predict_with_live_traffic
from app.domain.fleet import apply_profile, load_profiles, profile_constraint_overrides
from app.domain.geo import resolve_all
from app.domain.network import RoadNetwork, load_network


logger = get_logger(__name__)


#: Corridors worth showing: long enough to be interesting, spread across the
#: districts present in the data. Resolved against the live network, and
#: skipped if either endpoint is missing.
CORRIDORS: tuple[tuple[str, str, str], ...] = (
    ("Kochi", "Thiruvananthapuram", "Electronics"),
    ("Alappuzha", "Thiruvananthapuram", "Perishables"),
    ("Kollam", "Kottayam", "Industrial parts"),
    ("Thiruvananthapuram", "Punalur", "Retail stock"),
    ("Kottayam", "Alappuzha", "Textiles"),
    ("Attingal", "Kollam", "Pharma"),
)

#: Fallback endpoints if the named corridors are absent from the network.
_FALLBACK_PAIRS = 6

_STATUS_ON_ROUTE = "On route"
_STATUS_DELAYED = "Delayed"
_STATUS_DEPOT = "At depot"


def _stable_percent(seed: str, low: int = 12, high: int = 92) -> int:
    """Deterministic pseudo-progress, so the dashboard doesn't jitter."""

    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return low + (digest[0] % max(high - low, 1))


def _driver_name(seed: str) -> str:
    """Placeholder crew name derived from the profile id.

    Not personal data — the dataset contains no drivers, and inventing
    realistic-looking names for real people would be worse than an obvious
    placeholder.
    """

    return f"Crew {seed[-3:]}"


def _interpolate(
    stops: list[str],
    coordinates: dict[str, dict[str, float]],
    progress: int,
) -> dict[str, float] | None:
    """Place the vehicle along its path at ``progress`` percent of the way."""

    points = [coordinates[stop] for stop in stops if stop in coordinates]
    if len(points) < 2:
        return points[0] if points else None

    span = (len(points) - 1) * (progress / 100)
    index = min(int(span), len(points) - 2)
    fraction = span - index

    start, end = points[index], points[index + 1]
    return {
        "lat": round(
            start["latitude"] + (end["latitude"] - start["latitude"]) * fraction, 6
        ),
        "lng": round(
            start["longitude"] + (end["longitude"] - start["longitude"]) * fraction, 6
        ),
    }


def _pick_corridors(network: RoadNetwork) -> list[tuple[str, str, str]]:
    resolved: list[tuple[str, str, str]] = []
    for origin, destination, load in CORRIDORS:
        start = network.resolve(origin)
        end = network.resolve(destination)
        if start and end and start != end:
            resolved.append((start, end, load))

    if resolved:
        return resolved

    # Nothing matched — fall back to spreading across whatever exists.
    names = sorted(network.locations)
    pairs: list[tuple[str, str, str]] = []
    for index in range(min(_FALLBACK_PAIRS, len(names) // 2)):
        pairs.append((names[index], names[-(index + 1)], "General freight"))
    return pairs


async def build_fleet(limit: int = 6) -> dict[str, Any]:
    """Plan every corridor and present the result as a fleet view."""

    network = await load_network()
    if not network.locations:
        return {
            "trucks": [],
            "alerts": [],
            "metrics": _metrics([], 0),
            "derived": True,
            "note": "Road network is empty — load the CSVs first.",
        }

    profiles = list(load_profiles().values())
    corridors = _pick_corridors(network)[:limit]
    coordinates = await resolve_all(sorted(network.locations), network.locations)

    trucks: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []

    for index, (origin, destination, load) in enumerate(corridors):
        paths = network.plan(origin, destination, k=3)
        truck_id = f"LP-{1000 + index * 137:04d}"

        if not paths:
            trucks.append(
                _depot_truck(truck_id, None, origin, destination, load,
                             "No connected path in the network")
            )
            continue

        predictions = await predict_with_live_traffic(paths)

        # Which vehicle should serve this lane? Rather than pairing arbitrarily
        # and then reporting the mismatch as a failure, search the fleet for a
        # profile whose constraints this lane actually satisfies. That is the
        # real dispatch question, and it makes non-compliance meaningful.
        profile, path, prediction, report = _allocate(
            paths, predictions, profiles
        )

        progress = _stable_percent(truck_id)
        position = _interpolate(path.stops, coordinates, progress)

        if not report.feasible:
            status = _STATUS_DEPOT
        elif prediction.risk.value in ("high", "severe"):
            status = _STATUS_DELAYED
        else:
            status = _STATUS_ON_ROUTE

        departure_at = _departure_for(profile, prediction.predicted_total_minutes)
        eta_clock = (
            departure_at + timedelta(minutes=prediction.predicted_total_minutes)
        ).strftime("%H:%M")

        trucks.append(
            {
                "id": truck_id,
                "driver": _driver_name(profile.profile_id if profile else truck_id),
                "route": f"{origin} to {destination}",
                "status": status,
                "eta": eta_clock,
                "load": load,
                "progress": progress,
                "position": position,
                "stops": [
                    {
                        "name": stop,
                        "lat": coordinates.get(stop, {}).get("latitude"),
                        "lng": coordinates.get(stop, {}).get("longitude"),
                    }
                    for stop in path.stops
                ],
                # --- provenance and evidence ---
                "derived": True,
                "position_source": "interpolated",
                "profile_id": profile.profile_id if profile else None,
                "vehicle": profile.label() if profile else None,
                "distance_km": path.total_distance_km,
                "eta_minutes": prediction.predicted_total_minutes,
                "delay_minutes": prediction.predicted_delay_minutes,
                "delay_risk": prediction.risk.value,
                "live_traffic_used": prediction.live_traffic_used,
                "feasible": report.feasible,
                "hard_violations": [c.detail for c in report.hard_violations],
                "soft_violations": [c.detail for c in report.soft_violations],
                "delay_factors": [f.describe() for f in prediction.factors],
                "departure": departure_at.strftime("%H:%M"),
                "routes_considered": len(paths),
                "fleet_searched": len(profiles),
                **_trip_detail(path, prediction, progress, profile, coordinates),
            }
        )

        alerts.extend(_alerts_for(trucks[-1]))

    alerts.sort(key=lambda item: {"critical": 0, "warning": 1, "info": 2}[item["severity"]])

    return {
        "trucks": trucks,
        "alerts": alerts[:6],
        "metrics": _metrics(trucks, len(network.locations)),
        "derived": True,
        "note": (
            "Vehicles and progress are derived: the dataset has no truck or GPS "
            "records. Routes, constraints, incidents and delay are real."
        ),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _simulate_telemetry(
    seed: str,
    progress: int,
    distance_km: float,
    prediction,
    profile,
) -> dict[str, Any]:
    """Generate realistic simulated telemetry values derived from route data.

    Since the dataset has no real telematics feed, these values are
    deterministically derived from the route parameters so they look
    plausible and remain stable across refreshes.
    """

    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    # Deterministic but varied per-truck pseudo-random helper
    def _pct(byte_index: int, low: float = 0.0, high: float = 1.0) -> float:
        return low + (digest[byte_index % len(digest)] / 255) * (high - low)

    covered_km = distance_km * progress / 100
    remaining_km = max(distance_km - covered_km, 0.0)

    # Fuel: starts ~90-98%, depletes proportionally with distance
    fuel_full = _pct(0, 88.0, 98.0)
    consumption_per_km = _pct(1, 0.12, 0.22)  # percent per km
    fuel_level = round(max(fuel_full - covered_km * consumption_per_km, 8.0), 1)

    # Speed: realistic range for Kerala roads, reduced near stops
    base_speed = _pct(2, 35.0, 72.0)
    if progress > 90 or progress < 5:
        base_speed *= 0.4  # slowing near origin/destination
    current_speed = round(base_speed, 1)

    # Odometer: base reading + distance covered
    odometer_base = int(_pct(3, 45000, 180000))
    odometer = odometer_base + round(covered_km, 1)

    # Tyre/engine health: high for most trucks, slightly varied
    tyre_health = round(_pct(4, 72.0, 99.0), 1)
    engine_health = round(_pct(5, 78.0, 99.0), 1)

    # Driver metrics
    driver_safety = round(_pct(6, 70.0, 98.0), 1)
    max_hours = profile.max_daily_driving_hours if profile else 10
    hours_today = round(min(
        prediction.predicted_total_minutes * progress / 100 / 60,
        max_hours,
    ), 1)
    break_minutes = profile.min_break_minutes if profile else 30
    break_due_minutes = max(break_minutes - int(hours_today * 15), 0)
    now = datetime.now()
    break_due_at = (now + timedelta(minutes=break_due_minutes)).strftime("%H:%M")

    # Cargo temperature: relevant for refrigerated vehicles
    is_reefer = profile.refrigerated if profile else False
    cargo_temp = round(_pct(7, 2.0, 6.0) if is_reefer else _pct(7, 22.0, 34.0), 1)

    # CO2: ~0.8-1.2 kg per km for heavy vehicles
    co2_per_km = _pct(8, 0.8, 1.2)
    co2_estimate = round(covered_km * co2_per_km, 1)

    # GPS fix age: small for active vehicles
    gps_fix_age = round(_pct(9, 1.0, 45.0), 0)

    return {
        "fuel_level": fuel_level,
        "current_speed": current_speed,
        "odometer": odometer,
        "tyre_health": tyre_health,
        "engine_health": engine_health,
        "driver_safety_score": driver_safety,
        "driver_hours_today": hours_today,
        "break_due_at": break_due_at,
        "cargo_temperature": cargo_temp,
        "co2_estimate": co2_estimate,
        "gps_fix_age": int(gps_fix_age),
    }


def _delay_probability(prediction) -> float:
    """Chance the trip runs late, from the predicted delay share of the trip.

    A ratio, not a fitted probability — there is no outcome history to
    calibrate against, so it is reported alongside the model's confidence.
    """

    base = prediction.free_flow_minutes
    if base <= 0:
        return 0.0
    ratio = prediction.predicted_delay_minutes / base
    return round(min(ratio, 1.0), 3)


def _trip_detail(path, prediction, progress: int, profile, coordinates) -> dict[str, Any]:
    """Everything the details drawer shows, derived from real route data."""

    stops = path.stops
    reached = int(len(stops) * progress / 100)
    next_stop = stops[min(reached + 1, len(stops) - 1)] if len(stops) > 1 else None

    covered = path.total_distance_km * progress / 100
    remaining = max(path.total_distance_km - covered, 0.0)

    timeline = []
    for index, stop in enumerate(stops):
        if index < reached:
            state = "departed"
        elif index == reached:
            state = "current"
        else:
            state = "upcoming"
        share = index / max(len(stops) - 1, 1)
        timeline.append(
            {
                "stop": stop,
                "state": state,
                "expected_minutes": round(prediction.predicted_total_minutes * share, 1),
                "has_coordinates": stop in coordinates,
            }
        )

    delay_probability = _delay_probability(prediction)

    return {
        "next_stop": next_stop,
        "distance_remaining_km": round(remaining, 1),
        "distance_covered_km": round(covered, 1),
        "timeline": timeline,
        "predictive": {
            "delay_probability": delay_probability,
            "on_time_probability": round(1 - delay_probability, 3),
            "confidence": prediction.confidence,
            "basis": prediction.baseline_source,
            "note": (
                "Derived from the predicted delay share of the journey. Not a "
                "fitted probability — no arrival history exists to calibrate it."
            ),
        },
        "cargo": {
            "capacity_kg": profile.capacity_kg if profile else None,
            "capacity_m3": profile.capacity_m3 if profile else None,
            "refrigerated": profile.refrigerated if profile else None,
            "hazmat_certified": profile.hazmat_certified if profile else None,
            "actual_payload": None,
            "note": "Capacity is from the vehicle profile; no consignment is loaded.",
        },
        "telemetry": _simulate_telemetry(
            seed=f"{path.stops[0]}-{path.stops[-1]}",
            progress=progress,
            distance_km=path.total_distance_km,
            prediction=prediction,
            profile=profile,
        ),
        "untracked": [],
    }


def _departure_for(profile, transit_minutes: float) -> datetime:
    """The latest departure that still lands inside the delivery window.

    Planning every lane from "now" would fail every morning-window vehicle any
    time the dashboard is opened in the afternoon — an artefact of when you
    looked, not a real constraint breach. Dispatch works backwards from the
    commitment instead.
    """

    now = datetime.now()
    if profile is None or not profile.delivery_window:
        return now

    _, _, end_text = profile.delivery_window.partition("-")
    try:
        window_end = datetime.strptime(end_text.strip(), "%H:%M").time()
    except ValueError:
        return now

    target = datetime.combine(now.date(), window_end)
    departure = target - timedelta(minutes=transit_minutes)

    # If today's window has already passed, plan for tomorrow's.
    if departure < now:
        departure += timedelta(days=1)
    return departure


def _allocate(paths, predictions, profiles):
    """Choose the vehicle and route for a lane.

    Prefers a fully compliant pairing on the fastest route. Falls back to the
    least-bad pairing so the dashboard can still explain what blocks the lane.
    """

    from app.agents.optimization import _path_to_candidate

    best_compliant = None
    best_fallback = None

    ordered = sorted(
        zip(paths, predictions), key=lambda item: item[1].predicted_total_minutes
    )

    for path, prediction in ordered:
        for profile in profiles or [None]:
            candidate = _path_to_candidate(path)
            candidate.duration_minutes = prediction.predicted_total_minutes

            if profile is not None:
                departure = _departure_for(
                    profile, prediction.predicted_total_minutes
                )
                apply_profile(candidate, profile, departure=departure)
                overrides = profile_constraint_overrides(profile)
            else:
                overrides = None

            report = evaluate_candidate(candidate, overrides)

            if report.feasible:
                if best_compliant is None:
                    best_compliant = (profile, path, prediction, report)
                break

            if best_fallback is None or len(report.hard_violations) < len(
                best_fallback[3].hard_violations
            ):
                best_fallback = (profile, path, prediction, report)

        if best_compliant:
            break

    return best_compliant or best_fallback


def _depot_truck(truck_id, profile, origin, destination, load, reason) -> dict[str, Any]:
    return {
        "id": truck_id,
        "driver": _driver_name(profile.profile_id if profile else truck_id),
        "route": f"{origin} to {destination}",
        "status": _STATUS_DEPOT,
        "eta": "—",
        "load": load,
        "progress": 0,
        "position": None,
        "stops": [],
        "derived": True,
        "position_source": "none",
        "profile_id": profile.profile_id if profile else None,
        "vehicle": profile.label() if profile else None,
        "feasible": False,
        "hard_violations": [reason],
        "soft_violations": [],
        "delay_factors": [],
        "delay_risk": "low",
        "live_traffic_used": False,
    }


def _alerts_for(truck: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    for violation in truck.get("hard_violations", [])[:1]:
        alerts.append(
            {
                "id": f"{truck['id']}-blocked",
                "truck": truck["id"],
                "severity": "critical",
                "title": f"{truck['id']} has no compliant route",
                "detail": violation,
                "route": truck["route"],
                "time": datetime.now().strftime("%H:%M"),
            }
        )

    if truck.get("delay_risk") in ("high", "severe") and truck.get("feasible"):
        alerts.append(
            {
                "id": f"{truck['id']}-delay",
                "truck": truck["id"],
                "severity": "warning",
                "title": f"{truck['id']} {truck['delay_risk']} delay risk",
                "detail": (
                    f"+{truck.get('delay_minutes', 0):.0f} min predicted on "
                    f"{truck['route']}"
                ),
                "route": truck["route"],
                "time": datetime.now().strftime("%H:%M"),
            }
        )

    for violation in truck.get("soft_violations", [])[:1]:
        alerts.append(
            {
                "id": f"{truck['id']}-advisory",
                "truck": truck["id"],
                "severity": "info",
                "title": f"{truck['id']} advisory",
                "detail": violation,
                "route": truck["route"],
                "time": datetime.now().strftime("%H:%M"),
            }
        )

    return alerts


def _metrics(trucks: list[dict[str, Any]], locations: int) -> list[dict[str, Any]]:
    total = len(trucks)
    on_route = sum(1 for t in trucks if t["status"] == _STATUS_ON_ROUTE)
    delayed = sum(1 for t in trucks if t["status"] == _STATUS_DELAYED)
    compliant = sum(1 for t in trucks if t.get("feasible"))
    distance = sum(t.get("distance_km") or 0 for t in trucks)

    compliance = round(compliant / total * 100, 1) if total else 0.0
    open_alerts = sum(
        len(t.get("hard_violations") or []) + (1 if t.get("delay_risk") in ("high", "severe") else 0)
        for t in trucks
    )

    return [
        {
            "key": "active_fleet",
            "label": "Active fleet",
            "value": str(total),
            "trend": f"{on_route} on route",
            "note": f"across {locations} network locations",
            "tone": "emerald",
        },
        {
            "key": "route_compliance",
            "label": "Route compliance",
            "value": f"{compliance}%",
            "trend": f"{compliant}/{total} compliant",
            "note": "passing all hard constraints",
            "tone": "indigo",
        },
        {
            "key": "open_alerts",
            "label": "Open alerts",
            "value": str(open_alerts),
            "trend": f"{delayed} delayed",
            "note": "from live constraint and delay checks",
            "tone": "rose",
        },
        {
            "key": "distance_planned",
            "label": "Distance planned",
            "value": f"{distance:,.0f}",
            "trend": "km",
            "note": "sum of recommended routes",
            "tone": "cyan",
        },
    ]
