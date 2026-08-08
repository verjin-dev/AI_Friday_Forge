"""Enterprise Multi-Objective Dynamic Edge Cost Model & Optimization Modes.

Architecture & Optimization Modes:
1. Dynamic Weight Adjustment:
   - Rush Hour / Peak Traffic
   - Heavy Rain
   - Festival Traffic
   - Road Closures
2. Emergency Shipment Mode (Maximum speed, zero cost penalty)
3. Fuel Optimization Mode (Steady speed, minimum fuel consumption)
4. Green Route Mode (Lowest Carbon CO2 Emissions g/km)
5. Cheapest Route Mode (Minimum toll + fuel money)
6. Fastest Route Mode (Minimum duration)
7. Balanced Route Mode (Multi-objective compromise)

Product-Specific Scoring Profiles:
- Medicine, Perishable Goods, Furniture, Luxury Goods, Hazmat, General Cargo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.config import settings


class ProductType(str, Enum):
    """Product profiles with distinct multi-objective optimization priorities."""

    MEDICINE = "medicine"
    PERISHABLE_GOODS = "perishable_goods"
    FURNITURE = "furniture"
    LUXURY_GOODS = "luxury_goods"
    HAZMAT = "hazmat"
    GENERAL_CARGO = "general_cargo"

    @classmethod
    def parse(cls, value: str | None) -> "ProductType":
        if not value:
            return cls.GENERAL_CARGO
        cleaned = value.strip().lower().replace(" ", "_")
        for member in cls:
            if member.value == cleaned or member.name.lower() == cleaned:
                return member
        if "med" in cleaned or "pharma" in cleaned:
            return cls.MEDICINE
        if "perish" in cleaned or "food" in cleaned or "cold" in cleaned:
            return cls.PERISHABLE_GOODS
        if "furn" in cleaned or "bulky" in cleaned:
            return cls.FURNITURE
        if "lux" in cleaned or "valuable" in cleaned or "high_value" in cleaned:
            return cls.LUXURY_GOODS
        if "haz" in cleaned or "chem" in cleaned:
            return cls.HAZMAT
        return cls.GENERAL_CARGO


class OptimizationMode(str, Enum):
    """Enterprise Optimization Modes for A* search."""

    EMERGENCY = "emergency"
    FUEL_OPTIMIZATION = "fuel_optimization"
    GREEN_ROUTE = "green_route"
    CHEAPEST = "cheapest"
    FASTEST = "fastest"
    BALANCED = "balanced"

    @classmethod
    def parse(cls, value: str | None) -> "OptimizationMode":
        if not value:
            return cls.BALANCED
        cleaned = value.strip().lower().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == cleaned or member.name.lower() == cleaned:
                return member
        if "emerg" in cleaned or "express" in cleaned:
            return cls.EMERGENCY
        if "fuel" in cleaned or "eco_fuel" in cleaned:
            return cls.FUEL_OPTIMIZATION
        if "green" in cleaned or "carbon" in cleaned or "eco" in cleaned:
            return cls.GREEN_ROUTE
        if "cheap" in cleaned or "cost" in cleaned or "min_cost" in cleaned:
            return cls.CHEAPEST
        if "fast" in cleaned or "time" in cleaned or "speed" in cleaned:
            return cls.FASTEST
        return cls.BALANCED


@dataclass(slots=True)
class EnvironmentalCondition:
    """Dynamic Real-Time Environmental Conditions for weight adjustment."""

    is_rush_hour: bool = False
    is_peak_traffic: bool = False
    is_heavy_rain: bool = False
    is_festival_traffic: bool = False
    has_road_closures: bool = False
    ambient_temp_celsius: float = 28.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None = None) -> "EnvironmentalCondition":
        if not data:
            return cls()
        return cls(
            is_rush_hour=bool(data.get("is_rush_hour", False)),
            is_peak_traffic=bool(data.get("is_peak_traffic", False)),
            is_heavy_rain=bool(data.get("is_heavy_rain", False)),
            is_festival_traffic=bool(data.get("is_festival_traffic", False)),
            has_road_closures=bool(data.get("has_road_closures", False)),
            ambient_temp_celsius=float(data.get("ambient_temp_celsius", 28.0)),
        )


#: Priority multiplier on time and delay terms
PRIORITY_MULTIPLIERS: dict[str, float] = {
    "critical": 2.5,
    "high": 1.8,
    "standard": 1.0,
    "low": 0.7,
}


#: Free-flow speeds by road class, km/h.
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
    """Infer a road class from its name."""
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
    """Enterprise metadata for one edge."""

    distance_km: float
    road_class: str = "DEFAULT"
    average_speed_kmh: float = 0.0
    road_condition: float = 0.0
    road_priority: float = 0.0
    historical_delay_min: float = 0.0
    traffic_congestion_factor: float = 1.0
    weather_severity: float = 0.0
    incident_probability: float = 0.0
    toll_cost: float = 0.0
    fuel_cost_per_km: float = 0.0
    hub_congestion_delay_min: float = 0.0

    # --- Restrictions ---
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
            traffic_congestion_factor=float(get("traffic_congestion_factor", 1.0)),
            weather_severity=float(get("weather_severity", get("weather_risk", 0.0))),
            incident_probability=float(get("incident_probability", 0.0)),
            toll_cost=float(get("toll_cost", 0.0)),
            fuel_cost_per_km=float(
                get("fuel_cost_per_km", settings.fuel_cost_per_km)
            ),
            hub_congestion_delay_min=float(get("hub_congestion_delay_min", 0.0)),
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
class ShipmentContext:
    """Shipment profile and constraint parameters."""

    product_type: ProductType = ProductType.GENERAL_CARGO
    priority: str = "standard"  # critical, high, standard, low
    payload_weight_kg: float | None = None
    payload_volume_m3: float | None = None
    delivery_deadline_minutes: float | None = None
    driver_hours_remaining: float | None = None


@dataclass(slots=True)
class VehicleContext:
    """Vehicle parameters for compatibility gating and load factor scoring."""

    weight_kg: float | None = None
    capacity_kg: float | None = None
    height_m: float | None = None
    axles: int | None = None
    vehicle_type: str = "truck"  # truck, van, reefer, flatbed, hazmat_tanker, ev_van
    fuel_type: str = "diesel"  # diesel, petrol, electric, cng
    requires_hazmat: bool = False
    requires_cold_chain: bool = False
    criticality: float = 0.5

    @classmethod
    def from_profile(cls, profile: Any, criticality: float = 0.5) -> "VehicleContext":
        if profile is None:
            return cls(criticality=criticality)
        return cls(
            weight_kg=getattr(profile, "capacity_kg", None),
            capacity_kg=getattr(profile, "capacity_kg", None),
            height_m=getattr(profile, "height_m", None),
            axles=getattr(profile, "axle_count", None),
            vehicle_type=getattr(profile, "vehicle_type", "truck"),
            fuel_type=getattr(profile, "fuel_type", "diesel"),
            requires_hazmat=bool(getattr(profile, "hazmat_certified", False)),
            requires_cold_chain=bool(getattr(profile, "refrigerated", False)),
            criticality=criticality,
        )


@dataclass(slots=True)
class CostWeights:
    """Multi-Objective Weights for Dynamic Edge Cost calculation."""

    travel_time: float = 1.0
    distance: float = 1.0
    congestion: float = 1.0
    weather: float = 1.0
    money: float = 1.0
    risk: float = 1.0
    carbon: float = 1.0
    hub_congestion: float = 1.0
    road_quality: float = 1.0
    historical_delay: float = 1.0
    priority: float = 1.0

    @classmethod
    def from_mode_and_environment(
        cls,
        mode: str | OptimizationMode = OptimizationMode.BALANCED,
        env: EnvironmentalCondition | None = None,
        product_type: str | ProductType | None = None,
        priority: str = "standard",
    ) -> "CostWeights":
        opt_mode = OptimizationMode.parse(str(mode)) if isinstance(mode, str) else (mode or OptimizationMode.BALANCED)
        env_cond = env or EnvironmentalCondition()
        mult = PRIORITY_MULTIPLIERS.get(priority.lower(), 1.0)

        # 1. Base weights by Optimization Mode
        if opt_mode == OptimizationMode.EMERGENCY:
            w = cls(
                travel_time=25.0 * mult,
                congestion=15.0 * mult,
                risk=5.0,
                money=0.1,
                distance=0.5,
                carbon=0.1,
                road_quality=1.0,
                hub_congestion=10.0,
            )
        elif opt_mode == OptimizationMode.FUEL_OPTIMIZATION:
            w = cls(
                money=10.0,
                distance=8.0,
                road_quality=8.0,
                congestion=7.0,
                travel_time=2.0 * mult,
                risk=3.0,
                carbon=5.0,
            )
        elif opt_mode == OptimizationMode.GREEN_ROUTE:
            w = cls(
                carbon=15.0,
                distance=8.0,
                congestion=10.0,
                road_quality=8.0,
                travel_time=3.0 * mult,
                money=4.0,
                risk=3.0,
            )
        elif opt_mode == OptimizationMode.CHEAPEST:
            w = cls(
                money=15.0,
                distance=10.0,
                travel_time=1.5 * mult,
                congestion=3.0,
                risk=2.0,
                carbon=2.0,
            )
        elif opt_mode == OptimizationMode.FASTEST:
            w = cls(
                travel_time=15.0 * mult,
                congestion=12.0 * mult,
                distance=1.0,
                money=0.5,
                risk=3.0,
                carbon=1.0,
            )
        else:
            # BALANCED — use ProductType baseline
            w = cls.from_product_type(product_type, priority)

        # 2. Dynamic Adjustments based on Real-Time Environmental Conditions
        if env_cond.is_rush_hour or env_cond.is_peak_traffic:
            w.congestion *= 1.8
            w.travel_time *= 1.5
        if env_cond.is_heavy_rain:
            w.weather *= 2.5
            w.risk *= 2.0
            w.road_quality *= 1.5
        if env_cond.is_festival_traffic:
            w.hub_congestion *= 2.2
            w.historical_delay *= 1.8
        if env_cond.has_road_closures:
            w.risk *= 2.5
            w.road_quality *= 1.8

        return w

    @classmethod
    def from_product_type(
        cls,
        product_type: str | ProductType | None = None,
        priority: str = "standard",
    ) -> "CostWeights":
        pt = ProductType.parse(str(product_type)) if isinstance(product_type, str) else (product_type or ProductType.GENERAL_CARGO)
        mult = PRIORITY_MULTIPLIERS.get(priority.lower(), 1.0)

        if pt == ProductType.MEDICINE:
            return cls(
                travel_time=10.0 * mult,
                weather=5.0,
                congestion=8.0 * mult,
                risk=8.0,
                distance=2.0,
                money=1.0,
                hub_congestion=4.0,
                road_quality=3.0,
                historical_delay=5.0,
                priority=2.0,
            )
        elif pt == ProductType.PERISHABLE_GOODS:
            return cls(
                travel_time=9.0 * mult,
                weather=8.0,
                congestion=7.0 * mult,
                distance=3.0,
                money=2.0,
                risk=4.0,
                hub_congestion=5.0,
                road_quality=3.0,
                historical_delay=4.0,
                priority=2.0,
            )
        elif pt == ProductType.FURNITURE:
            return cls(
                money=9.0,
                distance=7.0,
                travel_time=2.0 * mult,
                road_quality=6.0,
                congestion=2.0,
                weather=3.0,
                risk=2.0,
                hub_congestion=2.0,
                historical_delay=2.0,
                priority=1.0,
            )
        elif pt == ProductType.LUXURY_GOODS:
            return cls(
                risk=10.0,
                travel_time=7.0 * mult,
                distance=3.0,
                money=4.0,
                congestion=5.0,
                weather=3.0,
                hub_congestion=4.0,
                road_quality=3.0,
                historical_delay=3.0,
                priority=3.0,
            )
        elif pt == ProductType.HAZMAT:
            return cls(
                risk=10.0,
                weather=6.0,
                travel_time=5.0 * mult,
                distance=4.0,
                money=3.0,
                congestion=6.0,
                road_quality=4.0,
                hub_congestion=3.0,
                historical_delay=3.0,
                priority=3.0,
            )
        else:
            return cls(
                travel_time=5.0 * mult,
                distance=5.0,
                money=5.0,
                risk=5.0,
                congestion=4.0,
                weather=3.0,
                hub_congestion=3.0,
                road_quality=3.0,
                historical_delay=3.0,
                priority=2.0,
            )


@dataclass(slots=True)
class CostBreakdown:
    """Comprehensive multi-objective cost breakdown explanation."""

    total: float
    travel_time: float = 0.0
    distance: float = 0.0
    congestion: float = 0.0
    weather: float = 0.0
    money: float = 0.0
    risk: float = 0.0
    carbon: float = 0.0
    co2_emissions_kg: float = 0.0
    hub_congestion: float = 0.0
    capacity_penalty: float = 0.0
    driver_hos_penalty: float = 0.0
    sla_penalty: float = 0.0
    quality: float = 0.0
    historical_delay: float = 0.0
    priority: float = 0.0
    #: Route-level ML figures. Reported, not summed into ``total`` — see the note
    #: in :meth:`CostModel.evaluate`.
    ml_expected_delay: float = 0.0
    ml_risk: float = 0.0
    blocked_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_dynamic_cost": round(self.total, 2),
            "travel_time_term": round(self.travel_time, 2),
            "distance_term": round(self.distance, 2),
            "congestion_term": round(self.congestion, 2),
            "weather_term": round(self.weather, 2),
            "money_term": round(self.money, 2),
            "risk_term": round(self.risk, 2),
            "carbon_term": round(self.carbon, 2),
            "co2_emissions_kg": round(self.co2_emissions_kg, 3),
            "hub_congestion_term": round(self.hub_congestion, 2),
            "capacity_penalty": round(self.capacity_penalty, 2),
            "driver_hos_penalty": round(self.driver_hos_penalty, 2),
            "sla_penalty": round(self.sla_penalty, 2),
            "road_quality_term": round(self.quality, 2),
            "historical_delay_term": round(self.historical_delay, 2),
            "ml_expected_delay_term": round(self.ml_expected_delay, 2),
            "ml_risk_term": round(self.ml_risk, 2),
            "blocked_reason": self.blocked_reason,
        }


class CostModel:
    """Enterprise Multi-Objective Dynamic Cost Engine for A* optimization."""

    def __init__(
        self,
        weights: CostWeights | None = None,
        vehicle: VehicleContext | None = None,
        shipment: ShipmentContext | None = None,
        ml_prediction: Any | None = None,
        mode: OptimizationMode | str = OptimizationMode.BALANCED,
        environment: EnvironmentalCondition | dict | None = None,
        *,
        penalties: dict[str, float] | None = None,
    ) -> None:
        self.shipment = shipment or ShipmentContext()
        self.vehicle = vehicle or VehicleContext()
        self.ml_prediction = ml_prediction
        self.mode = OptimizationMode.parse(str(mode)) if isinstance(mode, str) else mode
        self.environment = (
            EnvironmentalCondition.from_dict(environment)
            if isinstance(environment, dict)
            else (environment or EnvironmentalCondition())
        )
        self.weights = (
            weights
            or CostWeights.from_mode_and_environment(
                self.mode, self.environment, self.shipment.product_type, self.shipment.priority
            )
        )
        self.penalties = penalties or {}

    # ------------------------------------------------------------------
    def compatibility(self, attributes: EdgeAttributes) -> str | None:
        """Evaluate hard vehicle & shipment road restrictions."""
        # In EMERGENCY mode, bypass non-safety restrictions
        if self.mode == OptimizationMode.EMERGENCY:
            return None

        vehicle = self.vehicle
        shipment = self.shipment

        # Weight limit gate
        if (
            attributes.weight_limit_kg is not None
            and vehicle.weight_kg is not None
            and vehicle.weight_kg > attributes.weight_limit_kg
        ):
            return f"vehicle weight {vehicle.weight_kg:.0f} kg exceeds road limit {attributes.weight_limit_kg:.0f} kg"

        # Height clearance gate
        if (
            attributes.height_limit_m is not None
            and vehicle.height_m is not None
            and vehicle.height_m > attributes.height_limit_m
        ):
            return f"vehicle height {vehicle.height_m:.2f} m exceeds clearance {attributes.height_limit_m:.2f} m"

        # Hazmat restriction gate
        if (
            (vehicle.requires_hazmat or shipment.product_type == ProductType.HAZMAT)
            and attributes.hazmat_allowed is False
        ):
            return "hazmat shipments are prohibited on this road segment"

        # Cold chain restriction gate
        if (
            (vehicle.requires_cold_chain or shipment.product_type in (ProductType.MEDICINE, ProductType.PERISHABLE_GOODS))
            and attributes.cold_chain_supported is False
        ):
            return "corridor does not support required cold chain infrastructure"

        return None

    # ------------------------------------------------------------------
    def evaluate(self, leg: Any, *, extra_penalty: float = 0.0) -> CostBreakdown:
        """Compute the Dynamic Edge Cost for A* algorithm minimization."""
        attributes = EdgeAttributes.from_leg(leg)

        blocked = self.compatibility(attributes)
        if blocked:
            return CostBreakdown(total=math.inf, blocked_reason=blocked)

        weights = self.weights
        vehicle = self.vehicle
        shipment = self.shipment

        # 1. Travel Time Term
        free_flow = attributes.free_flow_minutes
        travel_time_term = free_flow * weights.travel_time

        # 2. Distance Term
        distance_term = attributes.distance_km * settings.minutes_per_km_distance_weight * weights.distance

        # 3. Traffic Congestion Term
        congestion_multiplier = max(attributes.traffic_congestion_factor, 1.0)
        congestion_term = (congestion_multiplier - 1.0) * free_flow * weights.congestion

        # 4. Weather Severity Term
        weather_term = attributes.weather_severity * settings.weather_risk_minutes * weights.weather

        # 5 & 6. Fuel Cost & Toll Charges Term
        money_cost = attributes.toll_cost + (attributes.fuel_cost_per_km * attributes.distance_km)
        money_term = (money_cost / max(settings.cost_per_minute, 0.0001)) * weights.money

        # Green Route Carbon Emission Model (CO2 g/km)
        # Diesel truck baseline: 2680 g/L fuel; Electric Vehicle (EV): 0 g/L direct emission
        fuel_l_per_km = 0.0 if vehicle.fuel_type == "electric" else (0.25 if vehicle.vehicle_type == "truck" else 0.12)
        idle_delay_min = (congestion_multiplier - 1.0) * free_flow
        fuel_consumed_liters = (fuel_l_per_km * attributes.distance_km) + (idle_delay_min * 0.04)
        co2_emissions_kg = (fuel_consumed_liters * 2.68) if vehicle.fuel_type != "electric" else 0.0
        carbon_term = co2_emissions_kg * 10.0 * weights.carbon

        # 7. Hub Congestion Term
        hub_term = attributes.hub_congestion_delay_min * weights.hub_congestion

        # 8. Capacity & Load Factor
        capacity_penalty = 0.0
        if vehicle.capacity_kg and shipment.payload_weight_kg:
            load_factor = shipment.payload_weight_kg / vehicle.capacity_kg
            if load_factor > 1.0 and self.mode != OptimizationMode.EMERGENCY:
                return CostBreakdown(
                    total=math.inf,
                    blocked_reason=f"payload {shipment.payload_weight_kg:.0f} kg exceeds vehicle capacity {vehicle.capacity_kg:.0f} kg",
                )
            elif load_factor > 0.85:
                capacity_penalty = (load_factor - 0.85) * 15.0

        # Priority, SLA & HOS
        priority_term = (free_flow * (1.0 - attributes.road_priority) * settings.priority_penalty_factor) * weights.priority

        driver_hos_penalty = 0.0
        if shipment.driver_hours_remaining is not None and free_flow > (shipment.driver_hours_remaining * 60):
            driver_hos_penalty = 50.0

        sla_penalty = 0.0
        if shipment.delivery_deadline_minutes is not None and free_flow > shipment.delivery_deadline_minutes:
            sla_penalty = (free_flow - shipment.delivery_deadline_minutes) * 2.0

        # Risk & ML Prediction influence
        risk_term = attributes.incident_probability * settings.incident_risk_minutes * weights.risk

        # The ML prediction is a single route-level figure. Adding it to every
        # edge made it a per-hop surcharge — with the delivered data it was ~57%
        # of each edge's cost, identical on all of them, so the search was driven
        # by hop count and the incident penalties that are supposed to decide the
        # route were drowned out. A constant cannot discriminate between
        # candidates for the same origin and destination, so it is reported for
        # transparency and kept out of the traversal cost.
        ml_risk_term = 0.0
        ml_delay_term = 0.0
        if self.ml_prediction:
            ml_risk_score = getattr(self.ml_prediction, "route_risk_score", 0.0)
            ml_expected_delay = getattr(self.ml_prediction, "expected_delay_minutes", 0.0)
            ml_risk_term = ml_risk_score * 20.0 * weights.risk
            ml_delay_term = ml_expected_delay * 0.1 * weights.travel_time

        quality_term = free_flow * (1.0 - attributes.road_condition) * weights.road_quality
        delay_term = attributes.historical_delay_min * weights.historical_delay

        # Total Dynamic Edge Cost minimized by A*
        total_dynamic_cost = (
            travel_time_term
            + distance_term
            + congestion_term
            + weather_term
            + money_term
            + carbon_term
            + hub_term
            + capacity_penalty
            + driver_hos_penalty
            + sla_penalty
            + risk_term
            + quality_term
            + delay_term
            + priority_term
            + extra_penalty
        )

        return CostBreakdown(
            total=total_dynamic_cost,
            travel_time=travel_time_term,
            distance=distance_term,
            congestion=congestion_term,
            weather=weather_term,
            money=money_term,
            risk=risk_term,
            carbon=carbon_term,
            co2_emissions_kg=co2_emissions_kg,
            hub_congestion=hub_term,
            capacity_penalty=capacity_penalty,
            driver_hos_penalty=driver_hos_penalty,
            sla_penalty=sla_penalty,
            quality=quality_term,
            historical_delay=delay_term,
            priority=priority_term,
            ml_expected_delay=ml_delay_term,
            ml_risk=ml_risk_term,
        )

    def cost(self, leg: Any, *, extra_penalty: float = 0.0) -> float:
        return self.evaluate(leg, extra_penalty=extra_penalty).total

    # ------------------------------------------------------------------
    @property
    def fastest_speed_kmh(self) -> float:
        return max(ROAD_CLASS_SPEED.values())

    def heuristic_minutes(self, straight_line_km: float) -> float:
        min_weight = min(
            self.weights.travel_time,
            self.weights.distance,
            self.weights.money,
            1.0,
        )
        return (straight_line_km / self.fastest_speed_kmh) * 60 * min_weight
