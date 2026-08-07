import pytest
import time
from app.core.config import settings
from app.routing.engine import RouteCandidate
from app.routing.monitor import (
    RouteMonitor,
    MonitoringEventType,
    MonitoringEvent,
    MonitoredRoute
)


@pytest.fixture
def mock_route_candidate() -> RouteCandidate:
    return RouteCandidate(
        rank=1,
        total_distance_km=100.0,
        estimated_travel_minutes=60.0,
        stops=["A", "B"],
        legs=[],
    )


@pytest.fixture
def monitor() -> RouteMonitor:
    # Always create a new instance for tests so state is isolated
    return RouteMonitor()


def test_register_and_deregister(monitor, mock_route_candidate, monkeypatch):
    monkeypatch.setattr(settings, "enable_route_monitoring", True)

    assert monitor.active_count == 0

    route_id = monitor.register(mock_route_candidate, original_eta=60.0)
    assert route_id is not None
    assert monitor.active_count == 1

    status = monitor.status()
    assert route_id in status["routes"]

    ok = monitor.deregister(route_id)
    assert ok is True
    assert monitor.active_count == 0


def test_register_enforces_max(monitor, mock_route_candidate, monkeypatch):
    monkeypatch.setattr(settings, "enable_route_monitoring", True)
    monkeypatch.setattr(settings, "monitor_max_active_routes", 2)

    monitor.register(mock_route_candidate, original_eta=60.0)
    monitor.register(mock_route_candidate, original_eta=60.0)

    with pytest.raises(RuntimeError, match="Max active routes"):
        monitor.register(mock_route_candidate, original_eta=60.0)


@pytest.mark.asyncio
async def test_poll_returns_ok_when_no_delay(monitor, mock_route_candidate, monkeypatch):
    monkeypatch.setattr(settings, "enable_route_monitoring", True)

    route_id = monitor.register(mock_route_candidate, original_eta=60.0)
    
    event = await monitor.poll(route_id)
    assert event.event_type == MonitoringEventType.ok


@pytest.mark.asyncio
async def test_poll_returns_replan_when_threshold_exceeded(monitor, mock_route_candidate, monkeypatch):
    monkeypatch.setattr(settings, "enable_route_monitoring", True)
    monkeypatch.setattr(settings, "monitor_replan_threshold_minutes", 15.0)

    route_id = monitor.register(mock_route_candidate, original_eta=60.0)

    # Manually increase current ETA to exceed threshold (15.0)
    monitor._routes[route_id].current_eta_minutes = 76.0

    event = await monitor.poll(route_id)
    
    assert event.event_type == MonitoringEventType.replan_triggered
    assert event.delay_delta_minutes == 16.0


@pytest.mark.asyncio
async def test_poll_nonexistent_route(monitor):
    with pytest.raises(KeyError):
        await monitor.poll("fake-id")


def test_status_returns_overview(monitor, mock_route_candidate, monkeypatch):
    monkeypatch.setattr(settings, "enable_route_monitoring", True)

    route_id1 = monitor.register(mock_route_candidate, original_eta=60.0)
    route_id2 = monitor.register(mock_route_candidate, original_eta=45.0)

    status = monitor.status()
    assert status["active_count"] == 2
    assert route_id1 in status["routes"]
    assert route_id2 in status["routes"]
    assert status["routes"][route_id1]["original_eta"] == 60.0
    assert status["routes"][route_id2]["original_eta"] == 45.0


def test_monitoring_event_model():
    evt = MonitoringEvent(
        event_type=MonitoringEventType.ok,
        timestamp="2023-01-01T12:00:00Z",
        message="All good",
        delay_delta_minutes=0.0
    )
    assert evt.event_type == "ok"
    assert evt.message == "All good"
    assert evt.details == {}


def test_feature_flag_disabled(monitor, mock_route_candidate, monkeypatch):
    monkeypatch.setattr(settings, "enable_route_monitoring", False)

    with pytest.raises(ValueError, match="Route monitoring is disabled"):
        monitor.register(mock_route_candidate, original_eta=60.0)
