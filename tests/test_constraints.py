"""Hard constraints are feasibility gates — never traded off against speed."""

from __future__ import annotations

import pytest

from app.agents.optimization import _path_to_candidate, _verify_against_network
from app.domain.constraints import (
    ConstraintProfile,
    ConstraintSeverity,
    RouteCandidate,
    evaluate_candidate,
)


def check(report, code):
    return next((item for item in report.checks if item.code == code), None)


class TestNetworkConstraints:
    def test_invented_road_is_infeasible(self, clear_network):
        candidate = RouteCandidate(
            label="Direct expressway",
            stops=["Kochi", "Thiruvananthapuram"],
            distance_km=45,
        )
        _verify_against_network(candidate, clear_network)
        report = evaluate_candidate(candidate)

        assert not report.feasible
        assert check(report, "NET_LEGS").satisfied is False

    def test_real_route_passes_leg_check(self, clear_network):
        candidate = RouteCandidate(
            label="Via Attingal",
            stops=["Kollam", "Attingal", "Thiruvananthapuram"],
        )
        _verify_against_network(candidate, clear_network)
        report = evaluate_candidate(candidate)

        assert check(report, "NET_LEGS").satisfied is True

    def test_blocking_incident_disqualifies(self, network):
        path = network.plan("Kayamkulam", "Thiruvananthapuram")[0]
        report = evaluate_candidate(_path_to_candidate(path))

        assert not report.feasible
        assert check(report, "NET_INCIDENT").satisfied is False

    def test_endpoint_incident_is_soft(self, network):
        path = network.plan("Kollam", "Thiruvananthapuram")[0]
        report = evaluate_candidate(_path_to_candidate(path))

        endpoint = check(report, "NET_ENDPOINT")
        assert endpoint.satisfied is False
        assert endpoint.severity is ConstraintSeverity.SOFT
        assert report.feasible, "an unavoidable endpoint incident must not disqualify"

    def test_advisory_incident_is_soft(self, network):
        path = next(
            item
            for item in network.plan("Kollam", "Thiruvananthapuram")
            if "Attingal" in item.stops
        )
        report = evaluate_candidate(_path_to_candidate(path))

        advisory = check(report, "NET_ADVISORY")
        assert advisory.satisfied is False
        assert advisory.severity is ConstraintSeverity.SOFT
        assert report.feasible

    def test_fabricated_distance_is_caught(self, clear_network):
        candidate = RouteCandidate(
            label="Understated distance",
            stops=["Kollam", "Attingal", "Thiruvananthapuram"],
            distance_km=20,  # the graph says 62
        )
        _verify_against_network(candidate, clear_network)
        report = evaluate_candidate(candidate)

        assert not report.feasible
        assert check(report, "NET_DISTANCE").satisfied is False


class TestFleetConstraints:
    """Dormant until vehicle data arrives — but must behave correctly now."""

    def test_overweight_is_infeasible(self):
        report = evaluate_candidate(
            RouteCandidate(
                label="Overloaded",
                payload_weight_kg=12000,
                vehicle_capacity_kg=10000,
            )
        )
        assert not report.feasible
        assert check(report, "CAP_WEIGHT").satisfied is False

    def test_within_capacity_passes(self):
        report = evaluate_candidate(
            RouteCandidate(
                label="Loaded",
                payload_weight_kg=8000,
                vehicle_capacity_kg=10000,
            )
        )
        assert check(report, "CAP_WEIGHT").satisfied is True

    def test_driver_hours_exceeded(self):
        report = evaluate_candidate(
            RouteCandidate(
                label="Long shift",
                driving_hours=6.0,
                hours_already_driven_today=4.0,
            )
        )
        assert not report.feasible
        assert check(report, "HOS_DAILY").satisfied is False

    def test_missing_break_after_continuous_driving(self):
        report = evaluate_candidate(
            RouteCandidate(label="No break", driving_hours=6.0, rest_break_minutes=0)
        )
        assert check(report, "HOS_BREAK").satisfied is False

    def test_break_taken_satisfies_rule(self):
        report = evaluate_candidate(
            RouteCandidate(label="Break", driving_hours=6.0, rest_break_minutes=45)
        )
        assert check(report, "HOS_BREAK").satisfied is True

    def test_cold_chain_requires_reefer(self):
        report = evaluate_candidate(
            RouteCandidate(
                label="Chilled on a dry van",
                required_temperature_c=4.0,
                refrigerated=False,
            )
        )
        assert not report.feasible
        assert check(report, "COLD_CHAIN").satisfied is False

    def test_hazmat_requires_certified_vehicle(self):
        report = evaluate_candidate(
            RouteCandidate(
                label="Uncertified hazmat",
                hazmat_class="3",
                hazmat_certified=False,
            )
        )
        assert not report.feasible

    def test_sla_breach_is_infeasible(self):
        report = evaluate_candidate(
            RouteCandidate(
                label="Late",
                estimated_arrival="2026-08-07T18:00",
                promised_delivery_by="2026-08-07T17:00",
            )
        )
        assert not report.feasible
        assert check(report, "SLA_PROMISE").satisfied is False

    def test_delivery_window_respected(self):
        report = evaluate_candidate(
            RouteCandidate(
                label="In window",
                estimated_arrival="2026-08-07T14:00",
                delivery_window_start="2026-08-07T13:00",
                delivery_window_end="2026-08-07T16:00",
            )
        )
        assert check(report, "TW_WINDOW").satisfied is True


class TestUnverifiable:
    def test_missing_data_is_reported_not_passed(self):
        report = evaluate_candidate(RouteCandidate(label="Bare"))
        assert "CAP_WEIGHT" in report.unverifiable
        assert not any(item.code == "CAP_WEIGHT" for item in report.checks)

    def test_unverifiable_does_not_make_infeasible(self):
        assert evaluate_candidate(RouteCandidate(label="Bare")).feasible


class TestProfile:
    def test_custom_limit_is_honoured(self):
        strict = ConstraintProfile(max_daily_driving_hours=4.0)
        report = evaluate_candidate(
            RouteCandidate(label="Six hours", driving_hours=6.0), strict
        )
        assert not report.feasible

    @pytest.mark.parametrize("utilisation,expected", [(1.0, True), (0.8, False)])
    def test_utilisation_ceiling(self, utilisation, expected):
        profile = ConstraintProfile(max_vehicle_utilisation=utilisation)
        report = evaluate_candidate(
            RouteCandidate(
                label="90 percent",
                payload_weight_kg=9000,
                vehicle_capacity_kg=10000,
            ),
            profile,
        )
        assert check(report, "CAP_WEIGHT").satisfied is expected
