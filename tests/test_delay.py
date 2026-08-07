"""Delay prediction: measured live traffic preferred, incidents added on top."""

from __future__ import annotations

from datetime import datetime

from app.domain.delay import (
    DelayRisk,
    free_flow_minutes,
    model_card,
    predict_delay,
)
from app.domain.live_traffic import LiveRoute, LiveTraffic


#: A departure well outside the morning and evening peak windows, so tests do
#: not change behaviour depending on when they run.
OFF_PEAK = datetime(2026, 8, 7, 13, 0)


def live(duration: float, static: float, distance: float = 62.0) -> LiveTraffic:
    return LiveTraffic(
        available=True,
        routes=[
            LiveRoute(
                description="live",
                distance_km=distance,
                duration_minutes=duration,
                static_duration_minutes=static,
            )
        ],
    )


class TestFreeFlow:
    def test_uses_road_class_speed(self, clear_network):
        path = next(
            item
            for item in clear_network.plan("Kollam", "Thiruvananthapuram")
            if "Attingal" in item.stops
        )
        minutes, known = free_flow_minutes(path)
        # 62 km of NH66 at 55 km/h
        assert known is True
        assert 66 < minutes < 69

    def test_unknown_road_class_is_flagged(self, clear_network):
        path = next(
            item
            for item in clear_network.plan("Kollam", "Thiruvananthapuram")
            if item.uses_alternate
        )
        _, known = free_flow_minutes(path)
        assert known is False


class TestIncidentFactors:
    def test_advisory_incident_adds_delay(self, network):
        path = next(
            item
            for item in network.plan("Kollam", "Thiruvananthapuram")
            if "Attingal" in item.stops
        )
        prediction = predict_delay(path)
        names = " ".join(factor.name for factor in prediction.factors)
        assert "Road Work" in names
        assert prediction.predicted_delay_minutes > 0

    def test_endpoint_incident_weighted_at_half(self, network):
        path = network.plan("Kollam", "Thiruvananthapuram")[0]
        prediction = predict_delay(path)
        critical = next(
            factor for factor in prediction.factors if "Heavy Rain" in factor.name
        )
        assert critical.minutes == 22.5  # half of the 45 min Critical weight

    def test_clear_route_has_no_incident_delay(self, clear_network):
        # An explicit off-peak departure keeps this deterministic: without one
        # the model falls back to datetime.now(), and the run would fail
        # whenever the suite happens to execute during a peak-hour window.
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        prediction = predict_delay(path, departure=OFF_PEAK)
        assert prediction.predicted_delay_minutes == 0
        assert prediction.risk is DelayRisk.LOW


class TestLiveTraffic:
    def test_live_baseline_preferred(self, clear_network):
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        prediction = predict_delay(path, live=live(90, 80))

        assert prediction.live_traffic_used is True
        assert prediction.baseline_source == "live"
        assert prediction.free_flow_minutes == 80

    def test_congestion_is_the_measured_difference(self, clear_network):
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        prediction = predict_delay(path, live=live(95, 80))

        congestion = next(
            factor
            for factor in prediction.factors
            if factor.name == "Live traffic congestion"
        )
        assert congestion.minutes == 15

    def test_negative_congestion_clamped(self, clear_network):
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        prediction = predict_delay(path, live=live(70, 80))
        assert prediction.predicted_delay_minutes >= 0

    def test_incidents_still_apply_with_live_data(self, network):
        path = network.plan("Kollam", "Thiruvananthapuram")[0]
        prediction = predict_delay(path, live=live(90, 80))
        names = " ".join(factor.name for factor in prediction.factors)
        assert "Heavy Rain" in names, "graph incidents must survive live traffic"

    def test_corridor_divergence_is_flagged(self, clear_network):
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        prediction = predict_delay(path, live=live(90, 80, distance=20))
        assert any("different corridor" in note for note in prediction.notes)

    def test_failure_degrades_and_says_so(self, clear_network):
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        unavailable = LiveTraffic(available=False, error="quota exceeded")
        prediction = predict_delay(path, live=unavailable)

        assert prediction.live_traffic_used is False
        assert prediction.baseline_source == "graph"
        assert any("quota exceeded" in note for note in prediction.notes)

    def test_live_raises_confidence(self, clear_network):
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        with_live = predict_delay(path, live=live(90, 80), departure=OFF_PEAK).confidence
        without = predict_delay(path, departure=OFF_PEAK).confidence
        assert with_live > without


class TestWeather:
    def test_rain_adds_delay(self, clear_network):
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        weather = {
            "found": True,
            "location": "Thiruvananthapuram",
            "current": {},
            "daily": {"precipitation_sum": [25.0]},
        }
        prediction = predict_delay(path, weather=weather)
        assert any("Rainfall" in factor.name for factor in prediction.factors)

    def test_light_rain_below_threshold_ignored(self, clear_network):
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        weather = {
            "found": True,
            "location": "TVM",
            "current": {},
            "daily": {"precipitation_sum": [2.0]},
        }
        prediction = predict_delay(path, weather=weather)
        assert not any("Rain" in factor.name for factor in prediction.factors)


class TestRisk:
    def test_severe_when_delay_dominates(self, clear_network):
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        prediction = predict_delay(path, live=live(200, 60))
        assert prediction.risk is DelayRisk.SEVERE

    def test_peak_hour_only_without_live_data(self, clear_network):
        path = clear_network.plan("Kollam", "Thiruvananthapuram")[0]
        peak = datetime(2026, 8, 7, 18, 0)

        without_live = predict_delay(path, departure=peak)
        with_live = predict_delay(path, departure=peak, live=live(90, 80))

        assert any("Peak-hour" in f.name for f in without_live.factors)
        assert not any("Peak-hour" in f.name for f in with_live.factors)


class TestModelCard:
    def test_declares_sources_and_limits(self):
        card = model_card()
        assert "data_sources" in card
        assert card["limitations"], "a model card without limitations is marketing"
