"""Unit tests for Optimization Modes and Environmental Dynamic Weight Adjustment."""

import pytest
from app.routing.cost import (
    CostModel,
    CostWeights,
    EnvironmentalCondition,
    OptimizationMode,
    ProductType,
    ShipmentContext,
    VehicleContext,
)
from app.routing.overlay import GraphOverlay, GraphProjection
from app.routing.strategies import AStarStrategy

COORDS = {
    "Kochi": {"latitude": 9.9312, "longitude": 76.2673},
    "Alappuzha": {"latitude": 9.4981, "longitude": 76.3388},
    "Haripad": {"latitude": 9.2833, "longitude": 76.4667},
    "Kayamkulam": {"latitude": 9.1800, "longitude": 76.5010},
    "Kollam": {"latitude": 8.8932, "longitude": 76.6141},
    "Attingal": {"latitude": 8.6957, "longitude": 76.8155},
    "Thiruvananthapuram": {"latitude": 8.5241, "longitude": 76.9366},
}


def test_emergency_mode_prioritizes_speed():
    weights = CostWeights.from_mode_and_environment(OptimizationMode.EMERGENCY)
    assert weights.travel_time > weights.money
    assert weights.travel_time > weights.distance
    assert weights.money == 0.1


def test_green_route_mode_prioritizes_carbon():
    weights = CostWeights.from_mode_and_environment(OptimizationMode.GREEN_ROUTE)
    assert weights.carbon > weights.travel_time
    assert weights.carbon > weights.money


def test_fuel_optimization_mode():
    weights = CostWeights.from_mode_and_environment(OptimizationMode.FUEL_OPTIMIZATION)
    assert weights.money > weights.travel_time
    assert weights.road_quality > weights.travel_time


def test_cheapest_mode():
    weights = CostWeights.from_mode_and_environment(OptimizationMode.CHEAPEST)
    assert weights.money > weights.travel_time
    assert weights.distance > weights.travel_time


def test_fastest_mode():
    weights = CostWeights.from_mode_and_environment(OptimizationMode.FASTEST)
    assert weights.travel_time > weights.distance
    assert weights.travel_time > weights.money


def test_environmental_adjustments_rush_hour():
    env = EnvironmentalCondition(is_rush_hour=True)
    base_weights = CostWeights.from_mode_and_environment(OptimizationMode.BALANCED)
    rush_weights = CostWeights.from_mode_and_environment(OptimizationMode.BALANCED, env=env)

    assert rush_weights.congestion > base_weights.congestion
    assert rush_weights.travel_time > base_weights.travel_time


def test_environmental_adjustments_heavy_rain():
    env = EnvironmentalCondition(is_heavy_rain=True)
    base_weights = CostWeights.from_mode_and_environment(OptimizationMode.BALANCED)
    rain_weights = CostWeights.from_mode_and_environment(OptimizationMode.BALANCED, env=env)

    assert rain_weights.weather > base_weights.weather
    assert rain_weights.risk > base_weights.risk


def test_environmental_adjustments_festival_and_closures():
    env = EnvironmentalCondition(is_festival_traffic=True, has_road_closures=True)
    base_weights = CostWeights.from_mode_and_environment(OptimizationMode.BALANCED)
    env_weights = CostWeights.from_mode_and_environment(OptimizationMode.BALANCED, env=env)

    assert env_weights.hub_congestion > base_weights.hub_congestion
    assert env_weights.risk > base_weights.risk


def test_astar_search_with_green_route_mode(clear_network):
    projection = GraphProjection(clear_network, GraphOverlay(), COORDS)
    model = CostModel(mode=OptimizationMode.GREEN_ROUTE)
    astar = AStarStrategy()
    res = astar.find(projection, "Kochi", "Thiruvananthapuram", model)

    assert res is not None
    assert res.cost > 0


def test_astar_search_with_emergency_mode(clear_network):
    projection = GraphProjection(clear_network, GraphOverlay(), COORDS)
    model = CostModel(mode=OptimizationMode.EMERGENCY)
    astar = AStarStrategy()
    res = astar.find(projection, "Kochi", "Thiruvananthapuram", model)

    assert res is not None
    assert res.cost > 0
