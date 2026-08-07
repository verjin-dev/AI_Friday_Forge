"""Vehicle and consignment profiles, from ``missing_data_template.csv``.

This is the data that activates the constraint rules which previously reported
as *unverifiable*. The mapping below is deliberately conservative: a column is
only mapped onto a constraint input when the semantics genuinely match.

What the file gives us, and what it does not
--------------------------------------------
``COLD_CHAIN`` and ``HAZMAT_CERT`` describe **vehicle capability** — whether
this unit is a reefer, whether it is hazmat-certified. They do not say what the
cargo *requires*. Those two checks therefore still report as unverifiable until
consignment data exists; asserting "cold chain satisfied" from a reefer flag
alone would be a false pass.

``RTE_AXLE`` holds values 1–5, which is an axle **count**, not an axle load in
kilograms. It is stored but not fed to the axle-load rule, because comparing a
count against a road's weight limit would be meaningless.

``RTE_ZONE`` (Urban/Industrial/Rural/Hazard) uses a different vocabulary from
the ``zone`` on ``:Location`` (Core/North Corridor/…), so the two cannot be
joined yet. It is carried through for display and future use.

``DRV_LICENCE`` is the class the driver holds. The class the vehicle *requires*
is derived from its capacity using Indian licensing thresholds, which makes the
check meaningful rather than trivially self-satisfying.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import PROJECT_ROOT
from app.core.logging import get_logger
from app.domain.constraints import ConstraintProfile, RouteCandidate


logger = get_logger(__name__)


#: Indian licence thresholds by gross vehicle weight.
#: MCWG covers light two/three-wheeled goods carriers, LMV up to 7.5 t,
#: HMV above that; TRANS denotes a transport endorsement for the heaviest units.
LICENCE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (1000.0, "MCWG"),
    (7500.0, "LMV"),
    (16000.0, "HMV"),
    (float("inf"), "TRANS"),
)

#: Licence classes ordered by what they permit — a higher class covers lower.
LICENCE_RANK: dict[str, int] = {"MCWG": 0, "LMV": 1, "HMV": 2, "TRANS": 3}

#: SLA promise wording to a delivery horizon.
SLA_HORIZONS: dict[str, timedelta] = {
    "same day": timedelta(hours=0),  # end of the departure day
    "next day": timedelta(days=1),
    "48 hours": timedelta(hours=48),
    "72 hours": timedelta(hours=72),
}

#: Preferred transit bands to a ceiling in minutes.
DURATION_BANDS: dict[str, float] = {
    "short": 180.0,
    "medium": 360.0,
    "long": 720.0,
}


class VehicleProfile(BaseModel):
    """One row of the fleet template, parsed and typed."""

    profile_id: str
    capacity_kg: float | None = None
    capacity_m3: float | None = None
    max_daily_driving_hours: float | None = None
    min_break_minutes: float | None = None
    licence_held: str | None = None
    delivery_window: str | None = None
    sla_promise: str | None = None
    warehouse_cutoff: str | None = None
    refrigerated: bool | None = None
    hazmat_certified: bool | None = None
    height_m: float | None = None
    axle_count: int | None = None
    permitted_zone: str | None = None
    cost_preference: str | None = None
    duration_preference: str | None = None

    @property
    def required_licence(self) -> str | None:
        """Licence class this vehicle's capacity demands."""

        if self.capacity_kg is None:
            return None
        for threshold, licence in LICENCE_THRESHOLDS:
            if self.capacity_kg <= threshold:
                return licence
        return "TRANS"

    @property
    def licence_sufficient(self) -> bool | None:
        required = self.required_licence
        if required is None or not self.licence_held:
            return None
        held_rank = LICENCE_RANK.get(self.licence_held.upper())
        need_rank = LICENCE_RANK.get(required)
        if held_rank is None or need_rank is None:
            return None
        return held_rank >= need_rank

    def label(self) -> str:
        parts = [self.profile_id]
        if self.capacity_kg:
            parts.append(f"{self.capacity_kg:.0f} kg")
        if self.licence_held:
            parts.append(self.licence_held)
        if self.refrigerated:
            parts.append("reefer")
        if self.hazmat_certified:
            parts.append("hazmat")
        return " · ".join(parts)


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _to_bool(value: Any) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"yes", "true", "y", "1"}:
        return True
    if text in {"no", "false", "n", "0"}:
        return False
    return None


def parse_profiles(path: Path) -> list[VehicleProfile]:
    """Read the fleet template into typed profiles, ids assigned by row order."""

    if not path.exists():
        logger.info("No fleet template found", extra={"path": str(path)})
        return []

    profiles: list[VehicleProfile] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            clean = {
                (key or "").strip().upper(): (value or "").strip()
                for key, value in row.items()
                if key
            }
            profiles.append(
                VehicleProfile(
                    profile_id=f"P{index:03d}",
                    capacity_kg=_to_float(clean.get("CAP_WEIGHT")),
                    capacity_m3=_to_float(clean.get("CAP_VOLUME")),
                    max_daily_driving_hours=_to_float(clean.get("HOS_DAILY")),
                    min_break_minutes=_to_float(clean.get("HOS_BREAK")),
                    licence_held=clean.get("DRV_LICENCE") or None,
                    delivery_window=clean.get("TW_WINDOW") or None,
                    sla_promise=clean.get("SLA_PROMISE") or None,
                    warehouse_cutoff=clean.get("WH_CUTOFF") or None,
                    refrigerated=_to_bool(clean.get("COLD_CHAIN")),
                    hazmat_certified=_to_bool(clean.get("HAZMAT_CERT")),
                    height_m=_to_float(clean.get("RTE_HEIGHT")),
                    axle_count=_to_int(clean.get("RTE_AXLE")),
                    permitted_zone=clean.get("RTE_ZONE") or None,
                    cost_preference=clean.get("PREF_COST") or None,
                    duration_preference=clean.get("PREF_DURATION") or None,
                )
            )

    logger.info("Fleet profiles parsed", extra={"count": len(profiles)})
    return profiles


@lru_cache(maxsize=1)
def load_profiles() -> dict[str, VehicleProfile]:
    """Profiles keyed by id, searched under ``data/``."""

    for candidate in sorted(
        (PROJECT_ROOT / "data").rglob("missing_data_template.csv")
    ):
        profiles = parse_profiles(candidate)
        if profiles:
            return {profile.profile_id: profile for profile in profiles}
    return {}


def get_profile(profile_id: str | None) -> VehicleProfile | None:
    if not profile_id:
        return None
    return load_profiles().get(profile_id.strip().upper())


# ----------------------------------------------------------------------
# Applying a profile to a candidate
# ----------------------------------------------------------------------
def _window_times(window: str | None, day: datetime) -> tuple[str | None, str | None]:
    """Turn ``"08:00-12:00"`` into two ISO datetimes on the given day."""

    if not window or "-" not in window:
        return None, None
    start_text, _, end_text = window.partition("-")
    try:
        start = datetime.strptime(start_text.strip(), "%H:%M").time()
        end = datetime.strptime(end_text.strip(), "%H:%M").time()
    except ValueError:
        return None, None
    return (
        datetime.combine(day.date(), start).isoformat(timespec="minutes"),
        datetime.combine(day.date(), end).isoformat(timespec="minutes"),
    )


def _promise_time(promise: str | None, departure: datetime) -> str | None:
    if not promise:
        return None
    horizon = SLA_HORIZONS.get(promise.strip().lower())
    if horizon is None:
        return None
    if horizon == timedelta(0):
        # "Same Day" means by close of the departure day.
        return departure.replace(hour=23, minute=59, second=0, microsecond=0).isoformat(
            timespec="minutes"
        )
    return (departure + horizon).isoformat(timespec="minutes")


def _cutoff_time(cutoff: str | None, day: datetime) -> str | None:
    if not cutoff:
        return None
    try:
        parsed = datetime.strptime(cutoff.strip(), "%H:%M").time()
    except ValueError:
        return None
    return datetime.combine(day.date(), parsed).isoformat(timespec="minutes")


def apply_profile(
    candidate: RouteCandidate,
    profile: VehicleProfile,
    *,
    departure: datetime | None = None,
    payload_weight_kg: float | None = None,
    payload_volume_m3: float | None = None,
) -> RouteCandidate:
    """Populate a candidate's vehicle, driver and service fields from a profile.

    Payload is optional: without it the capacity rules stay unverifiable rather
    than passing on an assumed load.
    """

    when = departure or datetime.now()

    candidate.vehicle_id = profile.profile_id
    candidate.vehicle_capacity_kg = profile.capacity_kg
    candidate.vehicle_capacity_m3 = profile.capacity_m3
    candidate.vehicle_height_m = profile.height_m
    candidate.refrigerated = profile.refrigerated
    candidate.hazmat_certified = profile.hazmat_certified

    if payload_weight_kg is not None:
        candidate.payload_weight_kg = payload_weight_kg
    if payload_volume_m3 is not None:
        candidate.payload_volume_m3 = payload_volume_m3

    # Driver
    if profile.licence_held:
        candidate.driver_licence_classes = [profile.licence_held.upper()]
    candidate.required_licence_class = profile.required_licence
    candidate.rest_break_minutes = profile.min_break_minutes

    # Convert predicted transit into driving hours so the HOS rules can run.
    if candidate.duration_minutes is not None:
        candidate.driving_hours = round(candidate.duration_minutes / 60, 2)

    # Service commitments
    window_start, window_end = _window_times(profile.delivery_window, when)
    candidate.delivery_window_start = window_start
    candidate.delivery_window_end = window_end
    candidate.promised_delivery_by = _promise_time(profile.sla_promise, when)
    candidate.warehouse_cutoff = _cutoff_time(profile.warehouse_cutoff, when)
    candidate.departure_time = when.isoformat(timespec="minutes")

    if candidate.duration_minutes is not None:
        candidate.estimated_arrival = (
            when + timedelta(minutes=candidate.duration_minutes)
        ).isoformat(timespec="minutes")

    return candidate


def profile_constraint_overrides(profile: VehicleProfile) -> ConstraintProfile:
    """Per-vehicle limits, overriding the fleet-wide defaults."""

    from app.domain.constraints import get_constraint_profile

    base = get_constraint_profile()
    data = base.model_dump()

    if profile.max_daily_driving_hours:
        data["max_daily_driving_hours"] = profile.max_daily_driving_hours
    if profile.min_break_minutes:
        data["min_break_minutes_after_continuous"] = profile.min_break_minutes
    if profile.duration_preference:
        band = DURATION_BANDS.get(profile.duration_preference.strip().lower())
        if band:
            data["preferred_max_duration_minutes"] = band

    return ConstraintProfile(**data)


def profile_summary(profile: VehicleProfile) -> dict[str, Any]:
    """Display payload, including what this profile still cannot verify."""

    unverifiable: list[str] = []
    if profile.refrigerated is not None:
        unverifiable.append(
            "COLD_CHAIN — vehicle capability known, cargo requirement unknown"
        )
    if profile.hazmat_certified is not None:
        unverifiable.append(
            "HAZMAT_CERT — vehicle certification known, cargo class unknown"
        )
    unverifiable.append("CAP_WEIGHT / CAP_VOLUME — no consignment payload supplied")
    unverifiable.append("RTE_AXLE — file holds an axle count, not an axle load")
    unverifiable.append("RTE_ZONE — zone vocabulary does not join to Location.zone")

    return {
        **profile.model_dump(),
        "label": profile.label(),
        "required_licence": profile.required_licence,
        "licence_sufficient": profile.licence_sufficient,
        "unverifiable_without_more_data": unverifiable,
    }
