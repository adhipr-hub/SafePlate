from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from safeplate.coerce import optional_float as _optional_float
from safeplate.coerce import optional_int as _optional_int
from safeplate.geo import haversine_meters
from safeplate.quality import restaurant_quality_score
from safeplate.schemas import RestaurantRecord


GOOGLE_NEARBY_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"

GOOGLE_INCLUDED_TYPES = [
    "restaurant",
    "cafe",
    "meal_takeaway",
    "meal_delivery",
]

GOOGLE_STANDARD_FIELDS = [
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.rating",
    "places.userRatingCount",
    "places.priceLevel",
    "places.types",
    "places.primaryType",
    "places.websiteUri",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.businessStatus",
    "places.regularOpeningHours",
    "places.currentOpeningHours",
]

GOOGLE_ATMOSPHERE_FIELDS = [
    "places.servesBreakfast",
    "places.servesLunch",
    "places.servesDinner",
    "places.servesBrunch",
    "places.servesVegetarianFood",
    "places.servesBeer",
    "places.servesWine",
    "places.servesCocktails",
    "places.servesDessert",
    "places.servesCoffee",
    "places.menuForChildren",
    "places.takeout",
    "places.delivery",
    "places.dineIn",
    "places.curbsidePickup",
    "places.reservable",
]


class GooglePlacesError(RuntimeError):
    """Raised when Google Places cannot return restaurant data."""


def fetch_nearby_restaurants(
    *,
    latitude: float,
    longitude: float,
    radius_meters: int,
    limit: int,
    api_key: str,
    user_agent: str,
    included_types: list[str] | None = None,
    include_atmosphere_fields: bool = False,
    rank_preference: str | None = None,
) -> list[RestaurantRecord]:
    if rank_preference is None:
        from safeplate.config import get_google_rank_preference

        rank_preference = get_google_rank_preference()
    payload = _fetch_google_places_payload(
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_meters,
        limit=limit,
        api_key=api_key,
        user_agent=user_agent,
        included_types=included_types or GOOGLE_INCLUDED_TYPES,
        rank_preference=rank_preference,
        field_mask=_google_field_mask(
            include_atmosphere_fields=include_atmosphere_fields,
        ),
    )

    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = [
        _normalize_place(
            place,
            origin_latitude=latitude,
            origin_longitude=longitude,
            fetched_at=fetched_at,
        )
        for place in payload.get("places", [])
        if _has_coordinates(place)
    ]

    rows.sort(key=lambda row: row.distance_meters)
    return rows[:limit]


def _google_search_body(
    *,
    latitude: float,
    longitude: float,
    radius_meters: int,
    limit: int,
    included_types: list[str],
    rank_preference: str = "DISTANCE",
) -> dict[str, Any]:
    """Build the searchNearby request body. ``rankPreference`` is DISTANCE so Google
    returns the NEAREST places; its default (POPULARITY) returns the most prominent in
    the radius and drops genuinely-close restaurants before our distance sort runs."""
    return {
        "includedTypes": included_types,
        "maxResultCount": min(limit, 20),
        "rankPreference": rank_preference,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": latitude,
                    "longitude": longitude,
                },
                "radius": float(radius_meters),
            }
        },
    }


def _fetch_google_places_payload(
    *,
    latitude: float,
    longitude: float,
    radius_meters: int,
    limit: int,
    api_key: str,
    user_agent: str,
    included_types: list[str],
    field_mask: str,
    rank_preference: str = "DISTANCE",
) -> dict[str, Any]:
    body = _google_search_body(
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_meters,
        limit=limit,
        included_types=included_types,
        rank_preference=rank_preference,
    )
    request = Request(
        GOOGLE_NEARBY_SEARCH_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": user_agent,
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": field_mask,
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        with exc:  # HTTPError is an open response; close it after reading the body
            details = exc.read().decode("utf-8", errors="replace")
        raise GooglePlacesError(
            f"Google Places request failed with HTTP {exc.code}: {details}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise GooglePlacesError(f"Google Places request failed: {exc}") from exc


def _normalize_place(
    place: dict[str, Any],
    origin_latitude: float,
    origin_longitude: float,
    fetched_at: str,
) -> RestaurantRecord:
    location = place.get("location", {})
    latitude = float(location["latitude"])
    longitude = float(location["longitude"])
    opening_hours = _opening_hours_text(place)

    record = RestaurantRecord(
        name=place.get("displayName", {}).get("text"),
        address=place.get("formattedAddress"),
        latitude=latitude,
        longitude=longitude,
        distance_meters=round(
            haversine_meters(origin_latitude, origin_longitude, latitude, longitude),
            1,
        ),
        rating=_optional_float(place.get("rating")),
        review_count=_optional_int(place.get("userRatingCount")),
        price_level=place.get("priceLevel"),
        categories=_categories_from_place(place),
        website_url=place.get("websiteUri"),
        phone_number=place.get("nationalPhoneNumber")
        or place.get("internationalPhoneNumber"),
        opening_hours=opening_hours,
        business_status=place.get("businessStatus"),
        is_open_now=_open_now_from_place(place),
        service_options=_service_options_from_place(place),
        source_last_updated=None,
        data_quality_score=0.0,
        source_name="google_places",
        source_id=str(place.get("id") or ""),
        fetched_at=fetched_at,
        raw_payload=place,
    )
    return replace(record, data_quality_score=restaurant_quality_score(record))


def _has_coordinates(place: dict[str, Any]) -> bool:
    location = place.get("location", {})
    return "latitude" in location and "longitude" in location


def _categories_from_place(place: dict[str, Any]) -> list[str]:
    categories = []
    primary_type = place.get("primaryType")
    if primary_type:
        categories.append(f"primary_type:{primary_type}")
    categories.extend(place.get("types", []))
    return categories


def _opening_hours_text(place: dict[str, Any]) -> str | None:
    for field in ["currentOpeningHours", "regularOpeningHours"]:
        opening_hours = place.get(field, {})
        descriptions = opening_hours.get("weekdayDescriptions") or []
        if descriptions:
            return "; ".join(descriptions)
    return None


def _open_now_from_place(place: dict[str, Any]) -> bool | None:
    for field in ["currentOpeningHours", "regularOpeningHours"]:
        opening_hours = place.get(field, {})
        if "openNow" in opening_hours:
            return bool(opening_hours["openNow"])
    return None


def _service_options_from_place(place: dict[str, Any]) -> dict[str, bool]:
    keys = [
        "servesBreakfast",
        "servesLunch",
        "servesDinner",
        "servesBrunch",
        "servesVegetarianFood",
        "servesBeer",
        "servesWine",
        "servesCocktails",
        "servesDessert",
        "servesCoffee",
        "menuForChildren",
        "takeout",
        "delivery",
        "dineIn",
        "curbsidePickup",
        "reservable",
    ]
    return {
        key: bool(place[key])
        for key in keys
        if key in place
    }


def _google_field_mask(*, include_atmosphere_fields: bool) -> str:
    fields = list(GOOGLE_STANDARD_FIELDS)
    if include_atmosphere_fields:
        fields.extend(GOOGLE_ATMOSPHERE_FIELDS)
    return ",".join(fields)

