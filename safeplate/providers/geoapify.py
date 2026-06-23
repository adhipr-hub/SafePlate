from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from safeplate.quality import restaurant_quality_score
from safeplate.schemas import RestaurantRecord


GEOAPIFY_PLACES_URL = "https://api.geoapify.com/v2/places"

GEOAPIFY_CATEGORIES = [
    "catering.restaurant",
    "catering.cafe",
    "catering.fast_food",
    "catering.food_court",
]


class GeoapifyError(RuntimeError):
    """Raised when Geoapify cannot return restaurant data."""


def fetch_nearby_restaurants(
    *,
    latitude: float,
    longitude: float,
    radius_meters: int,
    limit: int,
    api_key: str,
    user_agent: str,
    categories: list[str] | None = None,
    conditions: list[str] | None = None,
) -> list[RestaurantRecord]:
    payload = _fetch_geoapify_payload(
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_meters,
        limit=limit,
        api_key=api_key,
        user_agent=user_agent,
        categories=categories or GEOAPIFY_CATEGORIES,
        conditions=conditions or [],
    )

    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = [
        _normalize_feature(
            feature,
            fetched_at=fetched_at,
        )
        for feature in payload.get("features", [])
        if _has_coordinates(feature)
    ]

    rows.sort(key=lambda row: row.distance_meters)
    return rows[:limit]


def _fetch_geoapify_payload(
    *,
    latitude: float,
    longitude: float,
    radius_meters: int,
    limit: int,
    api_key: str,
    user_agent: str,
    categories: list[str],
    conditions: list[str],
) -> dict[str, Any]:
    query_params = {
        "categories": ",".join(categories),
        "filter": f"circle:{longitude},{latitude},{radius_meters}",
        "bias": f"proximity:{longitude},{latitude}",
        "limit": str(limit),
        "apiKey": api_key,
    }
    if conditions:
        query_params["conditions"] = ",".join(conditions)

    params = urlencode(query_params)
    request = Request(
        f"{GEOAPIFY_PLACES_URL}?{params}",
        headers={"User-Agent": user_agent},
    )

    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        with exc:  # HTTPError is an open response; close it after reading the body
            details = exc.read().decode("utf-8", errors="replace")
        raise GeoapifyError(
            f"Geoapify request failed with HTTP {exc.code}: {details}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise GeoapifyError(f"Geoapify request failed: {exc}") from exc


def _normalize_feature(feature: dict[str, Any], fetched_at: str) -> RestaurantRecord:
    properties = feature.get("properties", {})
    latitude, longitude = _feature_coordinates(feature)

    record = RestaurantRecord(
        name=properties.get("name"),
        address=properties.get("formatted"),
        latitude=latitude,
        longitude=longitude,
        distance_meters=round(float(properties.get("distance") or 0), 1),
        rating=None,
        review_count=None,
        price_level=None,
        categories=properties.get("categories") or [],
        website_url=_first_value(
            properties,
            ["website", "contact.website", "datasource.raw.website"],
        ),
        phone_number=_first_value(
            properties,
            ["phone", "contact.phone", "datasource.raw.phone"],
        ),
        opening_hours=_first_value(
            properties,
            ["opening_hours", "datasource.raw.opening_hours"],
        ),
        business_status=_business_status_from_properties(properties),
        is_open_now=None,
        service_options=_service_options_from_properties(properties),
        source_last_updated=_first_value(
            properties,
            [
                "datasource.raw.check_date",
                "datasource.raw.check_date:opening_hours",
                "datasource.raw.survey:date",
            ],
        ),
        data_quality_score=0.0,
        source_name="geoapify",
        source_id=_source_id_from_properties(properties),
        fetched_at=fetched_at,
        raw_payload=feature,
    )
    return replace(record, data_quality_score=restaurant_quality_score(record))


def _has_coordinates(feature: dict[str, Any]) -> bool:
    geometry = feature.get("geometry", {})
    coordinates = geometry.get("coordinates", [])
    return len(coordinates) >= 2


def _feature_coordinates(feature: dict[str, Any]) -> tuple[float, float]:
    longitude, latitude = feature["geometry"]["coordinates"][:2]
    return float(latitude), float(longitude)


def _business_status_from_properties(properties: dict[str, Any]) -> str | None:
    raw = properties.get("datasource", {}).get("raw", {})
    if raw.get("disused:amenity") or raw.get("abandoned:amenity"):
        return "closed_or_inactive"
    if properties.get("categories"):
        return "presumed_operational"
    return None


def _service_options_from_properties(properties: dict[str, Any]) -> dict[str, bool]:
    raw = properties.get("datasource", {}).get("raw", {})
    result = {}
    for raw_key, output_key in [
        ("takeaway", "takeout"),
        ("delivery", "delivery"),
        ("outdoor_seating", "outdoorSeating"),
        ("indoor_seating", "indoorSeating"),
        ("diet:vegetarian", "servesVegetarianFood"),
        ("diet:vegan", "servesVeganFood"),
    ]:
        value = raw.get(raw_key)
        if value in ["yes", "no"]:
            result[output_key] = value == "yes"
    return result


def _source_id_from_properties(properties: dict[str, Any]) -> str:
    place_id = properties.get("place_id")
    if place_id:
        return str(place_id)

    raw = properties.get("datasource", {}).get("raw", {})
    osm_id = raw.get("osm_id")
    if osm_id:
        osm_type = raw.get("osm_type")
        return f"{osm_type}/{osm_id}" if osm_type else str(osm_id)

    return ""


def _first_value(payload: dict[str, Any], paths: list[str]) -> str | None:
    for path in paths:
        value = _nested_get(payload, path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _nested_get(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
