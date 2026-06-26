"""Tier 1: recover dish x allergen data from JSON embedded in a page (free, no
browser).

JS allergen tools (filters, nutrition calculators) usually hydrate from a JSON
blob already present in the initial HTML -- `__NEXT_DATA__`, `__NUXT__`,
`__APOLLO_STATE__`, or a `<script type="application/json">` payload. The existing
`embedded_json` extractor misses this data because it REQUIRES a price and only
reads allergens out of free text. This reads the STRUCTURED allergen fields
(arrays like `["Milk","Egg"]` or flag maps like `{milk: true}`) with no price
needed.

Robust, not field-name-hardcoded: an object is a dish-with-allergens if it has a
name AND some array/flag field whose contents match the controlled allergen
vocabulary (`ALLERGEN_TERMS`). Detection is driven by that vocabulary, so it
generalizes across wildly different field names and languages.
"""

from __future__ import annotations

from typing import Any

from safeplate.json_extract import (
    NAME_KEYS as _NAME_KEYS,
    extract_records_from_html,
    extract_records_from_obj,
    first_string as _first_string,
)
from safeplate.menu_text import ALLERGEN_TERMS, MenuItemRecord, _matched_terms

_TRUTHY = {"true", "1", "yes", "y", "contains", "may contain", "present", "x", "✓", "✔", "●"}
_FLAG_KEYS = ("contains", "present", "value", "status", "flag", "ispresent", "has", "marked")
_ALLERGEN_NAME_KEYS = {"name", "allergen", "allergenname", "allergen_name", "label", "title", "code"}


def extract_allergen_items_from_embedded_json(
    html: str, *, soup: Any = None
) -> list[MenuItemRecord]:
    return extract_records_from_html(
        html, item_fn=_item_from_object, key_fn=lambda record: record.item_name.lower(),
        soup=soup,
    )


def extract_allergen_items_from_obj(payload: Any) -> list[MenuItemRecord]:
    """Same dish x allergen detection over an already-parsed JSON object -- used by
    the Tier 2 API-capture path on a backend response, not just embedded blobs."""
    return extract_records_from_obj(
        payload, item_fn=_item_from_object, key_fn=lambda record: record.item_name.lower()
    )


def _item_from_object(obj: dict[str, Any]) -> MenuItemRecord | None:
    name = _first_string(obj, _NAME_KEYS)
    if not name or not (2 <= len(name) <= 120):
        return None
    allergens = _allergens_from_object(obj)
    if not allergens:
        return None
    return MenuItemRecord(
        restaurant_name="",
        restaurant_source_id="",
        menu_source_url="",
        category="",
        item_name=name.strip(),
        description="",
        price="",
        dietary_terms=[],
        allergen_terms=allergens,
        source_type="",
        extraction_method="embedded_allergens",
        confidence=0.6,
        raw_text=f"{name.strip()}: {', '.join(allergens)}",
        fetched_at="",
    )


def _allergens_from_object(obj: dict[str, Any]) -> list[str]:
    """Allergens declared in this object's STRUCTURED fields (arrays / flag maps /
    flag-object lists), matched against the controlled allergen vocabulary."""
    found: set[str] = set()
    for key, value in obj.items():
        if isinstance(key, str) and key.lower() in _NAME_KEYS:
            continue
        if isinstance(value, list) and value:
            if all(isinstance(x, str) for x in value):
                tokens = [x for x in value if len(x) <= 40]
                if tokens:
                    found.update(_matched_terms(" ".join(tokens).lower(), ALLERGEN_TERMS))
            elif all(isinstance(x, dict) for x in value):
                for entry in value:
                    entry_name = _first_string(entry, _ALLERGEN_NAME_KEYS)
                    if entry_name and _truthy(_flag_value(entry)):
                        found.update(_matched_terms(entry_name.lower(), ALLERGEN_TERMS))
        elif isinstance(value, dict):
            for flag_key, flag_value in value.items():
                if isinstance(flag_key, str) and _truthy(flag_value):
                    found.update(_matched_terms(flag_key.lower(), ALLERGEN_TERMS))
    return sorted(found)


def _flag_value(entry: dict[str, Any]) -> Any:
    """The presence flag in a {name: 'Milk', contains: true} object; absent flag
    means the allergen is simply listed as present."""
    for raw_key, raw_value in entry.items():
        if isinstance(raw_key, str) and raw_key.lower() in _FLAG_KEYS:
            return raw_value
    return True


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return False
