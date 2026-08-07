"""Enterprise edge cost model.

Every routing decision reduces to one question: what does traversing this edge
actually cost the business? Distance alone answers that badly — a 10 km hop on
a broken district road with a history of delays is worse than 14 km of national
highway.

The model expresses everything in **effective minutes**, so distance, delay
risk, tolls and fuel are directly comparable and the resulting path cost is
still something an operator can reason about. Money is converted through
``cost_per_minute``, which is the fleet's own time valuation.

Vehicle incompatibility (over weight limit, hazmat on a prohibited road, cold
chain on an unsupported corridor) returns infinity rather than a large penalty.
That makes it a genuine feasibility gate inside the search, not a preference
the optimiser can trade away.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings


#: Free-flow speeds by road class, km/h. Used when an edge carries no
#: `average_speed` of its own.
ROAD_CLASS_SPEED: dict[str, float] = {
    "NH": 55.0,
    "SH": 45.0,
    "MC": 45.0,
    "CITY": 25.0,
    "LOCAL": 30.0,
    "DEFAULT": 40.0,
}

#: Surface quality by class, 0..1. Multiplies into a time penalty.
ROAD_CLASS_CONDITION: dict[str, float] = {
    "NH": 0.92,
    "SH": 0.82,
    "MC": 0.80,
    "CITY": 0.70,
    "LOCAL": 0.65,
    "DEFAULT": 0.75,
}

#: Relative preference: higher is a more strategic corridor.
ROAD_CLASS_PRIORITY: dict[str, float] = {
    "NH": 1.0,
    "SH": 0.8,
    "MC": 0.75,
    "CITY": 0.5,
    "LOCAL": 0.45,
    "DEFAULT": 0.6,
}


def classify_road(road_name: str, kind: str = "primary") -> str:
    """Infer a road class from its name — the dataset has no explicit column."""

    text = (road_name or "").strip().upper()
    if text.startswith("NH"):
        return "NH"
    if text.startswith("SH"):
        return "SH"
    if "MC ROAD" in text or text.startswith("MC"):
        return "MC"
    if any(token in text for token in ("CITY", "RING", "MG ROAD", "BYPASS")):
        return "CITY"
    if kind == "alternate" or any(
        token in text for token in ("SERVICE", "LOCAL", "INNER", "LINK", "OLD")
    ):
        return "LOCAL"
    return "DEFAULT"


@dataclass(slots=True)
class EdgeAttributes:
    """Enterprise metadata for one edge.

    Values present on the edge in Neo4j win; anything absent is derived from the
    road class so the model degrades predictably instead of failing.
    """

    distance_km: float
    road_class: str = "DEFAULT"
    average_speed_kmh: float = 0.0
    road_condition: float = 0.0
    road_priority: float = 0.0
    historical_delay_min: float = 0.0
    incident_probability: float = 0.0
    weather_risk: float = 0.0
    toll_cost: float = 0.0
    fuel_cost_per_km: float = 0.0

    # --- restrictions: None means "unknown / unrestricted" ---
    weight_limit_kg: float | None = None
    height_limit_m: float | None = None
    axle_limit: int | None = None
    hazmat_allowed: bool | None = None
    cold_chain_supported: bool | None = None

    @classmethod
    def from_leg(cls, leg: Any) -> "EdgeAttributes":
        road_class = getattr(leg, "road_class", None) or classify_road(
            getattr(leg, "road_name", ""), getattr(leg, "kind", "primary")
        )
        get = lambda name, default=None: getattr(leg, name, None) or default  # noqa: E731

        return cls(
            distance_km=float(getattr(leg, "distance_km", 0.0) or 0.0),
            road_class=road_class,
            average_speed_kmh=float(
                get("average_speed_kmh", ROAD_CLASS_SPEED.get(road_class, 40.0))
            ),
            road_condition=float(
                get("road_condition", ROAD_CLASS_CONDITION.get(road_class, 0.75))
            ),
            road_priority=float(
                get("road_priority", ROAD_CLASS_PRIORITY.get(road_class, 0.6))
            ),
            historical_delay_min=float(get("historical_delay_min", 0.0)),
            incident_probability=float(get("incident_probability", 0.0)),
            weather_risk=float(get("weather_risk", 0.0)),
            toll_cost=float(get("toll_cost", 0.0)),
            fuel_cost_per_km=float(
                get("fuel_cost_per_km", settings.fuel_cost_per_km)
            ),
            weight_limit_kg=getattr(leg, "weight_limit_kg", None),
            height_limit_m=getattr(leg, "height_limit_m", None),
            axle_limit=getattr(leg, "axle_limit", None),
            hazmat_allowed=getattr(leg, "hazmat_allowed", None),
            cold_chain_supported=getattr(leg, "cold_chain_supported", None),
        )

    @property
    def free_flow_minutes(self) -> float:
        speed = self.average_speed_kmh or ROAD_CLASS_SPEED["DEFAULT"]
        return (self.distance_km / speed) * 60 if speed > 0 else 0.0


@dataclass(slots=True)
class VehicleContext:
    """What the vehicle needs, for compatibility gating inside the search."""

    weight_kg: float | None = None
    height_m: float | None = None
    axles: int | None = None
    requires_hazmat: bool = False
    requires_cold_chain: bool = False
    #: Shipment urgency, 0..1. Raises the weight on time versus money.
    criticality: float = 0.5

    @classmethod
    def from_profile(cls, profile: Any, criticality: float = 0.5) -> "VehicleContext":
        if profile is None:
            return cls(criticality=criticality)
        return cls(
            weight_kg=getattr(profile, "capacity_kg", None),
            height_m=getattr(profile, "height_m", None),
            axles=getattr(profile, "axle_count", None),
            requires_hazmat=bool(getattr(profile, "hazmat_certified", False)),
            requires_cold_chain=bool(getattr(profile, "refrigerated", False)),
            criticality=criticality,
        )


@dataclass(slots=True)
class CostWeights:
    """Tunable weighting. Defaults come from settings; override per request."""

    distance: float = 1.0
    travel_time: float = 1.0
    road_quality: float = 1.0
    historical_delay: float = 1.0
    risk: float = 1.0
    money: float = 1.0
    priority: float = 1.0
    criticality: float = 1.0

    @classmethod
    def from_settings(cls) -> "CostWeights":
        return cls(
            distance=settings.weight_distance,
            travel_time=settings.weight_travel_time,
            road_quality=settings.weight_road_quality,
            historical_delay=settings.weight_historical_delay,
            risk=settings.weight_risk,
            money=settings.weight_money,
            priority=settings.weight_priority,
            criticality=settings.weight_criticality,
        )


@dataclass(slots=True)
class CostBreakdown:
    """Per-edge explanation — the Explanation Agent quotes these numbers."""

    total: float
    travel_time: float = 0.0
    distance: float = 0.0
    quality: float = 0.0
    historical_delay: float = 0.0
    risk: float = 0.0
    money: float = 0.0
    priority: float = 0.0
    blocked_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_effective_minutes": round(self.total, 2),
            "travel_time": round(self.travel_time, 2),
            "distance": round(self.distance, 2),
            "road_quality": round(self.quality, 2),
            "historical_delay": round(self.historical_delay, 2),
            "risk": round(self.risk, 2),
            "money": round(self.money, 2),
            "priority": round(self.priority, 2),
            "blocked_reason": self.blocked_reason,
        }


class CostModel:
    """Turns an edge into an effective-minutes cost.

    Stateless and pure, so it is trivially unit-testable and safe to share
    across concurrent searches.
    """

    def __init__(
        self,
        weights: CostWeights | None = None,
        vehicle: VehicleContext | None = None,
        *,
        penalties: dict[str, float] | None = None,
    ) -> None:
        self.weights = weights or CostWeights.from_settings()
        self.vehicle = vehicle or VehicleContext()
        self.penalties = penalties or {}

    # ------------------------------------------------------------------
    def compatibility(self, attributes: EdgeAttributes) -> str | None:
        """Return a reason this vehicle may not use the edge, or None."""

        vehicle = self.vehicle
        if (
            attributes.weight_limit_kg is not None
            and vehicle.weight_kg is not None
            and vehicle.weight_kg > attributes.weight_limit_kg
        ):
            return (
                f"vehicle {vehicle.weight_kg:.0f} kg exceeds the "
                f"{attributes.weight_limit_kg:.0f} kg limit"
            )
        if (
            attributes.height_limit_m is not None
            and vehicle.height_m is not None
            and vehicle.height_m > attributes.height_limit_m
        ):
            return (
                f"vehicle {vehicle.height_m:.2f} m exceeds the "
                f"{attributes.height_limit_m:.2f} m clearance"
            )
        if (
            attributes.axle_limit is not None
            and vehicle.axles is not None
            and vehicle.axles > attributes.axle_limit
        ):
            return f"{vehicle.axles} axles exceeds the {attributes.axle_limit} limit"
        if vehicle.requires_hazmat and attributes.hazmat_allowed is False:
            return "hazmat is not permitted on this road"
        if vehicle.requires_cold_chain and attributes.cold_chain_supported is False:
            return "corridor does not support cold chain"
        return None

    # ------------------------------------------------------------------
    def evaluate(self, leg: Any, *, extra_penalty: float = 0.0) -> CostBreakdown:
        attributes = EdgeAttributes.from_leg(leg)

        blocked = self.compatibility(attributes)
        if blocked:
            return CostBreakdown(total=math.inf, blocked_reason=blocked)

        weights = self.weights
        # Urgency shifts emphasis from money onto time.
        urgency = 0.5 + self.vehicle.criticality

        travel = attributes.free_flow_minutes
        time_term = travel * weights.travel_time * urgency

        # Distance matters beyond the time it takes: wear, driver hours, tolls
        # by the kilometre. Expressed as a small per-km minute equivalent.
        distance_term = (
            attributes.distance_km * settings.minutes_per_km_distance_weight
        ) * weights.distance

        # Poor surface costs time proportionally to how long you are on it.
        quality_term = travel * (1.0 - attributes.road_condition) * weights.road_quality

        delay_term = attributes.historical_delay_min * weights.historical_delay

        risk_term = (
            attributes.incident_probability * settings.incident_risk_minutes
            + attributes.weather_risk * settings.weather_risk_minutes
        ) * weights.risk

        money = attributes.toll_cost + attributes.fuel_cost_per_km * attributes.distance_km
        money_term = (money / max(settings.cost_per_minute, 0.0001)) * weights.money

        # Prefer strategic corridors: a small penalty for low-priority roads.
        priority_term = (
            travel * (1.0 - attributes.road_priority) * settings.priority_penalty_factor
        ) * weights.priority

        total = (
            time_term
            + distance_term
            + quality_term
            + delay_term
            + risk_term
            + money_term
            + priority_term
            + extra_penalty
        )

        return CostBreakdown(
            total=total,
            travel_time=time_term,
            distance=distance_term,
            quality=quality_term,
            historical_delay=delay_term,
            risk=risk_term,
            money=money_term,
            priority=priority_term,
        )

    def cost(self, leg: Any, *, extra_penalty: float = 0.0) -> float:
        return self.evaluate(leg, extra_penalty=extra_penalty).total

    # ------------------------------------------------------------------
    @property
    def fastest_speed_kmh(self) -> float:
        """Upper speed bound, for an admissible A* heuristic."""

        return max(ROAD_CLASS_SPEED.values())

    def heuristic_minutes(self, straight_line_km: float) -> float:
        """Never over-estimates: straight line at the best possible speed.

        Admissibility is what makes A* return the same optimum as Dijkstra, so
        this deliberately ignores every penalty term.
        """

        return (straight_line_km / self.fastest_speed_kmh) * 60 * self.weights.travel_time
