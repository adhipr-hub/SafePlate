"""Option C: recover menu items from JSON embedded in the page (free, no network).

Many "JavaScript menus" actually ship the menu as JSON inside the initial HTML
-- Next.js ``__NEXT_DATA__``, ``<script type="application/json">`` state blobs,
or an ordering platform's embedded payload. When the deterministic HTML parser
finds nothing, scanning those blobs for item-shaped objects (a name plus a
price in the same object) recovers a meaningful slice at zero cost.

Schema.org ``Menu`` JSON-LD is intentionally NOT handled here -- that already has
a dedicated, higher-precision extractor in ``menu_text``.
"""

from __future__ import annotations

import re
from typing import Any

from safeplate.json_extract import (
    NAME_KEYS as _NAME_KEYS,
    extract_records_from_html,
    first_string as _first_string,
)
from safeplate.menu_text import MenuItemRecord, _matched_terms, ALLERGEN_TERMS, DIETARY_TERMS

_PRICE_KEYS = {
    "price", "amount", "cost", "baseprice", "base_price", "pricev2",
    "displayprice", "display_price", "unitprice", "unit_price",
}
_DESC_KEYS = {"description", "desc", "caption", "subtitle"}

_MAX_ITEMS = 500


def extract_items_from_embedded_json(html: str) -> list[MenuItemRecord]:
    return extract_records_from_html(
        html,
        item_fn=_item_from_object,
        key_fn=lambda record: (record.item_name.lower(), record.price.lower()),
        max_items=_MAX_ITEMS,
    )


def _item_from_object(obj: dict[str, Any]) -> MenuItemRecord | None:
    name = _first_string(obj, _NAME_KEYS)
    if not name or not (2 <= len(name) <= 120):
        return None
    price = _price_text(obj)
    if not price:
        return None

    description = _first_string(obj, _DESC_KEYS) or ""
    raw_text = f"{name} {description} {price}".strip()
    return MenuItemRecord(
        restaurant_name="",
        restaurant_source_id="",
        menu_source_url="",
        category="",
        item_name=name.strip(),
        description=description.strip(),
        price=price,
        dietary_terms=_matched_terms(raw_text, DIETARY_TERMS),
        allergen_terms=_matched_terms(raw_text, ALLERGEN_TERMS),
        source_type="",
        extraction_method="embedded_json",
        confidence=0.6,
        raw_text=raw_text,
        fetched_at="",
    )


def _price_text(obj: dict[str, Any]) -> str:
    for raw_key, value in obj.items():
        if raw_key.lower() not in _PRICE_KEYS:
            continue
        formatted = _format_price_value(value)
        if formatted:
            return formatted
    return ""


def _format_price_value(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        amount = float(value)
        if amount <= 0:
            return ""
        # Ordering platforms commonly store integer cents (e.g. 1295 -> $12.95).
        if isinstance(value, int) and value >= 100:
            return f"${value / 100:.2f}"
        return f"${amount:.2f}" if amount % 1 else f"${int(amount)}"
    if isinstance(value, str):
        text = value.strip()
        if re.search(r"\d", text) and len(text) <= 16:
            return text if text.startswith("$") else f"${text}" if re.fullmatch(r"\d+(\.\d{1,2})?", text) else text
    if isinstance(value, dict):
        # Shapes like {"amount": 1295, "currencyCode": "USD"} or {"units": "12"}.
        for nested_key in ("amount", "units", "value", "price"):
            if nested_key in value:
                return _format_price_value(value[nested_key])
    return ""
