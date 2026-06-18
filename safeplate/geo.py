from __future__ import annotations

from dataclasses import dataclass
import json
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float


_PLACE_TYPES = {"city", "town", "village", "municipality", "hamlet", "suburb"}


def geocode_location(location: str, user_agent: str) -> Coordinates:
    # Ask for several candidates so we can pick the populated-place centroid
    # rather than whatever Nominatim ranks first (which can be an off-centre
    # feature that misses the restaurant core — e.g. Ithaca landing ~3 km west).
    params = urlencode(
        {
            "q": location,
            "format": "jsonv2",
            "limit": "5",
            "addressdetails": "1",
        }
    )
    request = Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": user_agent},
    )

    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not payload:
        raise ValueError(f"Could not geocode location: {location}")

    result = _best_place(payload)
    return Coordinates(
        latitude=float(result["lat"]),
        longitude=float(result["lon"]),
    )


def _best_place(results: list[dict]) -> dict:
    """Prefer a populated-place / administrative centroid, ranked by importance."""

    def rank(result: dict) -> tuple[int, float]:
        category = str(result.get("category") or result.get("class") or "").lower()
        kind = str(result.get("type") or result.get("addresstype") or "").lower()
        if category == "place" and kind in _PLACE_TYPES:
            tier = 2
        elif category == "boundary" and kind == "administrative":
            tier = 1
        else:
            tier = 0
        try:
            importance = float(result.get("importance") or 0.0)
        except (TypeError, ValueError):
            importance = 0.0
        return (tier, importance)

    return max(results, key=rank)


def haversine_meters(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    earth_radius_meters = 6_371_000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_meters * c
