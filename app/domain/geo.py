"""Coordinates for road-network locations, for the map UI.

The delivered ``location_nodes.csv`` carries no latitude/longitude, so
coordinates are resolved in three tiers, most trustworthy first:

1. ``latitude`` / ``longitude`` properties on the ``:Location`` node, if the
   graph has been enriched (see ``scripts/geocode_locations.py``);
2. a static table of the Kerala corridor towns, so the demo works with no
   network access;
3. live geocoding, cached to ``data/geocache.json``.

A location that resolves to nothing is returned without coordinates rather than
placed at a guessed point — a wrong pin on a map is worse than a missing one.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import PROJECT_ROOT, settings
from app.core.logging import get_logger


logger = get_logger(__name__)

_CACHE_PATH = PROJECT_ROOT / "data" / "geocache.json"

#: Approximate town centroids for every location in the delivered dataset.
#:
#: These are supplied here rather than geocoded because several names are
#: ambiguous within India — "Paravur" resolves to North Paravur in Ernakulam
#: (165 km from the Kollam town the data means), "Pathanapuram" and "Mannar"
#: are similarly ambiguous. A wrong pin does not merely misdraw the map: it
#: sends the live routing provider down a different corridor, and that journey
#: time then becomes the delay model's baseline. Explicit beats guessed.
#:
#: Accuracy is town-centroid level (within a few km), which is the right
#: resolution for corridor routing. Replace with surveyed depot coordinates if
#: the operation needs door-level precision.
STATIC_COORDINATES: dict[str, tuple[float, float]] = {
    # --- Thiruvananthapuram district ---
    "thiruvananthapuram": (8.5241, 76.9366),
    "kazhakkoottam": (8.5674, 76.8700),
    "sreekaryam": (8.5510, 76.9080),
    "pattom": (8.5180, 76.9450),
    "palayam": (8.5010, 76.9490),
    "east fort": (8.4855, 76.9450),
    "neyyattinkara": (8.4000, 77.0870),
    "balaramapuram": (8.4230, 77.0330),
    "parassala": (8.3390, 77.1470),
    "attingal": (8.6957, 76.8155),
    "varkala": (8.7379, 76.7163),
    "kilimanoor": (8.7770, 76.8760),
    "kallambalam": (8.7780, 76.7690),
    "venjaramoodu": (8.6870, 76.9060),
    "nedumangad": (8.6030, 77.0000),
    "chirayinkeezhu": (8.6690, 76.7830),
    # --- Kollam district ---
    "kollam": (8.8932, 76.6141),
    "paravur": (8.8130, 76.6690),
    "chathannoor": (8.8490, 76.7100),
    "kottiyam": (8.8760, 76.6580),
    "kundara": (8.9330, 76.6830),
    "kottarakkara": (9.0000, 76.7833),
    "karunagappally": (9.0540, 76.5340),
    "ochira": (9.1360, 76.5060),
    "sasthamkotta": (9.0430, 76.6270),
    "anchal": (8.9060, 76.9200),
    "punalur": (9.0117, 76.9285),
    "pathanapuram": (9.0930, 76.8560),
    # --- Alappuzha district ---
    "kayamkulam": (9.1800, 76.5010),
    "haripad": (9.2833, 76.4667),
    "ambalappuzha": (9.3800, 76.3500),
    "alappuzha": (9.4981, 76.3388),
    "cherthala": (9.6840, 76.3360),
    "mavelikara": (9.2540, 76.5490),
    "chengannur": (9.3150, 76.6150),
    "aroor": (9.8690, 76.3050),
    "karthikappally": (9.2270, 76.4340),
    "thakazhy": (9.3810, 76.4210),
    "mannar": (9.3220, 76.5330),
    # --- Pathanamthitta district ---
    #: Adoor is on the MC Road corridor and appears in delivered routes, but had
    #: no entry here, so it resolved to no coordinates at all: no map pin, and no
    #: geographic lower bound for A* through one of the busiest junctions.
    "adoor": (9.1594, 76.7314),
    "pathanamthitta": (9.2648, 76.7870),
    # --- Kottayam district ---
    "changanassery": (9.4450, 76.5400),
    "kottayam": (9.5916, 76.5222),
    "ettumanoor": (9.6700, 76.5580),
    "pala": (9.7140, 76.6830),
    "vaikom": (9.7480, 76.3960),
    "kuravilangad": (9.7530, 76.5580),
    "kanjirappally": (9.5600, 76.7900),
    "mundakayam": (9.5400, 76.8900),
    "kumarakom": (9.6180, 76.4300),
    "pampady": (9.5100, 76.6200),
    "thalayolaparambu": (9.8000, 76.4700),
    "kaduthuruthy": (9.7700, 76.4900),
    "erattupetta": (9.6900, 76.7800),
    "vechoor": (9.6700, 76.4100),
    "kalathipady": (9.5800, 76.5300),
    "aymanam": (9.5900, 76.4800),
    # --- referenced by alternate routes but not Location nodes ---
    "kochi": (9.9312, 76.2673),
    "mc road": (9.0500, 76.7000),
}

#: Kerala's bounding box. Several town names in the dataset are ambiguous
#: nationally — "Paravur" exists both near Kollam and near Kochi, "Mannar" is
#: also a town in Sri Lanka. A geocode landing outside this box is the wrong
#: place, and a wrong pin sends the live routing provider on a 100 km detour
#: that then poisons the delay prediction. So results outside are rejected.
KERALA_BOUNDS = {
    "min_lat": 8.0,
    "max_lat": 13.0,
    "min_lon": 74.5,
    "max_lon": 77.6,
}

_cache: dict[str, list[float]] | None = None


def within_bounds(latitude: float, longitude: float) -> bool:
    return (
        KERALA_BOUNDS["min_lat"] <= latitude <= KERALA_BOUNDS["max_lat"]
        and KERALA_BOUNDS["min_lon"] <= longitude <= KERALA_BOUNDS["max_lon"]
    )


def _load_cache() -> dict[str, list[float]]:
    global _cache
    if _cache is not None:
        return _cache
    if _CACHE_PATH.exists():
        try:
            _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save_cache() -> None:
    if _cache is None:
        return
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write geocache", extra={"error": str(exc)[:200]})


def static_lookup(name: str) -> tuple[float, float] | None:
    return STATIC_COORDINATES.get((name or "").strip().lower())


def cached_lookup(name: str) -> tuple[float, float] | None:
    entry = _load_cache().get((name or "").strip().lower())
    if entry and len(entry) == 2:
        return float(entry[0]), float(entry[1])
    return None


async def geocode(
    name: str, *, district: str | None = None
) -> tuple[float, float] | None:
    """Resolve a place name to coordinates within Kerala, caching the result.

    Several dataset names are ambiguous nationally, so candidates outside the
    Kerala bounding box are discarded rather than accepted as a best guess.
    """

    key = (name or "").strip().lower()
    if not key:
        return None

    for lookup in (static_lookup, cached_lookup):
        found = lookup(name)
        if found:
            return found

    # Ask for several candidates so an out-of-region first hit can be skipped.
    query = f"{name}, {district}" if district else name
    try:
        async with httpx.AsyncClient(
            timeout=settings.external_api_timeout_seconds,
            verify=settings.external_verify_ssl,
        ) as client:
            response = await client.get(
                settings.geocoding_api_url,
                params={
                    "name": query,
                    "count": 10,
                    "format": "json",
                    "countryCode": "IN",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 - offline demos must still work
        logger.info(
            "Geocoding unavailable",
            extra={"location": name, "error": str(exc)[:160]},
        )
        return None

    results = payload.get("results") or []
    if not results and district:
        # Retry without the district qualifier, which some gazetteers reject.
        return await geocode(name)

    for candidate in results:
        try:
            latitude = float(candidate["latitude"])
            longitude = float(candidate["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not within_bounds(latitude, longitude):
            continue
        # Prefer a candidate whose admin area matches the known district.
        if district and district.lower() not in str(
            candidate.get("admin2", "") or candidate.get("admin1", "")
        ).lower():
            # Not disqualifying — Kerala districts and gazetteer admin names
            # do not always agree — but keep looking for a better match first.
            continue
        coordinates = (latitude, longitude)
        cache = _load_cache()
        cache[key] = list(coordinates)
        _save_cache()
        return coordinates

    # No district match: accept the first in-region candidate.
    for candidate in results:
        try:
            latitude = float(candidate["latitude"])
            longitude = float(candidate["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if within_bounds(latitude, longitude):
            coordinates = (latitude, longitude)
            cache = _load_cache()
            cache[key] = list(coordinates)
            _save_cache()
            return coordinates

    logger.warning(
        "No in-region geocode candidate",
        extra={"location": name, "district": district, "candidates": len(results)},
    )
    return None


def coordinates_from_properties(properties: dict[str, Any]) -> tuple[float, float] | None:
    """Read coordinates already stored on a :Location node."""

    latitude = properties.get("latitude") or properties.get("lat")
    longitude = properties.get("longitude") or properties.get("lon") or properties.get(
        "lng"
    )
    if latitude is None or longitude is None:
        return None
    try:
        return float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None


async def resolve_all(
    names: list[str], properties: dict[str, dict[str, Any]] | None = None
) -> dict[str, dict[str, float]]:
    """Resolve coordinates for many locations, preferring graph properties."""

    resolved: dict[str, dict[str, float]] = {}
    for name in names:
        point: tuple[float, float] | None = None
        district = (properties or {}).get(name, {}).get("district")

        if properties and name in properties:
            point = coordinates_from_properties(properties[name])
        if point is None:
            point = static_lookup(name) or cached_lookup(name)
        if point is None:
            point = await geocode(name, district=district)

        if point and not within_bounds(*point):
            logger.warning(
                "Discarding out-of-region coordinates",
                extra={"location": name, "lat": point[0], "lon": point[1]},
            )
            point = None

        if point:
            resolved[name] = {"latitude": point[0], "longitude": point[1]}
        else:
            logger.debug("No coordinates for location", extra={"location": name})

    return resolved
