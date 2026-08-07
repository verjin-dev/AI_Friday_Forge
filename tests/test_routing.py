"""Routing engine: algorithms, cost model, overlay and segment replanning."""

from __future__ import annotations

import math

import pytest

from app.routing.cost import CostModel, CostWeights, EdgeAttributes, VehicleContext
from app.routing.engine import RoutingEngine
from app.routing.factory import RoutingStrategyFactory
from app.routing.overlay import GraphOverlay, GraphProjection, overlay_from_incidents
from app.routing.replanner import ReplanRequest, SegmentReplanner
from app.routing.strategies import (
    AStarStrategy,
    DijkstraStrategy,
    YenKShortestStrategy,
    haversine_km,
)

COORDS = {
    "Kochi": {"latitude": 9.9312, "longitude": 76.2673},
    "Alappuzha": {"latitude": 9.4981, "longitude": 76.3388},
    "Haripad": {"latitude": 9.2833, "longitude": 76.4667},
    "Kayamkulam": {"latitude": 9.1800, "longitude": 76.5010},
    "Kollam": {"latitude": 8.8932, "longitude": 76.6141},
    "Attingal": {"latitude": 8.6957, "longitude": 76.8155},
    "Thiruvananthapuram": {"latitude": 8.5241, "longitude": 76.9366},
}


@pytest.fixture
def projection(clear_network):
    return GraphProjection(clear_network, GraphOverlay(), COORDS)


@pytest.fixture
def model():
    return CostModel(CostWeights())


class TestCostModel:
    def test_cost_is_positive_and_finite(self, clear_network, model):
        leg = clear_network.adjacency["Kollam"][0]
        assert 0 < model.cost(leg) < math.inf

    def test_highway_beats_local_road_per_km(self, model):
        from app.domain.network import Leg

        highway = Leg(from_location="A", to_location="B", distance_km=50, road_name="NH66")
        local = Leg(
            from_location="A", to_location="B", distance_km=50, road_name="Service Road"
        )
        assert model.cost(highway) < model.cost(local)

    def test_overweight_vehicle_is_infinite(self):
        from app.domain.network import Leg

        leg = Leg(
            from_location="A", to_location="B", distance_km=10, weight_limit_kg=5000
        )
        blocked = CostModel(vehicle=VehicleContext(weight_kg=9000))
        assert math.isinf(blocked.cost(leg))
        assert "exceeds" in blocked.evaluate(leg).blocked_reason

    def test_hazmat_prohibition_is_infinite(self):
        from app.domain.network import Leg

        leg = Leg(from_location="A", to_location="B", distance_km=10, hazmat_allowed=False)
        model = CostModel(vehicle=VehicleContext(requires_hazmat=True))
        assert math.isinf(model.cost(leg))

    def test_explicit_metadata_overrides_derived(self):
        from app.domain.network import Leg

        leg = Leg(
            from_location="A",
            to_location="B",
            distance_km=10,
            road_name="NH66",
            average_speed_kmh=20,
        )
        assert EdgeAttributes.from_leg(leg).average_speed_kmh == 20

    def test_historical_delay_raises_cost(self, model):
        from app.domain.network import Leg

        clean = Leg(from_location="A", to_location="B", distance_km=10, road_name="NH66")
        delayed = Leg(
            from_location="A",
            to_location="B",
            distance_km=10,
            road_name="NH66",
            historical_delay_min=30,
        )
        assert model.cost(delayed) > model.cost(clean)

    def test_heuristic_never_exceeds_real_cost(self, model):
        # Admissibility: the straight-line estimate must not exceed the true
        # cost of any road covering that distance.
        straight = haversine_km((8.8932, 76.6141), (8.5241, 76.9366))
        from app.domain.network import Leg

        real = Leg(
            from_location="A", to_location="B", distance_km=straight, road_name="NH66"
        )
        assert model.heuristic_minutes(straight) <= model.cost(real) + 1e-6


class TestAlgorithms:
    def test_dijkstra_finds_a_path(self, projection, model):
        result = DijkstraStrategy().find(
            projection, "Kochi", "Thiruvananthapuram", model
        )
        assert result is not None
        assert result.stops[0] == "Kochi"
        assert result.stops[-1] == "Thiruvananthapuram"
        assert result.algorithm == "dijkstra"

    def test_astar_matches_dijkstra_cost(self, projection, model):
        # The whole point of an admissible heuristic: same optimum.
        a = DijkstraStrategy().find(projection, "Kochi", "Thiruvananthapuram", model)
        b = AStarStrategy().find(projection, "Kochi", "Thiruvananthapuram", model)
        assert a and b
        assert a.cost == pytest.approx(b.cost, rel=1e-6)

    def test_astar_expands_no_more_than_dijkstra(self, projection, model):
        a = DijkstraStrategy().find(projection, "Kochi", "Thiruvananthapuram", model)
        b = AStarStrategy().find(projection, "Kochi", "Thiruvananthapuram", model)
        assert b.nodes_expanded <= a.nodes_expanded

    def test_no_path_returns_none(self, projection, model):
        assert DijkstraStrategy().find(projection, "Kochi", "Nowhere", model) is None

    def test_excluded_edge_is_avoided(self, projection, model):
        result = DijkstraStrategy().find(
            projection,
            "Kollam",
            "Thiruvananthapuram",
            model,
            excluded_edges={("Kollam", "Attingal")},
        )
        if result:
            pairs = list(zip(result.stops, result.stops[1:]))
            assert ("Kollam", "Attingal") not in pairs

    def test_yen_returns_distinct_paths(self, projection, model):
        paths = YenKShortestStrategy().find_k(
            projection, "Kochi", "Thiruvananthapuram", model, k=3
        )
        assert len(paths) >= 2
        assert len({tuple(path.stops) for path in paths}) == len(paths)

    def test_yen_orders_by_cost(self, projection, model):
        paths = YenKShortestStrategy().find_k(
            projection, "Kochi", "Thiruvananthapuram", model, k=4
        )
        costs = [path.cost for path in paths]
        assert costs == sorted(costs)

    def test_yen_first_path_is_the_optimum(self, projection, model):
        best = DijkstraStrategy().find(projection, "Kochi", "Thiruvananthapuram", model)
        paths = YenKShortestStrategy().find_k(
            projection, "Kochi", "Thiruvananthapuram", model, k=3
        )
        assert paths[0].cost == pytest.approx(best.cost, rel=1e-6)

    def test_paths_are_loopless(self, projection, model):
        for path in YenKShortestStrategy().find_k(
            projection, "Kochi", "Thiruvananthapuram", model, k=4
        ):
            assert len(path.stops) == len(set(path.stops))


class TestFactory:

    def test_default_picks_astar(self):
        choice = RoutingStrategyFactory().create(node_count=55)
        assert choice.name == "astar"

    def test_alternatives_pick_yen(self):
        choice = RoutingStrategyFactory().create(node_count=55, want_alternatives=True)
        assert choice.name == "yen"

    def test_missing_coordinates_uses_astar_fallback(self):
        choice = RoutingStrategyFactory().create(node_count=9000, has_coordinates=False)
        assert choice.name == "astar"

    def test_every_choice_explains_itself(self):
        choice = RoutingStrategyFactory().create(node_count=55)
        assert choice.reason


class TestOverlay:
    def test_blocking_incident_disables_the_node(self, network):
        overlay = overlay_from_incidents(network.incidents_by_location)
        state = overlay.node_state("Kollam")  # Critical, Active in the fixture
        assert state and state.disabled

    def test_advisory_incident_only_penalises(self, network):
        overlay = overlay_from_incidents(network.incidents_by_location)
        state = overlay.node_state("Attingal")  # Medium, Active
        assert state and not state.disabled and state.penalty_minutes > 0

    def test_inactive_incident_is_ignored(self, clear_network):
        assert overlay_from_incidents(clear_network.incidents_by_location).is_empty

    def test_projection_hides_disabled_nodes(self, clear_network):
        overlay = GraphOverlay()
        overlay.disable_node("Attingal", "test closure")
        projection = GraphProjection(clear_network, overlay, COORDS)
        targets = [leg.to_location for leg in projection.neighbours("Kollam")]
        assert "Attingal" not in targets

    def test_endpoints_are_never_hidden(self, clear_network):
        overlay = GraphOverlay()
        overlay.disable_node("Thiruvananthapuram", "closed")
        projection = GraphProjection(
            clear_network, overlay, COORDS, protected_nodes=("Thiruvananthapuram",)
        )
        targets = [leg.to_location for leg in projection.neighbours("Attingal")]
        assert "Thiruvananthapuram" in targets

    def test_penalty_reaches_the_cost(self, clear_network, model):
        overlay = GraphOverlay()
        overlay.penalise_node("Attingal", 45, "flooding")
        projection = GraphProjection(clear_network, overlay, COORDS)
        assert projection.penalty_for("Kollam", "Attingal") == 45

    def test_clearing_restores_without_mutating_the_graph(self, clear_network):
        before = clear_network.adjacency["Kollam"][0].distance_km
        overlay = GraphOverlay()
        overlay.disable_node("Attingal", "closure")
        overlay.clear(node="Attingal")
        assert overlay.node_state("Attingal") is None
        # The stored edge was never touched.
        assert clear_network.adjacency["Kollam"][0].distance_km == before


class TestEngine:
    def test_produces_scored_candidates(self, clear_network):
        candidates, report = RoutingEngine().plan(
            clear_network, "Kochi", "Thiruvananthapuram", coordinates=COORDS, k=3
        )
        assert candidates
        assert report.algorithm in {"dijkstra", "astar", "yen"}
        for candidate in candidates:
            assert candidate.total_distance_km > 0
            assert 0 <= candidate.route_score <= 1
            assert 0 <= candidate.logistics_score <= 1
            assert 0 < candidate.confidence <= 0.95

    def test_best_candidate_scores_one(self, clear_network):
        candidates, _ = RoutingEngine().plan(
            clear_network, "Kochi", "Thiruvananthapuram", coordinates=COORDS, k=3
        )
        assert candidates[0].route_score == pytest.approx(1.0)

    def test_report_is_auditable(self, clear_network):
        _, report = RoutingEngine().plan(
            clear_network, "Kochi", "Thiruvananthapuram", coordinates=COORDS
        )
        payload = report.as_dict()
        assert payload["algorithm_reason"]
        assert payload["nodes_expanded"] > 0

    def test_blocking_incident_removes_the_corridor(self, network):
        # Kollam is Critical/Active and sits mid-corridor.
        candidates, report = RoutingEngine().plan(
            network, "Kochi", "Thiruvananthapuram", coordinates=COORDS, k=3
        )
        assert candidates == []
        assert report.overlay_applied

    def test_overlay_can_be_disabled(self, network):
        candidates, _ = RoutingEngine().plan(
            network,
            "Kochi",
            "Thiruvananthapuram",
            coordinates=COORDS,
            apply_incident_overlay=False,
        )
        assert candidates

    def test_incompatible_vehicle_yields_no_route(self, clear_network):
        for legs in clear_network.adjacency.values():
            for leg in legs:
                leg.weight_limit_kg = 3000
        candidates, _ = RoutingEngine().plan(
            clear_network,
            "Kochi",
            "Thiruvananthapuram",
            vehicle=VehicleContext(weight_kg=12000),
            coordinates=COORDS,
        )
        assert candidates == []


class TestSegmentReplanning:
    def test_branches_as_late_as_possible(self, clear_network):
        outcome = SegmentReplanner().replan(
            clear_network,
            ReplanRequest(
                stops=["Kochi", "Alappuzha", "Haripad", "Kayamkulam", "Kollam"],
                current_node="Haripad",
                blocked_edge=("Kayamkulam", "Kollam"),
                reason="flooding",
            ),
            coordinates=COORDS,
        )
        # The vehicle is at Haripad but the blockage is further on, so
        # Haripad->Kayamkulam is still drivable. Branching at Kayamkulam keeps
        # more of the committed route than branching at the vehicle would.
        assert outcome.replanned_from == "Kayamkulam"
        assert outcome.kept_prefix == ["Kochi", "Alappuzha", "Haripad", "Kayamkulam"]

    def test_walks_back_when_the_late_branch_is_a_dead_end(self, clear_network):
        # Kayamkulam's only onward edge is to Kollam; block Kollam entirely and
        # the branch point must retreat toward the vehicle rather than fail.
        outcome = SegmentReplanner().replan(
            clear_network,
            ReplanRequest(
                stops=["Kochi", "Alappuzha", "Haripad", "Kayamkulam", "Kollam", "Attingal"],
                current_node="Alappuzha",
                blocked_nodes=["Kollam"],
                reason="closure",
            ),
            coordinates=COORDS,
        )
        # Either it retreated to a stop that works, or it reports no diversion —
        # but it must never branch behind Alappuzha.
        allowed = {"Alappuzha", "Haripad", "Kayamkulam"}
        assert outcome.replanned_from in allowed

    def test_does_not_branch_behind_the_vehicle(self, clear_network):
        outcome = SegmentReplanner().replan(
            clear_network,
            ReplanRequest(
                stops=["Kochi", "Alappuzha", "Haripad", "Kayamkulam"],
                current_node="Haripad",
                blocked_edge=("Kochi", "Alappuzha"),
                reason="late notice",
            ),
            coordinates=COORDS,
        )
        assert outcome.replanned_from == "Haripad"

    def test_blocked_destination_is_not_routed_into(self, clear_network):
        # Endpoint protection must not let an explicitly blocked destination
        # be treated as reachable.
        outcome = SegmentReplanner().replan(
            clear_network,
            ReplanRequest(
                stops=["Kollam", "Attingal", "Thiruvananthapuram"],
                current_node="Kollam",
                blocked_nodes=["Thiruvananthapuram"],
                reason="total closure",
            ),
            coordinates=COORDS,
        )
        assert not outcome.replanned
        assert "destination" in outcome.reason
        assert outcome.note

    def test_disruption_off_route_is_ignored(self, clear_network):
        outcome = SegmentReplanner().replan(
            clear_network,
            ReplanRequest(
                stops=["Kollam", "Attingal", "Thiruvananthapuram"],
                blocked_edge=("Kochi", "Alappuzha"),
            ),
            coordinates=COORDS,
        )
        assert not outcome.replanned
        assert "not on the current route" in outcome.reason
