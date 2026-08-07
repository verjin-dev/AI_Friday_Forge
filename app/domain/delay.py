"""Delay prediction for a planned route.

Design note
-----------
There is no historical delay dataset in the knowledge graph — only current
incident state and road distances. A supervised model trained on nothing would
be a black box producing invented numbers, which the Validation Agent would
correctly reject as ungrounded.

So this is a **calibrated factor model**: every minute of predicted delay is
attributed to a named, evidence-backed factor the user can inspect and argue
with. Each factor carries its evidence source, and the confidence score falls
when factors are assumed rather than observed.

When historical actual-versus-planned data becomes available, replace
:func:`predict_delay` with a trained estimator and keep the same output shape —
the API, UI and Validation Agent all consume :class:`DelayPrediction`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.domain.network import Incident, RoutePath


logger = get_logger(__name__)


class DelayRisk(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    SEVERE = "severe"


#: Free-flow speeds by road class (km/h). Indian highway planning norms for
#: mixed traffic; override per corridor once telemetry is available.
ROAD_SPEEDS: dict[str, float] = {
    "NH": 55.0,
    "SH": 45.0,
    "MC ROAD": 45.0,
    "DEFAULT": 40.0,
}

#: Minutes of delay attributed to an active incident, by severity.
#: Intermediate stops take the full weight; endpoints take half, because part
#: of the disruption is absorbed before departure or after arrival.
INCIDENT_DELAY_MINUTES: dict[str, float] = {
    "critical": 45.0,
    "high": 25.0,
    "medium": 12.0,
    "low": 5.0,
}

#: Weather thresholds and their delay contribution.
RAIN_MM_THRESHOLD = 10.0
RAIN_DELAY_MINUTES = 15.0
HEAVY_RAIN_MM_THRESHOLD = 40.0
HEAVY_RAIN_DELAY_MINUTES = 30.0
WIND_KMH_THRESHOLD = 40.0
WIND_DELAY_MINUTES = 10.0

#: Peak-hour congestion multiplier applied to free-flow travel time.
PEAK_HOURS = {7, 8, 9, 10, 17, 18, 19, 20}
PEAK_MULTIPLIER = 0.15

#: Plausible average speeds for road freight. A live reading outside this band,
#: measured against the graph's own distance, means the provider routed a
#: different journey than the one being planned — usually an ambiguous place
#: name — so the reading is discarded rather than trusted.
MIN_PLAUSIBLE_KMH = 12.0
MAX_PLAUSIBLE_KMH = 110.0


class DelayFactor(BaseModel):
    """One named contribution to the predicted delay."""

    name: str
    minutes: float
    evidence: str
    #: True when drawn from graph/API data; False when assumed from a default.
    observed: bool = True

    def describe(self) -> str:
        mark = "" if self.observed else " (assumed)"
        return f"{self.name}: +{self.minutes:.0f} min — {self.evidence}{mark}"


class DelayPrediction(BaseModel):
    route_label: str = ""
    #: "live" when the baseline came from measured traffic, "graph" when it was
    #: estimated from road classes in Neo4j.
    baseline_source: str = "graph"
    live_traffic_used: bool = False
    distance_km: float = 0.0
    free_flow_minutes: float = 0.0
    predicted_delay_minutes: float = 0.0
    predicted_total_minutes: float = 0.0
    eta_iso: str | None = None
    risk: DelayRisk = DelayRisk.LOW
    confidence: float = 0.5
    factors: list[DelayFactor] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def delay_percentage(self) -> float:
        if self.free_flow_minutes <= 0:
            return 0.0
        return round(self.predicted_delay_minutes / self.free_flow_minutes * 100, 1)

    def describe(self) -> str:
        lines = [
            f"{self.route_label or 'Route'}: "
            f"{self.predicted_total_minutes:.0f} min total "
            f"({self.free_flow_minutes:.0f} free-flow "
            f"+ {self.predicted_delay_minutes:.0f} delay), "
            f"risk {self.risk.value}, confidence {self.confidence:.2f}"
        ]
        lines.extend(f"    {factor.describe()}" for factor in self.factors)
        return "\n".join(lines)


def _speed_for_road(road_name: str) -> tuple[float, str]:
    """Resolve a free-flow speed from the road name in the graph."""

    upper = (road_name or "").strip().upper()
    if upper.startswith("NH"):
        return ROAD_SPEEDS["NH"], "NH"
    if upper.startswith("SH"):
        return ROAD_SPEEDS["SH"], "SH"
    if "MC ROAD" in upper:
        return ROAD_SPEEDS["MC ROAD"], "MC ROAD"
    return ROAD_SPEEDS["DEFAULT"], "DEFAULT"


def free_flow_minutes(path: RoutePath) -> tuple[float, bool]:
    """Travel time with no disruption, from per-leg road class.

    Returns the estimate and whether every leg had a known road class.
    """

    total = 0.0
    all_known = True
    for leg in path.legs:
        speed, resolved = _speed_for_road(leg.road_name)
        if resolved == "DEFAULT":
            all_known = False
        total += (leg.distance_km / speed) * 60 if speed else 0.0

    if not path.legs and path.total_distance_km:
        # Alternate routes collapse to a single synthetic leg.
        total = (path.total_distance_km / ROAD_SPEEDS["DEFAULT"]) * 60
        all_known = False

    return round(total, 1), all_known


def _incident_factors(path: RoutePath) -> list[DelayFactor]:
    factors: list[DelayFactor] = []

    def add(incident: Incident, weight: float, position: str) -> None:
        base = INCIDENT_DELAY_MINUTES.get(incident.severity.strip().lower())
        if base is None:
            return
        factors.append(
            DelayFactor(
                name=f"{incident.severity} {incident.type} at {incident.location}",
                minutes=round(base * weight, 1),
                evidence=(
                    f"Incident {incident.incident_id} is {incident.status} at "
                    f"{incident.location} ({position} on this route)"
                ),
            )
        )

    for incident in path.blocking_incidents:
        add(incident, 1.0, "intermediate stop")
    for incident in path.advisory_incidents:
        add(incident, 1.0, "on route")
    for incident in path.endpoint_incidents:
        add(incident, 0.5, "origin/destination")

    return factors


def _weather_factors(weather: dict[str, Any] | None) -> list[DelayFactor]:
    """Translate an ``weather_lookup`` payload into delay contributions."""

    if not weather or not weather.get("found"):
        return []

    factors: list[DelayFactor] = []
    location = weather.get("location", "route")

    current = weather.get("current") or {}
    daily = weather.get("daily") or {}

    precipitation = current.get("precipitation")
    daily_precipitation = (daily.get("precipitation_sum") or [None])[0]
    rain = daily_precipitation if daily_precipitation is not None else precipitation

    if rain is not None:
        try:
            rain_value = float(rain)
        except (TypeError, ValueError):
            rain_value = 0.0
        if rain_value >= HEAVY_RAIN_MM_THRESHOLD:
            factors.append(
                DelayFactor(
                    name="Heavy rainfall",
                    minutes=HEAVY_RAIN_DELAY_MINUTES,
                    evidence=f"{rain_value:.0f} mm forecast at {location}",
                )
            )
        elif rain_value >= RAIN_MM_THRESHOLD:
            factors.append(
                DelayFactor(
                    name="Rainfall",
                    minutes=RAIN_DELAY_MINUTES,
                    evidence=f"{rain_value:.0f} mm forecast at {location}",
                )
            )

    wind = current.get("wind_speed_10m")
    daily_wind = (daily.get("wind_speed_10m_max") or [None])[0]
    wind_value = daily_wind if daily_wind is not None else wind
    if wind_value is not None:
        try:
            wind_kmh = float(wind_value)
        except (TypeError, ValueError):
            wind_kmh = 0.0
        if wind_kmh >= WIND_KMH_THRESHOLD:
            factors.append(
                DelayFactor(
                    name="High wind",
                    minutes=WIND_DELAY_MINUTES,
                    evidence=f"{wind_kmh:.0f} km/h at {location}",
                )
            )

    return factors


def _peak_factor(
    base_minutes: float, departure: datetime | None
) -> DelayFactor | None:
    if base_minutes <= 0:
        return None

    when = departure or datetime.now()
    if when.hour not in PEAK_HOURS:
        return None

    return DelayFactor(
        name="Peak-hour congestion",
        minutes=round(base_minutes * PEAK_MULTIPLIER, 1),
        evidence=(
            f"Departure at {when.strftime('%H:%M')} falls in the "
            f"{'morning' if when.hour < 12 else 'evening'} peak window"
        ),
        observed=departure is not None,
    )


def _classify(delay_minutes: float, base_minutes: float) -> DelayRisk:
    if base_minutes <= 0:
        return DelayRisk.LOW if delay_minutes < 15 else DelayRisk.HIGH

    ratio = delay_minutes / base_minutes
    if delay_minutes < 10 or ratio < 0.10:
        return DelayRisk.LOW
    if ratio < 0.25:
        return DelayRisk.MODERATE
    if ratio < 0.50:
        return DelayRisk.HIGH
    return DelayRisk.SEVERE


def predict_delay(
    path: RoutePath,
    *,
    weather: dict[str, Any] | None = None,
    departure: datetime | None = None,
    live: Any = None,
) -> DelayPrediction:
    """Predict delay and ETA for one route, with a full factor breakdown.

    When a live traffic reading is supplied (``live`` is a
    :class:`~app.domain.live_traffic.LiveTraffic`), the free-flow baseline and
    the congestion component come from measurement rather than assumption, and
    the peak-hour heuristic is dropped — live traffic already contains it.
    Incident delays from Neo4j are still added on top, because the graph knows
    about disruptions the traffic feed does not.
    """

    notes: list[str] = []
    live_route = getattr(live, "best", None) if live is not None else None
    live_available = bool(live_route) and getattr(live, "available", False)

    # Sanity-check the live reading before trusting it as the baseline. An
    # ambiguous place name can send the routing provider on a long detour, and
    # the resulting duration would otherwise silently become "free-flow".
    if live_available and path.total_distance_km > 0:
        baseline_minutes = (
            live_route.static_duration_minutes or live_route.duration_minutes
        )
        if baseline_minutes > 0:
            implied_kmh = path.total_distance_km / (baseline_minutes / 60)
            if not (MIN_PLAUSIBLE_KMH <= implied_kmh <= MAX_PLAUSIBLE_KMH):
                logger.warning(
                    "Rejecting implausible live traffic reading",
                    extra={
                        "route": path.label,
                        "implied_kmh": round(implied_kmh, 1),
                        "graph_km": path.total_distance_km,
                        "live_km": live_route.distance_km,
                    },
                )
                notes.append(
                    f"Live reading rejected: it implies {implied_kmh:.0f} km/h "
                    f"over the graph's {path.total_distance_km:.0f} km "
                    f"(provider routed {live_route.distance_km:.0f} km). "
                    "Falling back to the road-class estimate."
                )
                live_available = False

    if live_available:
        base = live_route.static_duration_minutes or live_route.duration_minutes
        roads_known = True
        source = "live"
    else:
        base, roads_known = free_flow_minutes(path)
        source = "graph"
        if live is not None and getattr(live, "error", None):
            notes.append(f"Live traffic unavailable ({live.error}); using road-class estimate.")

    factors: list[DelayFactor] = []

    if live_available:
        congestion = live_route.traffic_delay_minutes
        if congestion > 0:
            factors.append(
                DelayFactor(
                    name="Live traffic congestion",
                    minutes=congestion,
                    evidence=(
                        f"Google Routes API: {live_route.duration_minutes:.0f} min "
                        f"with traffic versus {live_route.static_duration_minutes:.0f} "
                        f"min free-flow ({live_route.congestion_ratio:.2f}x)"
                    ),
                )
            )
        if abs(live_route.distance_km - path.total_distance_km) > 5:
            notes.append(
                f"Live route is {live_route.distance_km:.0f} km against the "
                f"graph's {path.total_distance_km:.0f} km — the live provider "
                "may have taken a different corridor."
            )

    # Graph-known incidents apply regardless: the traffic feed does not model
    # a road-work closure the operator has recorded.
    factors.extend(_incident_factors(path))
    factors.extend(_weather_factors(weather))

    if not live_available:
        peak = _peak_factor(base, departure)
        if peak:
            factors.append(peak)

    delay = round(sum(factor.minutes for factor in factors), 1)
    total = round(base + delay, 1)

    if not roads_known:
        notes.append(
            f"Some legs had no recognised road class; "
            f"{ROAD_SPEEDS['DEFAULT']:.0f} km/h assumed for those."
        )
    if weather is None:
        notes.append("No weather data was retrieved; weather delay not modelled.")

    # Confidence reflects how much of the prediction rests on measurement.
    observed = sum(1 for factor in factors if factor.observed)
    confidence = 0.5
    if live_available:
        confidence += 0.3
    elif roads_known:
        confidence += 0.2
    if weather and weather.get("found"):
        confidence += 0.1
    if factors:
        confidence += 0.1 * (observed / len(factors))
    confidence = round(min(confidence, 0.95), 2)

    notes.append(
        "Baseline from live traffic measurement (Google Routes API); "
        "incidents from the Neo4j knowledge graph."
        if live_available
        else "Baseline estimated from graph road classes; no live traffic reading."
    )

    eta = None
    if departure is not None:
        from datetime import timedelta

        eta = (departure + timedelta(minutes=total)).isoformat(timespec="minutes")

    return DelayPrediction(
        route_label=path.label or " → ".join(path.stops),
        baseline_source=source,
        live_traffic_used=live_available,
        distance_km=path.total_distance_km,
        free_flow_minutes=base,
        predicted_delay_minutes=delay,
        predicted_total_minutes=total,
        eta_iso=eta,
        risk=_classify(delay, base),
        confidence=confidence,
        factors=factors,
        notes=notes,
    )


def predict_all(
    paths: list[RoutePath],
    *,
    weather: dict[str, Any] | None = None,
    departure: datetime | None = None,
    live_by_label: dict[str, Any] | None = None,
) -> list[DelayPrediction]:
    return [
        predict_delay(
            path,
            weather=weather,
            departure=departure,
            live=(live_by_label or {}).get(path.label),
        )
        for path in paths
    ]


async def predict_with_live_traffic(
    paths: list[RoutePath],
    *,
    weather: dict[str, Any] | None = None,
    departure: datetime | None = None,
    coordinates: dict[str, dict[str, float]] | None = None,
) -> list[DelayPrediction]:
    """Fetch live traffic for each route in parallel, then predict.

    A failed live call degrades that one route to the heuristic baseline; it
    never fails the request.
    """

    import asyncio

    from app.domain.live_traffic import fetch_live_traffic

    readings = await asyncio.gather(
        *(
            fetch_live_traffic(
                path.stops, departure=departure, coordinates=coordinates
            )
            for path in paths
        ),
        return_exceptions=True,
    )

    predictions: list[DelayPrediction] = []
    for path, reading in zip(paths, readings):
        live = None if isinstance(reading, BaseException) else reading
        if isinstance(reading, BaseException):
            logger.info(
                "Live traffic call failed",
                extra={"route": path.label, "error": str(reading)[:200]},
            )
        predictions.append(
            predict_delay(path, weather=weather, departure=departure, live=live)
        )
    return predictions


def describe_predictions(predictions: list[DelayPrediction]) -> str:
    if not predictions:
        return "(no delay predictions)"
    return "\n".join(prediction.describe() for prediction in predictions)


def model_card() -> dict[str, Any]:
    """Transparency record for the prediction model, surfaced in the UI."""

    return {
        "name": "LogiPilot hybrid delay model",
        "version": "2.0",
        "type": "measured live traffic + graph incident factors",
        "rationale": (
            "Congestion is measured, not guessed: the Google Routes API "
            "returns both traffic-aware and free-flow duration for the same "
            "geometry, and their difference is the live delay. Neo4j supplies "
            "the historical record — network topology, sanctioned alternates "
            "and known incidents — which the traffic feed does not model. "
            "Incident delay is added on top of the measured congestion. When "
            "the live call is unavailable the model degrades to a road-class "
            "estimate and says so."
        ),
        "data_sources": {
            "historical / authoritative": (
                "Neo4j — Location, Incident, CONNECTED_TO, ALTERNATE_ROUTE"
            ),
            "live": "Google Routes API v2 (TRAFFIC_AWARE)",
            "environmental": "open-meteo forecast (optional)",
        },
        "inputs": [
            "route legs and distance (CONNECTED_TO / ALTERNATE_ROUTE)",
            "live duration and staticDuration (Google Routes API)",
            "road class from road_name (fallback baseline)",
            "active incidents and severity (HAS_INCIDENT)",
            "weather forecast (open-meteo, optional)",
            "departure hour (fallback only)",
        ],
        "outputs": [
            "free_flow_minutes",
            "predicted_delay_minutes",
            "predicted_total_minutes",
            "risk",
            "confidence",
            "per-factor breakdown",
        ],
        "parameters": {
            "road_speeds_kmh": ROAD_SPEEDS,
            "incident_delay_minutes": INCIDENT_DELAY_MINUTES,
            "rain_mm_threshold": RAIN_MM_THRESHOLD,
            "heavy_rain_mm_threshold": HEAVY_RAIN_MM_THRESHOLD,
            "wind_kmh_threshold": WIND_KMH_THRESHOLD,
            "peak_hours": sorted(PEAK_HOURS),
            "peak_multiplier": PEAK_MULTIPLIER,
        },
        "limitations": [
            "Incident delay minutes are an operational judgement, not fitted "
            "to outcomes — no actual-versus-planned history exists yet.",
            "Endpoint incidents are weighted at half, also a judgement.",
            "Live traffic reflects conditions now; it is not a forecast for a "
            "departure hours ahead.",
            "The live provider may route down a different corridor than the "
            "graph; a large distance divergence is flagged in `notes`.",
        ],
        "upgrade_path": (
            "Capture actual arrival times to fit the incident coefficients, "
            "and keep the live traffic term as the measured baseline. The "
            "DelayPrediction output shape is the stable contract."
        ),
    }
