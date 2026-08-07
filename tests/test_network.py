"""Road-network pathfinding: routes must come from the graph, never invented."""

from __future__ import annotations

from tests.conftest import build_network


class TestResolution:
    def test_exact_name(self, network):
        assert network.resolve("Kollam") == "Kollam"

    def test_case_insensitive(self, network):
        assert network.resolve("kollam") == "Kollam"

    def test_partial_prefix(self, network):
        assert network.resolve("thiruvanantha") == "Thiruvananthapuram"

    def test_unknown_returns_none(self, network):
        assert network.resolve("Bengaluru") is None

    def test_blank_returns_none(self, network):
        assert network.resolve("") is None


class TestPathfinding:
    def test_finds_direct_corridor(self, clear_network):
        paths = clear_network.plan("Kollam", "Thiruvananthapuram")
        assert paths, "expected at least one route"
        primary = paths[0]
        assert primary.stops[0] == "Kollam"
        assert primary.stops[-1] == "Thiruvananthapuram"

    def test_distance_sums_graph_edges(self, clear_network):
        paths = clear_network.plan("Kollam", "Thiruvananthapuram")
        via_attingal = next(p for p in paths if "Attingal" in p.stops)
        # 30 (Kollam-Attingal) + 32 (Attingal-TVM)
        assert via_attingal.total_distance_km == 62

    def test_long_corridor(self, clear_network):
        paths = clear_network.plan("Kochi", "Thiruvananthapuram")
        assert paths
        # 54 + 28 + 14 + 42 + 30 + 32
        assert paths[0].total_distance_km == 200

    def test_no_path_between_unconnected(self, clear_network):
        assert clear_network.plan("Kollam", "Nowhere") == []

    def test_paths_are_simple(self, clear_network):
        for path in clear_network.plan("Kochi", "Thiruvananthapuram"):
            assert len(path.stops) == len(set(path.stops)), "cycle in path"

    def test_legs_are_real_edges(self, clear_network):
        for path in clear_network.plan("Kochi", "Thiruvananthapuram"):
            for leg in path.legs:
                if leg.kind == "alternate":
                    continue
                assert clear_network.leg_exists(leg.from_location, leg.to_location)


class TestAlternates:
    def test_alternate_becomes_traversable_edge(self, clear_network):
        legs = clear_network.adjacency["Kollam"]
        assert any(leg.kind == "alternate" for leg in legs)

    def test_alternate_total_is_primary_plus_extra(self, clear_network):
        alternate = next(
            leg
            for leg in clear_network.adjacency["Kollam"]
            if leg.kind == "alternate" and leg.to_location == "Thiruvananthapuram"
        )
        # primary Kollam->TVM is 62 km, extra_distance is 15
        assert alternate.distance_km == 77

    def test_alternate_used_mid_route(self, clear_network):
        paths = clear_network.plan("Kochi", "Thiruvananthapuram", k=6)
        assert any(path.uses_alternate for path in paths), (
            "an alternate should be usable as a leg inside a longer corridor"
        )

    def test_alternate_skipped_without_primary_anchor(self):
        # extra_distance is relative; with no primary path there is nothing to
        # measure against, so the alternate must be dropped rather than guessed.
        network = build_network(attach_alternates=False)
        network.adjacency["Kollam"] = [
            leg for leg in network.adjacency["Kollam"] if leg.to_location != "Attingal"
        ]
        network.adjacency["Attingal"] = [
            leg for leg in network.adjacency["Attingal"] if leg.to_location != "Kollam"
        ]
        network.attach_alternates()
        alternates = [
            leg
            for leg in network.adjacency["Kollam"]
            if leg.kind == "alternate" and leg.to_location == "Thiruvananthapuram"
        ]
        assert alternates == []


class TestIncidentAnnotation:
    def test_intermediate_blocking_incident_marks_route(self, network):
        paths = network.plan("Kayamkulam", "Thiruvananthapuram")
        assert paths
        # Kollam carries a Critical incident and sits mid-route.
        assert all(path.blocking_incidents for path in paths)
        assert all(not path.is_clear for path in paths)

    def test_endpoint_incident_is_not_blocking(self, network):
        paths = network.plan("Kollam", "Thiruvananthapuram")
        assert paths
        primary = paths[0]
        # Kollam is the origin: disclosed, not disqualifying.
        assert primary.endpoint_incidents
        assert not primary.blocking_incidents
        assert primary.is_clear

    def test_advisory_incident_does_not_block(self, network):
        via_attingal = next(
            path
            for path in network.plan("Kollam", "Thiruvananthapuram")
            if "Attingal" in path.stops
        )
        assert via_attingal.advisory_incidents
        assert via_attingal.is_clear

    def test_inactive_incident_ignored(self, clear_network):
        paths = clear_network.plan("Kayamkulam", "Thiruvananthapuram")
        assert all(path.is_clear for path in paths)

    def test_clear_routes_rank_first(self, network):
        paths = network.plan("Kollam", "Thiruvananthapuram")
        clear = [index for index, path in enumerate(paths) if path.is_clear]
        blocked = [index for index, path in enumerate(paths) if not path.is_clear]
        if clear and blocked:
            assert max(clear) < min(blocked)


class TestLegVerification:
    def test_verifies_real_sequence(self, clear_network):
        ok, legs, missing = clear_network.verify_legs(
            ["Kollam", "Attingal", "Thiruvananthapuram"]
        )
        assert ok and not missing and len(legs) == 2

    def test_rejects_invented_road(self, clear_network):
        ok, _, missing = clear_network.verify_legs(["Kochi", "Thiruvananthapuram"])
        assert not ok
        assert missing == ["Kochi → Thiruvananthapuram"]
