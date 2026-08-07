"""Unit tests for Enterprise Multi-Objective Route Optimizer and Dynamic Edge Cost model."""

import math
import pytest
from app.routing.cost import (
    CostBreakdown,
    CostModel,
    CostWeights,
    EdgeAttributes,
    ProductType,
    ShipmentContext,
    VehicleContext,
)
from app.routing.overlay import GraphOverlay, GraphProjection
from app.routing.strategies import AStarStrategy, SearchResult

COORDS = {
    "Kochi": {"latitude": 9.9312, "longitude": 76.2673},
    "Alappuzha": {"latitude": 9.4981, "longitude": 76.3388},
    "Haripad": {"latitude": 9.2833, "longitude": 76.4667},
    "Kayamkulam": {"latitude": 9.1800, "longitude": 76.5010},
    "Kollam": {"latitude": 8.8932, "longitude": 76.6141},
    "Attingal": {"latitude": 8.6957, "longitude": 76.8155},
    "Thiruvananthapuram": {"latitude": 8.5241, "longitude": 76.9366},
}


class DummyLeg:
    def __init__(
        self,
        from_location: str = "A",
        to_location: str = "B",
        distance_km: float = 20.0,
        road_name: str = "NH66",
        average_speed_kmh: float = 60.0,
        traffic_congestion_factor: float = 1.0,
        weather_severity: float = 0.0,
        incident_probability: float = 0.0,
        toll_cost: float = 100.0,
        fuel_cost_per_km: float = 15.0,
        hub_congestion_delay_min: float = 0.0,
        weight_limit_kg: float | None = 40000.0,
        height_limit_m: float | None = 4.5,
        hazmat_allowed: bool = True,
        cold_chain_supported: bool = True,
    ):
        self.from_location = from_location
        self.to_location = to_location
        self.distance_km = distance_km
        self.road_name = road_name
        self.average_speed_kmh = average_speed_kmh
        self.traffic_congestion_factor = traffic_congestion_factor
        self.weather_severity = weather_severity
        self.incident_probability = incident_probability
        self.toll_cost = toll_cost
        self.fuel_cost_per_km = fuel_cost_per_km
        self.hub_congestion_delay_min = hub_congestion_delay_min
        self.weight_limit_kg = weight_limit_kg
        self.height_limit_m = height_limit_m
        self.hazmat_allowed = hazmat_allowed
        self.cold_chain_supported = cold_chain_supported
        self.kind = "primary"


def test_product_type_weights_medicine():
    weights = CostWeights.from_product_type(ProductType.MEDICINE, priority="critical")
    # Medicine priorities: Time > Weather > Distance > Cost
    assert weights.travel_time > weights.distance
    assert weights.weather > weights.money
    assert weights.risk > weights.money


def test_product_type_weights_furniture():
    weights = CostWeights.from_product_type(ProductType.FURNITURE, priority="standard")
    # Furniture priorities: Cost > Distance > Time
    assert weights.money > weights.travel_time
    assert weights.distance > weights.travel_time


def test_product_type_weights_luxury_goods():
    weights = CostWeights.from_product_type(ProductType.LUXURY_GOODS, priority="standard")
    # Luxury Goods priorities: Security/Risk > Time > Distance
    assert weights.risk > weights.travel_time
    assert weights.risk > weights.distance


def test_dynamic_edge_cost_cold_chain_gate():
    leg = DummyLeg(cold_chain_supported=False)
    shipment = ShipmentContext(product_type=ProductType.MEDICINE)
    vehicle = VehicleContext(requires_cold_chain=True)
    model = CostModel(vehicle=vehicle, shipment=shipment)

    breakdown = model.evaluate(leg)
    assert math.isinf(breakdown.total)
    assert "cold chain" in breakdown.blocked_reason.lower()


def test_dynamic_edge_cost_weight_limit_gate():
    leg = DummyLeg(weight_limit_kg=15000.0)
    shipment = ShipmentContext(payload_weight_kg=25000.0)
    vehicle = VehicleContext(weight_kg=25000.0)
    model = CostModel(vehicle=vehicle, shipment=shipment)

    breakdown = model.evaluate(leg)
    assert math.isinf(breakdown.total)
    assert "exceeds" in breakdown.blocked_reason.lower()


def test_dynamic_edge_cost_15_factor_calculation():
    leg = DummyLeg(
        distance_km=50.0,
        traffic_congestion_factor=1.4,
        weather_severity=0.6,
        incident_probability=0.2,
        toll_cost=150.0,
        hub_congestion_delay_min=10.0,
    )
    shipment = ShipmentContext(
        product_type=ProductType.GENERAL_CARGO,
        priority="high",
        payload_weight_kg=12000.0,
        delivery_deadline_minutes=30.0,
        driver_hours_remaining=0.5,
    )
    vehicle = VehicleContext(weight_kg=12000.0, capacity_kg=13000.0)
    model = CostModel(vehicle=vehicle, shipment=shipment)

    breakdown = model.evaluate(leg)
    assert breakdown.total > 0
    assert not math.isinf(breakdown.total)
    assert breakdown.congestion > 0
    assert breakdown.weather > 0
    assert breakdown.money > 0
    assert breakdown.hub_congestion > 0
    assert breakdown.capacity_penalty > 0
    assert breakdown.driver_hos_penalty > 0


def test_astar_minimizes_dynamic_edge_cost(clear_network):
    projection = GraphProjection(clear_network, GraphOverlay(), COORDS)

    # Route for Medicine (Time priority)
    med_shipment = ShipmentContext(product_type=ProductType.MEDICINE)
    med_model = CostModel(shipment=med_shipment)

    # Route for Furniture (Cost priority)
    furn_shipment = ShipmentContext(product_type=ProductType.FURNITURE)
    furn_model = CostModel(shipment=furn_shipment)

    astar = AStarStrategy()
    res_med = astar.find(projection, "Kochi", "Thiruvananthapuram", med_model)
    res_furn = astar.find(projection, "Kochi", "Thiruvananthapuram", furn_model)

    assert res_med is not None
    assert res_furn is not None
    assert res_med.cost > 0
    assert res_furn.cost > 0
