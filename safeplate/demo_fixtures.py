from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from safeplate.menu_text import MenuItemRecord
from safeplate.menu_text import MenuTextRecord
from safeplate.schemas import MenuSourceRecord
from safeplate.schemas import RestaurantRecord


ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "fixtures" / "demo"
DEFAULT_DEMO_LOCATION = "SafePlate Demo"


class DemoFixtureError(RuntimeError):
    """Raised when demo fixture data is missing or malformed."""


@dataclass(frozen=True)
class DemoSearchFixture:
    default_location: str
    location: str
    coordinates: dict[str, float]
    radius: int
    limit: int
    restaurants: list[RestaurantRecord]


@dataclass(frozen=True)
class DemoMenuFixture:
    scenario: str
    menu_sources: list[MenuSourceRecord]
    menu_text: list[MenuTextRecord]
    menu_items: list[MenuItemRecord]


def load_demo_search() -> DemoSearchFixture:
    payload = _read_json(DEMO_DIR / "search.json")
    _require_keys(
        payload,
        ["defaultLocation", "location", "coordinates", "radius", "limit", "restaurants"],
        "search fixture",
    )
    restaurants = [
        _restaurant_record(row, f"restaurants[{index}]")
        for index, row in enumerate(_list(payload["restaurants"]), start=0)
    ]
    coordinates = payload["coordinates"]
    if not isinstance(coordinates, dict):
        raise DemoFixtureError("search fixture coordinates must be an object")
    return DemoSearchFixture(
        default_location=str(payload["defaultLocation"]),
        location=str(payload["location"]),
        coordinates={
            "latitude": float(coordinates["latitude"]),
            "longitude": float(coordinates["longitude"]),
        },
        radius=int(payload["radius"]),
        limit=int(payload["limit"]),
        restaurants=restaurants,
    )


def load_demo_menu(source_id: str) -> DemoMenuFixture:
    normalized = _safe_fixture_id(source_id)
    if not normalized:
        raise DemoFixtureError("Demo menu source_id is required")
    payload = _read_json(DEMO_DIR / "menus" / f"{normalized}.json")
    _require_keys(payload, ["scenario", "menuSources", "menuText", "menuItems"], "menu fixture")
    return DemoMenuFixture(
        scenario=str(payload["scenario"]),
        menu_sources=[
            _menu_source_record(row, f"menuSources[{index}]")
            for index, row in enumerate(_list(payload["menuSources"]), start=0)
        ],
        menu_text=[
            _menu_text_record(row, f"menuText[{index}]")
            for index, row in enumerate(_list(payload["menuText"]), start=0)
        ],
        menu_items=[
            _menu_item_record(row, f"menuItems[{index}]")
            for index, row in enumerate(_list(payload["menuItems"]), start=0)
        ],
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DemoFixtureError(f"Missing demo fixture: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DemoFixtureError(f"Invalid demo fixture JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise DemoFixtureError(f"Demo fixture must be a JSON object: {path}")
    return payload


def _require_keys(payload: dict[str, Any], keys: list[str], label: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise DemoFixtureError(f"Missing {label} key(s): {', '.join(missing)}")


def _list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise DemoFixtureError("Expected fixture value to be a list")
    rows = []
    for row in value:
        if not isinstance(row, dict):
            raise DemoFixtureError("Fixture list entries must be objects")
        rows.append(row)
    return rows


def _restaurant_record(row: dict[str, Any], label: str) -> RestaurantRecord:
    _require_keys(
        row,
        [
            "name",
            "address",
            "latitude",
            "longitude",
            "distance_meters",
            "categories",
            "source_name",
            "source_id",
            "fetched_at",
            "raw_payload",
        ],
        label,
    )
    return RestaurantRecord(**row)


def _menu_source_record(row: dict[str, Any], label: str) -> MenuSourceRecord:
    _require_keys(
        row,
        [
            "restaurant_name",
            "restaurant_source_id",
            "website_url",
            "candidate_url",
            "source_type",
            "confidence",
            "evidence_grade",
            "validation_status",
            "fetched_at",
            "raw_payload",
        ],
        label,
    )
    return MenuSourceRecord(**row)


def _menu_text_record(row: dict[str, Any], label: str) -> MenuTextRecord:
    _require_keys(
        row,
        [
            "restaurant_name",
            "restaurant_source_id",
            "menu_source_url",
            "source_type",
            "extraction_method",
            "char_count",
            "price_count",
            "dietary_terms",
            "allergen_terms",
            "fetched_at",
            "extracted_text",
        ],
        label,
    )
    return MenuTextRecord(**row)


def _menu_item_record(row: dict[str, Any], label: str) -> MenuItemRecord:
    _require_keys(
        row,
        [
            "restaurant_name",
            "restaurant_source_id",
            "menu_source_url",
            "category",
            "item_name",
            "description",
            "price",
            "dietary_terms",
            "allergen_terms",
            "source_type",
            "extraction_method",
            "confidence",
            "raw_text",
            "fetched_at",
        ],
        label,
    )
    return MenuItemRecord(**row)


def _safe_fixture_id(value: str) -> str:
    return "".join(char for char in value.strip().lower() if char.isalnum() or char in "-_")
