from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RestaurantRecord:
    name: str | None
    address: str | None
    latitude: float
    longitude: float
    distance_meters: float
    rating: float | None
    review_count: int | None
    price_level: str | None
    categories: list[str]
    website_url: str | None
    phone_number: str | None
    opening_hours: str | None
    business_status: str | None
    is_open_now: bool | None
    service_options: dict[str, Any]
    source_last_updated: str | None
    data_quality_score: float
    source_name: str
    source_id: str
    fetched_at: str
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class MenuSourceRecord:
    restaurant_name: str | None
    restaurant_source_id: str | None
    website_url: str
    candidate_url: str
    source_type: str
    link_text: str | None
    confidence: float
    evidence_grade: str
    reason: str
    is_primary_menu_candidate: bool
    validation_status: str
    validation_reason: str
    fetched_at: str
    raw_payload: dict[str, Any]
