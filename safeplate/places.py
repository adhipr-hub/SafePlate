"""Classify whether a provider result is an actual food establishment.

Nearby-search providers (Google Places especially) return non-food POIs —
shopping malls, hotels, cinemas, museums, department stores — that often carry a
generic ``restaurant``/``food`` type. Feeding those into menu discovery wastes
effort and makes coverage metrics misleading. This filter keeps the funnel
honest using the provider's own type tags (general, not per-site).
"""

from __future__ import annotations

# Primary-type substrings that mark a genuine food establishment.
_FOOD_PRIMARY = (
    "restaurant", "cafe", "coffee_shop", "bakery", "bar", "pub", "bistro",
    "diner", "meal_takeaway", "meal_delivery", "fast_food", "ice_cream",
    "food_court", "tea_house", "juice_shop", "deli", "steak_house", "pizza",
)
# Non-food primary types that should win even if a generic "food" tag is present.
_NON_FOOD_PRIMARY = (
    "shopping_mall", "department_store", "hotel", "lodging", "movie_theater",
    "museum", "supermarket", "grocery", "tourist_attraction", "gym",
    "convention_center", "store", "stadium", "park", "airport", "hospital",
)


def _tokens(categories) -> list[str]:
    if isinstance(categories, str):
        parts = categories.split(";")
    else:
        parts = list(categories or [])
    return [str(p).split(":", 1)[-1].strip().lower() for p in parts if str(p).strip()]


def _primary_type(categories) -> str:
    parts = categories.split(";") if isinstance(categories, str) else list(categories or [])
    for p in parts:
        text = str(p).strip().lower()
        if text.startswith("primary_type:"):
            return text.split(":", 1)[1]
    return ""


def is_food_place(categories) -> bool:
    """True if the categories describe a food establishment, not a mall/hotel/etc."""
    primary = _primary_type(categories)
    if any(bad in primary for bad in _NON_FOOD_PRIMARY):
        return False
    if any(good in primary for good in _FOOD_PRIMARY):
        return True
    # No usable primary type: fall back to any food-ish token, but still reject
    # if a non-food token dominates the list.
    tokens = _tokens(categories)
    if any(any(bad in t for bad in _NON_FOOD_PRIMARY) for t in tokens):
        return False
    return any(any(good in t for good in _FOOD_PRIMARY) for t in tokens)
