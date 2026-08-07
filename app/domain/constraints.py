from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Any, Callable

from pydantic import BaseModel, Field

from app.core.config import PROJECT_ROOT
from app.core.logging import get_logger


logger = get_logger(__name__)


class ConstraintSeverity(str, Enum):
    #: A hard constraint is a feasibility gate. Violating it disqualifies the
    #: option outright — it is never traded off against cost or speed.
    HARD = "hard"
    #: A soft constraint is a preference. Violations are allowed but penalised
    #: and must be disclosed in the explanation.
    SOFT = "soft"


class ConstraintCheck(BaseModel):
    code: str
    name: str
    severity: ConstraintSeverity
    satisfied: bool
    detail: str
    observed: float | str | None = None
    limit: float | str | None = None
    penalty: float = 0.0


class ConstraintReport(BaseModel):
    """Verdict for one candidate solution."""

    candidate: str
    feasible: bool = True
    checks: list[ConstraintCheck] = Field(default_factory=list)
    penalty: float = 0.0
    unverifiable: list[str] = Field(default_factory=list)

    @property
    def hard_violations(self) -> list[ConstraintCheck]:
        return [
            check
            for check in self.checks
            if not check.satisfied and check.severity is ConstraintSeverity.HARD
        ]

    @property
    def soft_violations(self) -> list[ConstraintCheck]:
        return [
            check
            for check in self.checks
            if not check.satisfied and check.severity is ConstraintSeverity.SOFT
        ]

    def summary(self) -> str:
        if self.feasible and not self.soft_violations:
            return f"'{self.candidate}': all constraints satisfied."
        if not self.feasible:
            reasons = "; ".join(check.detail for check in self.hard_violations)
            return f"'{self.candidate}': INFEASIBLE — {reasons}"
        reasons = "; ".join(check.detail for check in self.soft_violations)
        return f"'{self.candidate}': feasible with soft violations — {reasons}"


class RouteCandidate(BaseModel):
    """A proposed logistics solution, in the shape the constraint engine checks.

    Every field is optional because real questions arrive with partial data;
    a constraint whose inputs are missing is reported as *unverifiable* rather
    than silently passed.
    """

    label: str
    description: str = ""

    # --- payload ---
    payload_weight_kg: float | None = None
    payload_volume_m3: float | None = None
    pallet_count: int | None = None

    # --- vehicle ---
    vehicle_id: str | None = None
    vehicle_type: str | None = None
    vehicle_capacity_kg: float | None = None
    vehicle_capacity_m3: float | None = None
    vehicle_height_m: float | None = None
    vehicle_axle_load_kg: float | None = None
    refrigerated: bool | None = None
    hazmat_certified: bool | None = None

    # --- route ---
    distance_km: float | None = None
    duration_minutes: float | None = None
    stop_count: int | None = None
    max_route_height_m: float | None = None
    max_route_axle_load_kg: float | None = None
    restricted_zones: list[str] = Field(default_factory=list)

    # --- road-network verification (populated by app.domain.network only) ---
    #: Ordered location names the route passes through.
    stops: list[str] = Field(default_factory=list)
    #: True/False once the network has confirmed every leg; None if unchecked.
    legs_verified: bool | None = None
    missing_legs: list[str] = Field(default_factory=list)
    blocking_incidents: list[str] = Field(default_factory=list)
    #: Blocking incidents at the origin/destination — unavoidable, so disclosed
    #: rather than disqualifying.
    endpoint_incidents: list[str] = Field(default_factory=list)
    advisory_incidents: list[str] = Field(default_factory=list)
    #: Distance computed from CONNECTED_TO edges, for cross-checking.
    network_distance_km: float | None = None

    # --- driver ---
    driver_id: str | None = None
    driving_hours: float | None = None
    hours_already_driven_today: float | None = None
    rest_break_minutes: float | None = None
    driver_licence_classes: list[str] = Field(default_factory=list)
    required_licence_class: str | None = None

    # --- time ---
    departure_time: str | None = None
    estimated_arrival: str | None = None
    promised_delivery_by: str | None = None
    delivery_window_start: str | None = None
    delivery_window_end: str | None = None
    warehouse_cutoff: str | None = None

    # --- cargo handling ---
    hazmat_class: str | None = None
    required_temperature_c: float | None = None
    planned_temperature_c: float | None = None

    # --- commercial ---
    cost: float | None = None
    carrier: str | None = None
    sla_tier: str | None = None


class ConstraintProfile(BaseModel):
    """Tunable limits. Override via ``logistics_constraints.json`` at the root."""

    # Hours of service
    max_daily_driving_hours: float = 9.0
    max_continuous_driving_hours: float = 4.5
    min_break_minutes_after_continuous: float = 45.0

    # Fleet
    max_vehicle_utilisation: float = 1.0
    max_stops_per_route: int = 25
    max_route_distance_km: float = 800.0

    # Service
    sla_buffer_minutes: float = 30.0
    allow_late_delivery: bool = False

    # Cold chain
    cold_chain_tolerance_c: float = 2.0

    # Hazmat classes that may not share a route with food-grade cargo etc.
    hazmat_requires_certified_vehicle: bool = True

    # Soft preferences
    preferred_max_cost: float | None = None
    preferred_max_duration_minutes: float | None = None

    # Road network
    #: Tolerance when cross-checking a stated distance against the graph.
    distance_match_tolerance_km: float = 1.0
    #: Treat Active advisory (Medium/Low) incidents as a soft penalty.
    advisory_incident_penalty: float = 0.25
    #: Severe incident at the origin/destination — unavoidable, weighted higher.
    endpoint_incident_penalty: float = 0.35


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    for parser in (
        lambda v: datetime.fromisoformat(v),
        lambda v: datetime.strptime(v, "%Y-%m-%d %H:%M"),
        lambda v: datetime.strptime(v, "%H:%M"),
    ):
        try:
            return parser(text)
        except (ValueError, TypeError):
            continue
    return None


@lru_cache(maxsize=1)
def get_constraint_profile() -> ConstraintProfile:
    path = PROJECT_ROOT / "logistics_constraints.json"
    if not path.exists():
        return ConstraintProfile()
    try:
        return ConstraintProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Invalid logistics_constraints.json; using defaults",
            extra={"error": str(exc)[:200]},
        )
        return ConstraintProfile()


# ----------------------------------------------------------------------
# Individual constraint rules
# ----------------------------------------------------------------------
Rule = Callable[[RouteCandidate, ConstraintProfile], ConstraintCheck | None]


def _check(
    code: str,
    name: str,
    severity: ConstraintSeverity,
    satisfied: bool,
    detail: str,
    observed: Any = None,
    limit: Any = None,
    penalty: float = 0.0,
) -> ConstraintCheck:
    return ConstraintCheck(
        code=code,
        name=name,
        severity=severity,
        satisfied=satisfied,
        detail=detail,
        observed=observed,
        limit=limit,
        penalty=0.0 if satisfied else penalty,
    )


def _weight_capacity(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.payload_weight_kg is None or c.vehicle_capacity_kg is None:
        return None
    limit = c.vehicle_capacity_kg * p.max_vehicle_utilisation
    ok = c.payload_weight_kg <= limit
    return _check(
        "CAP_WEIGHT",
        "Vehicle weight capacity",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Payload {c.payload_weight_kg:.0f} kg "
            f"{'within' if ok else 'EXCEEDS'} vehicle limit {limit:.0f} kg."
        ),
        c.payload_weight_kg,
        limit,
    )


def _volume_capacity(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.payload_volume_m3 is None or c.vehicle_capacity_m3 is None:
        return None
    limit = c.vehicle_capacity_m3 * p.max_vehicle_utilisation
    ok = c.payload_volume_m3 <= limit
    return _check(
        "CAP_VOLUME",
        "Vehicle volume capacity",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Load {c.payload_volume_m3:.1f} m³ "
            f"{'within' if ok else 'EXCEEDS'} vehicle limit {limit:.1f} m³."
        ),
        c.payload_volume_m3,
        limit,
    )


def _daily_driving_hours(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.driving_hours is None:
        return None
    total = c.driving_hours + (c.hours_already_driven_today or 0.0)
    ok = total <= p.max_daily_driving_hours
    return _check(
        "HOS_DAILY",
        "Driver hours of service (daily)",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Total driving {total:.1f} h "
            f"{'within' if ok else 'EXCEEDS'} the {p.max_daily_driving_hours:.1f} h "
            "daily limit."
        ),
        total,
        p.max_daily_driving_hours,
    )


def _continuous_driving(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.driving_hours is None:
        return None
    if c.driving_hours <= p.max_continuous_driving_hours:
        return _check(
            "HOS_BREAK",
            "Mandatory rest break",
            ConstraintSeverity.HARD,
            True,
            f"Leg of {c.driving_hours:.1f} h needs no mandatory break.",
            c.driving_hours,
            p.max_continuous_driving_hours,
        )
    taken = c.rest_break_minutes or 0.0
    ok = taken >= p.min_break_minutes_after_continuous
    return _check(
        "HOS_BREAK",
        "Mandatory rest break",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Driving {c.driving_hours:.1f} h exceeds "
            f"{p.max_continuous_driving_hours:.1f} h continuous; "
            f"{taken:.0f} min break planned versus "
            f"{p.min_break_minutes_after_continuous:.0f} min required."
        ),
        taken,
        p.min_break_minutes_after_continuous,
    )


def _licence_class(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if not c.required_licence_class:
        return None
    if not c.driver_licence_classes:
        return None
    ok = c.required_licence_class in c.driver_licence_classes
    return _check(
        "DRV_LICENCE",
        "Driver licence class",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Driver holds {', '.join(c.driver_licence_classes)}; "
            f"route requires {c.required_licence_class}."
        ),
        ", ".join(c.driver_licence_classes),
        c.required_licence_class,
    )


def _delivery_window(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    arrival = _parse_time(c.estimated_arrival)
    start = _parse_time(c.delivery_window_start)
    end = _parse_time(c.delivery_window_end)
    if arrival is None or (start is None and end is None):
        return None
    early = start is not None and arrival < start
    late = end is not None and arrival > end
    ok = not (early or late)
    if early:
        detail = f"Arrival {c.estimated_arrival} is BEFORE the window opens at {c.delivery_window_start}."
    elif late:
        detail = f"Arrival {c.estimated_arrival} is AFTER the window closes at {c.delivery_window_end}."
    else:
        detail = f"Arrival {c.estimated_arrival} falls inside the agreed delivery window."
    return _check(
        "TW_WINDOW",
        "Customer delivery time window",
        ConstraintSeverity.HARD,
        ok,
        detail,
        c.estimated_arrival,
        f"{c.delivery_window_start or '-'} to {c.delivery_window_end or '-'}",
    )


def _sla_promise(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    arrival = _parse_time(c.estimated_arrival)
    promised = _parse_time(c.promised_delivery_by)
    if arrival is None or promised is None:
        return None
    slack_minutes = (promised - arrival).total_seconds() / 60
    ok = slack_minutes >= 0 if p.allow_late_delivery else slack_minutes >= p.sla_buffer_minutes
    return _check(
        "SLA_PROMISE",
        "Promised delivery / SLA",
        ConstraintSeverity.HARD,
        ok,
        (
            f"ETA {c.estimated_arrival} versus promise {c.promised_delivery_by}: "
            f"{slack_minutes:.0f} min slack "
            f"(buffer required {p.sla_buffer_minutes:.0f} min)."
        ),
        round(slack_minutes, 1),
        p.sla_buffer_minutes,
    )


def _warehouse_cutoff(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    departure = _parse_time(c.departure_time)
    cutoff = _parse_time(c.warehouse_cutoff)
    if departure is None or cutoff is None:
        return None
    ok = departure <= cutoff
    return _check(
        "WH_CUTOFF",
        "Warehouse dispatch cut-off",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Departure {c.departure_time} "
            f"{'meets' if ok else 'MISSES'} the {c.warehouse_cutoff} cut-off."
        ),
        c.departure_time,
        c.warehouse_cutoff,
    )


def _cold_chain(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.required_temperature_c is None:
        return None
    if c.refrigerated is False:
        return _check(
            "COLD_CHAIN",
            "Cold chain integrity",
            ConstraintSeverity.HARD,
            False,
            (
                f"Cargo requires {c.required_temperature_c:.1f} °C but the assigned "
                "vehicle is not refrigerated."
            ),
            "non-refrigerated vehicle",
            f"{c.required_temperature_c:.1f} °C",
        )
    if c.planned_temperature_c is None:
        return None
    drift = abs(c.planned_temperature_c - c.required_temperature_c)
    ok = drift <= p.cold_chain_tolerance_c
    return _check(
        "COLD_CHAIN",
        "Cold chain integrity",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Planned {c.planned_temperature_c:.1f} °C versus required "
            f"{c.required_temperature_c:.1f} °C (drift {drift:.1f} °C, "
            f"tolerance {p.cold_chain_tolerance_c:.1f} °C)."
        ),
        drift,
        p.cold_chain_tolerance_c,
    )


def _hazmat(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if not c.hazmat_class or not p.hazmat_requires_certified_vehicle:
        return None
    if c.hazmat_certified is None:
        return None
    ok = bool(c.hazmat_certified)
    return _check(
        "HAZMAT_CERT",
        "Hazmat vehicle certification",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Cargo is hazmat class {c.hazmat_class}; assigned vehicle is "
            f"{'certified' if ok else 'NOT certified'} to carry it."
        ),
        c.hazmat_certified,
        True,
    )


def _height_restriction(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.vehicle_height_m is None or c.max_route_height_m is None:
        return None
    ok = c.vehicle_height_m <= c.max_route_height_m
    return _check(
        "RTE_HEIGHT",
        "Route height clearance",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Vehicle height {c.vehicle_height_m:.2f} m versus route clearance "
            f"{c.max_route_height_m:.2f} m."
        ),
        c.vehicle_height_m,
        c.max_route_height_m,
    )


def _axle_load(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.vehicle_axle_load_kg is None or c.max_route_axle_load_kg is None:
        return None
    ok = c.vehicle_axle_load_kg <= c.max_route_axle_load_kg
    return _check(
        "RTE_AXLE",
        "Route axle load limit",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Axle load {c.vehicle_axle_load_kg:.0f} kg versus route limit "
            f"{c.max_route_axle_load_kg:.0f} kg."
        ),
        c.vehicle_axle_load_kg,
        c.max_route_axle_load_kg,
    )


def _restricted_zones(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if not c.restricted_zones:
        return None
    return _check(
        "RTE_ZONE",
        "Restricted zone access",
        ConstraintSeverity.HARD,
        False,
        f"Route passes through restricted zone(s): {', '.join(c.restricted_zones)}.",
        ", ".join(c.restricted_zones),
        "none permitted",
    )


def _stop_count(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.stop_count is None:
        return None
    ok = c.stop_count <= p.max_stops_per_route
    return _check(
        "RTE_STOPS",
        "Maximum stops per route",
        ConstraintSeverity.HARD,
        ok,
        f"{c.stop_count} stops versus limit of {p.max_stops_per_route}.",
        c.stop_count,
        p.max_stops_per_route,
    )


def _route_distance(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.distance_km is None:
        return None
    ok = c.distance_km <= p.max_route_distance_km
    return _check(
        "RTE_DISTANCE",
        "Maximum route distance",
        ConstraintSeverity.HARD,
        ok,
        f"{c.distance_km:.0f} km versus limit of {p.max_route_distance_km:.0f} km.",
        c.distance_km,
        p.max_route_distance_km,
    )


def _network_legs(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    """Every leg must be a real CONNECTED_TO edge in the knowledge graph."""

    if c.legs_verified is None:
        return None
    ok = bool(c.legs_verified) and not c.missing_legs
    return _check(
        "NET_LEGS",
        "Road segments exist in the network",
        ConstraintSeverity.HARD,
        ok,
        (
            f"All {max(len(c.stops) - 1, 0)} leg(s) verified against CONNECTED_TO edges."
            if ok
            else "Route uses road segments that do not exist in the graph: "
            + ", ".join(c.missing_legs)
        ),
        " → ".join(c.stops) if c.stops else None,
        "graph-verified legs only",
    )


def _network_incidents(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    """No route may pass through a location with an Active blocking incident."""

    if c.legs_verified is None and not c.stops:
        return None
    ok = not c.blocking_incidents
    return _check(
        "NET_INCIDENT",
        "No active blocking incident on route",
        ConstraintSeverity.HARD,
        ok,
        (
            "No active Critical/High incident on any location in this route."
            if ok
            else "Route is blocked by active incident(s): "
            + "; ".join(c.blocking_incidents)
        ),
        "; ".join(c.blocking_incidents) if c.blocking_incidents else "none",
        "no active Critical/High incidents",
    )


def _network_advisories(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.legs_verified is None and not c.stops:
        return None
    ok = not c.advisory_incidents
    return _check(
        "NET_ADVISORY",
        "Advisory incidents on route",
        ConstraintSeverity.SOFT,
        ok,
        (
            "No advisory incidents reported on this route."
            if ok
            else "Active advisory incident(s) raise delay risk: "
            + "; ".join(c.advisory_incidents)
        ),
        "; ".join(c.advisory_incidents) if c.advisory_incidents else "none",
        "none preferred",
        penalty=p.advisory_incident_penalty,
    )


def _network_endpoints(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    """Severe conditions at the origin or destination — unavoidable, so soft."""

    if c.legs_verified is None and not c.stops:
        return None
    ok = not c.endpoint_incidents
    return _check(
        "NET_ENDPOINT",
        "Conditions at origin/destination",
        ConstraintSeverity.SOFT,
        ok,
        (
            "No severe incident at the origin or destination."
            if ok
            else "Severe conditions at the route endpoint(s) — cannot be routed "
            "around, plan for delay: " + "; ".join(c.endpoint_incidents)
        ),
        "; ".join(c.endpoint_incidents) if c.endpoint_incidents else "none",
        "none preferred",
        penalty=p.endpoint_incident_penalty,
    )


def _network_distance(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    """A stated distance must match what the graph's edges actually sum to."""

    if c.distance_km is None or c.network_distance_km is None:
        return None
    drift = abs(c.distance_km - c.network_distance_km)
    ok = drift <= p.distance_match_tolerance_km
    return _check(
        "NET_DISTANCE",
        "Distance matches graph data",
        ConstraintSeverity.HARD,
        ok,
        (
            f"Stated {c.distance_km:.1f} km versus graph total "
            f"{c.network_distance_km:.1f} km (drift {drift:.1f} km, tolerance "
            f"{p.distance_match_tolerance_km:.1f} km)."
        ),
        c.distance_km,
        c.network_distance_km,
    )


def _cost_preference(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.cost is None or p.preferred_max_cost is None:
        return None
    ok = c.cost <= p.preferred_max_cost
    return _check(
        "PREF_COST",
        "Preferred cost ceiling",
        ConstraintSeverity.SOFT,
        ok,
        f"Cost {c.cost:.2f} versus preferred ceiling {p.preferred_max_cost:.2f}.",
        c.cost,
        p.preferred_max_cost,
        penalty=0.2,
    )


def _duration_preference(c: RouteCandidate, p: ConstraintProfile) -> ConstraintCheck | None:
    if c.duration_minutes is None or p.preferred_max_duration_minutes is None:
        return None
    ok = c.duration_minutes <= p.preferred_max_duration_minutes
    return _check(
        "PREF_DURATION",
        "Preferred transit time",
        ConstraintSeverity.SOFT,
        ok,
        (
            f"Transit {c.duration_minutes:.0f} min versus preferred "
            f"{p.preferred_max_duration_minutes:.0f} min."
        ),
        c.duration_minutes,
        p.preferred_max_duration_minutes,
        penalty=0.15,
    )


#: Order matters only for readability of the report.
RULES: tuple[tuple[str, Rule], ...] = (
    # Road-network rules run first: they are the ones the delivered graph can
    # actually verify, and a route that does not exist fails everything else.
    ("NET_LEGS", _network_legs),
    ("NET_INCIDENT", _network_incidents),
    ("NET_DISTANCE", _network_distance),
    ("NET_ENDPOINT", _network_endpoints),
    ("NET_ADVISORY", _network_advisories),
    ("CAP_WEIGHT", _weight_capacity),
    ("CAP_VOLUME", _volume_capacity),
    ("HOS_DAILY", _daily_driving_hours),
    ("HOS_BREAK", _continuous_driving),
    ("DRV_LICENCE", _licence_class),
    ("TW_WINDOW", _delivery_window),
    ("SLA_PROMISE", _sla_promise),
    ("WH_CUTOFF", _warehouse_cutoff),
    ("COLD_CHAIN", _cold_chain),
    ("HAZMAT_CERT", _hazmat),
    ("RTE_HEIGHT", _height_restriction),
    ("RTE_AXLE", _axle_load),
    ("RTE_ZONE", _restricted_zones),
    ("RTE_STOPS", _stop_count),
    ("RTE_DISTANCE", _route_distance),
    ("PREF_COST", _cost_preference),
    ("PREF_DURATION", _duration_preference),
)


def evaluate_candidate(
    candidate: RouteCandidate, profile: ConstraintProfile | None = None
) -> ConstraintReport:
    """Run every logistics constraint against one candidate solution.

    Hard violations make the candidate infeasible — the Optimization Agent must
    discard it and the Validation Agent independently re-runs this check before
    any recommendation reaches the user.
    """

    active = profile or get_constraint_profile()
    report = ConstraintReport(candidate=candidate.label)

    for code, rule in RULES:
        try:
            check = rule(candidate, active)
        except Exception as exc:  # noqa: BLE001 - a broken rule must not pass silently
            logger.warning(
                "Constraint rule errored",
                extra={"rule": code, "error": str(exc)[:200]},
            )
            report.unverifiable.append(f"{code} (rule error)")
            continue

        if check is None:
            report.unverifiable.append(code)
            continue

        report.checks.append(check)
        if not check.satisfied:
            if check.severity is ConstraintSeverity.HARD:
                report.feasible = False
            else:
                report.penalty += check.penalty

    return report


def evaluate_all(
    candidates: list[RouteCandidate], profile: ConstraintProfile | None = None
) -> list[ConstraintReport]:
    return [evaluate_candidate(candidate, profile) for candidate in candidates]


def constraint_catalogue() -> str:
    """Human/LLM readable list of the rules, for prompts and the UI."""

    profile = get_constraint_profile()
    lines = [
        "HARD constraints (a violation makes an option infeasible — never trade off):",
        "  NET_LEGS     every leg must be a real CONNECTED_TO edge in the graph",
        "  NET_INCIDENT no INTERMEDIATE stop may have an Active Critical/High incident",
        f"  NET_DISTANCE stated distance must match the graph total within "
        f"{profile.distance_match_tolerance_km} km",
        f"  CAP_WEIGHT   payload_weight_kg <= vehicle_capacity_kg x {profile.max_vehicle_utilisation}",
        f"  CAP_VOLUME   payload_volume_m3 <= vehicle_capacity_m3 x {profile.max_vehicle_utilisation}",
        f"  HOS_DAILY    driving_hours + hours_already_driven_today <= {profile.max_daily_driving_hours} h",
        f"  HOS_BREAK    driving beyond {profile.max_continuous_driving_hours} h requires "
        f">= {profile.min_break_minutes_after_continuous} min rest",
        "  DRV_LICENCE  required_licence_class must be held by the driver",
        "  TW_WINDOW    estimated_arrival must fall inside the customer delivery window",
        f"  SLA_PROMISE  estimated_arrival <= promised_delivery_by minus "
        f"{profile.sla_buffer_minutes} min buffer",
        "  WH_CUTOFF    departure_time <= warehouse dispatch cut-off",
        f"  COLD_CHAIN   refrigerated vehicle required; temperature drift <= "
        f"{profile.cold_chain_tolerance_c} °C",
        "  HAZMAT_CERT  hazmat cargo requires a certified vehicle",
        "  RTE_HEIGHT   vehicle_height_m <= route clearance",
        "  RTE_AXLE     vehicle_axle_load_kg <= route axle limit",
        "  RTE_ZONE     no restricted zones on the route",
        f"  RTE_STOPS    stop_count <= {profile.max_stops_per_route}",
        f"  RTE_DISTANCE distance_km <= {profile.max_route_distance_km}",
        "",
        "SOFT constraints (penalised and disclosed, not disqualifying):",
        "  NET_ENDPOINT  severe incident at origin/destination — unavoidable, disclosed",
        "  NET_ADVISORY  active Medium/Low incidents on the route raise delay risk",
        f"  PREF_COST     cost <= {profile.preferred_max_cost}",
        f"  PREF_DURATION duration_minutes <= {profile.preferred_max_duration_minutes}",
    ]
    return "\n".join(lines)


def report_to_text(reports: list[ConstraintReport]) -> str:
    if not reports:
        return "(no candidates evaluated)"
    lines: list[str] = []
    for report in reports:
        lines.append(report.summary())
        for check in report.checks:
            mark = "PASS" if check.satisfied else "FAIL"
            lines.append(f"    [{mark}] {check.code} {check.name}: {check.detail}")
        if report.unverifiable:
            lines.append(
                f"    [UNVERIFIED] missing data for: {', '.join(report.unverifiable)}"
            )
    return "\n".join(lines)


def write_default_profile() -> None:
    """Emit a starter constraint file so operations teams can tune limits."""

    path = PROJECT_ROOT / "logistics_constraints.json"
    if path.exists():
        return
    path.write_text(
        json.dumps(ConstraintProfile().model_dump(), indent=2), encoding="utf-8"
    )
    logger.info("Wrote default constraint profile", extra={"path": str(path)})


__all__ = [
    "ConstraintCheck",
    "ConstraintProfile",
    "ConstraintReport",
    "ConstraintSeverity",
    "RouteCandidate",
    "constraint_catalogue",
    "evaluate_all",
    "evaluate_candidate",
    "get_constraint_profile",
    "report_to_text",
    "write_default_profile",
]
