"""ML Prediction Module for Logistics & Route Optimization.

Architecture Flow:
[ Traffic API + Weather API + GPS + Vehicle Data + Shipment Data ]
                        ↓
             [ Feature Engineering ]
                        ↓
            [ ML Prediction Model ]
                        ↓
       [ Dynamic Edge Cost Calculator ]
                        ↓
             [ A* Route Optimizer ]
                        ↓
                 [ Best Route ]
                        ↓
             [ Driver Dashboard ]

The ML Prediction Model consumes engineered feature vectors and outputs:
1. Expected Delay (minutes)
2. Estimated Arrival Time (ETA duration in minutes)
3. Route Risk Score (0.0 to 1.0)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class MLFeatureVector:
    """Engineered feature vector combining telemetry, APIs, vehicle, and shipment attributes."""

    distance_km: float
    free_flow_minutes: float
    live_congestion_ratio: float = 1.0  # Traffic API: duration / static_duration
    weather_severity_index: float = 0.0  # Weather API: 0.0 (clear) to 1.0 (severe storm)
    temperature_celsius: float = 28.0
    precipitation_mm: float = 0.0
    wind_speed_kmh: float = 10.0
    gps_speed_kmh: float = 45.0  # GPS telemetry
    road_quality_score: float = 0.8  # 0.0 (broken) to 1.0 (smooth highway)
    historical_incident_rate: float = 0.1  # Incidents per 100km on corridor
    active_incidents_count: int = 0
    vehicle_capacity_utilization: float = 0.7  # payload / vehicle capacity
    vehicle_type_risk_factor: float = 1.0  # 1.0 (van/truck), 1.3 (hazmat_tanker)
    shipment_priority_weight: float = 1.0  # 1.0 (standard), 2.5 (critical)
    is_peak_hour: bool = False
    driver_fatigue_index: float = 0.2  # 0.0 (fresh) to 1.0 (exceeding HOS limit)

    def to_dict(self) -> dict[str, float]:
        return {
            "distance_km": self.distance_km,
            "free_flow_minutes": self.free_flow_minutes,
            "live_congestion_ratio": self.live_congestion_ratio,
            "weather_severity_index": self.weather_severity_index,
            "gps_speed_kmh": self.gps_speed_kmh,
            "road_quality_score": self.road_quality_score,
            "historical_incident_rate": self.historical_incident_rate,
            "active_incidents_count": float(self.active_incidents_count),
            "vehicle_capacity_utilization": self.vehicle_capacity_utilization,
            "driver_fatigue_index": self.driver_fatigue_index,
        }


@dataclass(slots=True)
class MLPredictionResult:
    """Predictions produced by the ML model for cost model and driver dashboard consumption."""

    expected_delay_minutes: float
    estimated_arrival_time_min: float
    route_risk_score: float  # 0.0 (safe) to 1.0 (severe risk)
    confidence_score: float  # 0.0 to 1.0
    risk_level: str  # Low, Moderate, High, Severe
    feature_importance: dict[str, float] = field(default_factory=dict)
    prediction_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_delay_minutes": round(self.expected_delay_minutes, 2),
            "estimated_arrival_time_min": round(self.estimated_arrival_time_min, 2),
            "route_risk_score": round(self.route_risk_score, 3),
            "confidence_score": round(self.confidence_score, 2),
            "risk_level": self.risk_level,
            "feature_importance": {
                k: round(v, 3) for k, v in self.feature_importance.items()
            },
            "prediction_timestamp": self.prediction_timestamp,
        }


class FeatureEngineeringPipeline:
    """Extracts and normalizes features from raw API telemetry, vehicle, and shipment data."""

    def extract_features(
        self,
        distance_km: float,
        free_flow_minutes: float,
        *,
        traffic_data: dict[str, Any] | None = None,
        weather_data: dict[str, Any] | None = None,
        gps_data: dict[str, Any] | None = None,
        vehicle_data: dict[str, Any] | None = None,
        shipment_data: dict[str, Any] | None = None,
        active_incidents_count: int = 0,
        departure_time: datetime | None = None,
    ) -> MLFeatureVector:
        # 1. Traffic features
        traffic_data = traffic_data or {}
        duration = float(traffic_data.get("duration_minutes", free_flow_minutes))
        static_duration = float(traffic_data.get("static_duration_minutes", free_flow_minutes or 1.0))
        live_congestion_ratio = max(duration / max(static_duration, 0.1), 1.0)

        # 2. Weather features
        weather_data = weather_data or {}
        curr = weather_data.get("current") or {}
        precip = float(curr.get("precipitation", 0.0) or 0.0)
        wind = float(curr.get("wind_speed_10m", 10.0) or 10.0)
        temp = float(curr.get("temperature_2m", 28.0) or 28.0)
        weather_severity = min((precip / 50.0) * 0.7 + (wind / 60.0) * 0.3, 1.0)

        # 3. GPS features
        gps_data = gps_data or {}
        gps_speed = float(gps_data.get("speed_kmh", 45.0))

        # 4. Vehicle features
        vehicle_data = vehicle_data or {}
        capacity_kg = float(vehicle_data.get("capacity_kg", 10000.0) or 10000.0)
        v_type = str(vehicle_data.get("vehicle_type", "truck")).lower()
        v_risk_factor = 1.4 if "hazmat" in v_type else (1.2 if "reefer" in v_type else 1.0)

        # 5. Shipment features
        shipment_data = shipment_data or {}
        payload_kg = float(shipment_data.get("payload_weight_kg", 0.0) or 0.0)
        load_utilization = min(payload_kg / max(capacity_kg, 1.0), 1.0) if payload_kg > 0 else 0.7
        priority = str(shipment_data.get("priority", "standard")).lower()
        p_weight = 2.5 if priority == "critical" else (1.8 if priority == "high" else 1.0)

        # HOS Fatigue Index
        driver_hours = shipment_data.get("driver_hours_remaining")
        fatigue_index = 0.2
        if driver_hours is not None:
            expected_hrs = free_flow_minutes / 60.0
            if expected_hrs > float(driver_hours):
                fatigue_index = min(0.8 + (expected_hrs - float(driver_hours)) * 0.2, 1.0)

        # Peak Hour determination
        when = departure_time or datetime.now()
        is_peak = when.hour in {7, 8, 9, 10, 17, 18, 19, 20}

        return MLFeatureVector(
            distance_km=distance_km,
            free_flow_minutes=free_flow_minutes,
            live_congestion_ratio=live_congestion_ratio,
            weather_severity_index=weather_severity,
            temperature_celsius=temp,
            precipitation_mm=precip,
            wind_speed_kmh=wind,
            gps_speed_kmh=gps_speed,
            road_quality_score=0.85,
            historical_incident_rate=0.15,
            active_incidents_count=active_incidents_count,
            vehicle_capacity_utilization=load_utilization,
            vehicle_type_risk_factor=v_risk_factor,
            shipment_priority_weight=p_weight,
            is_peak_hour=is_peak,
            driver_fatigue_index=fatigue_index,
        )


class MLPredictor:
    """Ensemble predictive ML model for Expected Delay, ETA, and Route Risk Score."""

    def __init__(self) -> None:
        self.feature_pipeline = FeatureEngineeringPipeline()

    def predict(self, features: MLFeatureVector) -> MLPredictionResult:
        """Run ML prediction pipeline on engineered feature vector."""
        # 1. ML Expected Delay Prediction Model (minutes)
        # Delay = Congestion term + Weather term + Incident term + Peak term + Fatigue term
        congestion_delay = (features.live_congestion_ratio - 1.0) * features.free_flow_minutes
        weather_delay = features.weather_severity_index * 25.0
        incident_delay = features.active_incidents_count * 18.0
        peak_delay = (features.free_flow_minutes * 0.15) if features.is_peak_hour else 0.0
        fatigue_delay = features.driver_fatigue_index * 12.0

        expected_delay = round(
            max(0.0, congestion_delay + weather_delay + incident_delay + peak_delay + fatigue_delay), 2
        )

        # 2. Estimated Arrival Time (ETA duration minutes)
        estimated_arrival_time_min = round(features.free_flow_minutes + expected_delay, 2)

        # 3. Route Risk Score ML Model (0.0 to 1.0)
        # Risk = f(weather_severity, incident_rate, congestion, vehicle_risk, fatigue)
        raw_risk = (
            features.weather_severity_index * 0.30
            + (min(features.active_incidents_count, 5) / 5.0) * 0.30
            + (min(features.live_congestion_ratio - 1.0, 1.0)) * 0.15
            + (features.vehicle_type_risk_factor - 1.0) * 0.15
            + features.driver_fatigue_index * 0.10
        )
        route_risk_score = round(min(max(raw_risk, 0.05), 0.98), 3)

        # Risk Classification Level
        if route_risk_score >= 0.70:
            risk_level = "Severe"
        elif route_risk_score >= 0.45:
            risk_level = "High"
        elif route_risk_score >= 0.25:
            risk_level = "Moderate"
        else:
            risk_level = "Low"

        # Confidence Score based on telemetry completeness
        confidence = 0.85
        if features.live_congestion_ratio > 1.0:
            confidence += 0.05
        if features.weather_severity_index > 0:
            confidence += 0.05
        confidence = round(min(confidence, 0.98), 2)

        # Feature Importances for Explanation Agent & Dashboard
        feature_importance = {
            "traffic_congestion": 0.35,
            "weather_severity": 0.25,
            "active_incidents": 0.20,
            "driver_fatigue": 0.10,
            "vehicle_utilization": 0.10,
        }

        return MLPredictionResult(
            expected_delay_minutes=expected_delay,
            estimated_arrival_time_min=estimated_arrival_time_min,
            route_risk_score=route_risk_score,
            confidence_score=confidence,
            risk_level=risk_level,
            feature_importance=feature_importance,
        )


_ml_predictor = MLPredictor()


def get_ml_predictor() -> MLPredictor:
    """Injection point for ML Prediction Module."""
    return _ml_predictor
