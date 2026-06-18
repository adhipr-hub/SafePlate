from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from http.client import RemoteDisconnected
import json
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from safeplate.geo import haversine_meters
from safeplate.schemas import RestaurantRecord
from safeplate.quality import restaurant_quality_score


OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

FOOD_AMENITIES = ["restaurant", "cafe", "fast_food", "food_court"]


class OverpassError(RuntimeError):
    """Raised when all Overpass API attempts fail."""


def fetch_nearby_restaurants(
    latitude: float,
    longitude: float,
    radius_meters: int,
    limit: int,
    user_agent: str,
) -> list[RestaurantRecord]:
    query = _build_overpass_query(latitude, longitude, radius_meters)
    payload = _fetch_overpass_payload(query, user_agent)

    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = [
        _normalize_element(
            element,
            origin_latitude=latitude,
            origin_longitude=longitude,
            fetched_at=fetched_at,
        )
        for element in payload.get("elements", [])
        if _has_coordinates(element)
    ]

    rows.sort(key=lambda row: row.distance_meters)
    return rows[:limit]


def _build_overpass_query(latitude: float, longitude: float, radius_meters: int) -> str:
    amenity_filter = "|".join(FOOD_AMENITIES)
    return f"""
[out:json][timeout:60];
(
  nwr["amenity"~"^({amenity_filter})$"](around:{radius_meters},{latitude},{longitude});
);
out tags center qt;
"""


def _fetch_overpass_payload(query: str, user_agent: str) -> dict[str, Any]:
    errors = []
    for index, url in enumerate(OVERPASS_URLS):
        try:
            request = Request(
                url,
                data=query.encode("utf-8"),
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "User-Agent": user_agent,
                },
                method="POST",
            )
            with urlopen(request, timeout=90) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, RemoteDisconnected) as exc:
            errors.append(f"{url}: {exc}")
            if index < len(OVERPASS_URLS) - 1:
                sleep(1.5)

    message = (
        "OpenStreetMap Overpass did not respond successfully. "
        "Public Overpass servers can time out when they are busy. "
        "Try again, reduce --radius, or lower --limit."
    )
    raise OverpassError(f"{message} Attempts: {' | '.join(errors)}")


def _normalize_element(
    element: dict[str, Any],
    origin_latitude: float,
    origin_longitude: float,
    fetched_at: str,
) -> RestaurantRecord:
    tags = element.get("tags", {})
    latitude, longitude = _element_coordinates(element)
    categories = _categories_from_tags(tags)

    record = RestaurantRecord(
        name=tags.get("name"),
        address=_address_from_tags(tags),
        latitude=latitude,
        longitude=longitude,
        distance_meters=round(
            haversine_meters(origin_latitude, origin_longitude, latitude, longitude),
            1,
        ),
        rating=None,
        review_count=None,
        price_level=None,
        categories=categories,
        website_url=tags.get("website") or tags.get("contact:website"),
        phone_number=tags.get("phone") or tags.get("contact:phone"),
        opening_hours=tags.get("opening_hours"),
        business_status=_business_status_from_tags(tags),
        is_open_now=None,
        service_options={},
        source_last_updated=_source_last_updated_from_tags(tags),
        data_quality_score=0.0,
        source_name="openstreetmap",
        source_id=f'{element.get("type")}/{element.get("id")}',
        fetched_at=fetched_at,
        raw_payload=element,
    )
    return replace(record, data_quality_score=restaurant_quality_score(record))


def _has_coordinates(element: dict[str, Any]) -> bool:
    if "lat" in element and "lon" in element:
        return True
    center = element.get("center", {})
    return "lat" in center and "lon" in center


def _element_coordinates(element: dict[str, Any]) -> tuple[float, float]:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element["center"]
    return float(center["lat"]), float(center["lon"])


def _categories_from_tags(tags: dict[str, str]) -> list[str]:
    categories = []
    for key in ["amenity", "cuisine", "diet:vegan", "diet:vegetarian"]:
        value = tags.get(key)
        if value:
            categories.append(f"{key}:{value}")
    return categories


def _address_from_tags(tags: dict[str, str]) -> str | None:
    house_number = tags.get("addr:housenumber")
    street = tags.get("addr:street")
    city = tags.get("addr:city")
    state = tags.get("addr:state")
    postcode = tags.get("addr:postcode")

    street_line = " ".join(part for part in [house_number, street] if part)
    city_line = ", ".join(part for part in [city, state] if part)
    address = ", ".join(part for part in [street_line, city_line, postcode] if part)
    return address or None


def _business_status_from_tags(tags: dict[str, str]) -> str | None:
    if tags.get("disused:amenity") or tags.get("abandoned:amenity"):
        return "closed_or_inactive"
    if tags.get("amenity") in FOOD_AMENITIES:
        return "presumed_operational"
    return None


def _source_last_updated_from_tags(tags: dict[str, str]) -> str | None:
    for key in [
        "check_date",
        "check_date:opening_hours",
        "survey:date",
        "opening_hours:signed",
    ]:
        value = tags.get(key)
        if value:
            return value
    return None

