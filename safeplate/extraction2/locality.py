"""City/locality slug helpers for menu-source provenance (dependency-free).

A restaurant chain serves DIFFERENT menus per location, but SafePlate's menu
discovery can fall back to another location's menu (a Cupertino query whose JS
menu is unreadable falling back to a Santa Monica PDF). These helpers detect that
mismatch from STRUCTURE -- the diner's Places address city vs. the menu-source URL
slug -- never from menu prose, mirroring region.py's stance that only structural
signals may vote. Stdlib-only so menu_service and discover can both import it with
no cycle.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Tokens in a menu-source path/filename that are NOT locations, so e.g.
# "dinner-menu.pdf" or "files/.../menu.pdf" don't read as a city. Covers menu/
# section words and common upload-path segments.
_NON_CITY_TOKENS = {
    # menu / section descriptors
    "menu", "menus", "pdf", "pdfs", "brunch", "dinner", "lunch", "breakfast",
    "drinks", "drink", "dessert", "desserts", "kids", "kid", "catering", "wine",
    "wines", "cocktail", "cocktails", "seasonal", "weekend", "new", "updated",
    "current", "food", "allergen", "allergens", "nutrition", "final", "draft",
    "specials", "special", "main", "mains", "full", "sample", "bar", "happy",
    "hour", "holiday", "spring", "summer", "fall", "autumn", "winter",
    "takeout", "delivery", "order", "ordering", "togo", "dinein", "print",
    "online", "download", "view", "our", "home", "index", "page",
    # upload / CMS path segments
    "files", "file", "uploads", "upload", "assets", "asset", "wp", "content",
    "sites", "default", "documents", "docs", "img", "images", "image", "media",
    "static", "cdn", "s", "downloads",
}


def _slug(text: str) -> str:
    """Fold text to a hyphen slug of alphanumeric tokens: 'Santa Monica' ->
    'santa-monica'."""
    return "-".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def city_from_address(address: str | None) -> str | None:
    """The diner-city slug from a Places address ('..., Cupertino, CA 95014, USA'
    -> 'cupertino'). The 2nd comma segment is usually the city (mirrors
    discover._city_token). For short addresses like 'Palo Alto, CA', returns
    the first segment. None when there is no city-like segment."""
    if not address:
        return None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return None
    # For full addresses (3+), the city is the second segment.
    # For short addresses (2), prefer the first segment (likely just city, state).
    if len(parts) >= 3:
        return _slug(parts[1])
    elif len(parts) == 2:
        return _slug(parts[0])
    return None


def url_has_city(url: str, city: str) -> bool:
    """True when EVERY token of the city slug appears as a whole word in the URL
    path (so 'palo-alto' needs both 'palo' and 'alto'). Empty city -> False."""
    if not city:
        return False
    path = urlparse(url or "").path.lower()
    return all(re.search(rf"\b{re.escape(tok)}\b", path) for tok in city.split("-"))


def source_city_slug(url: str, restaurant_name: str = "") -> str | None:
    """Best-effort city slug from a menu-source URL's path + filename. Tokenizes on
    non-alphanumerics, drops restaurant-name tokens, menu/section descriptors and
    upload-path segments, and pure-number/date tokens; the residual 1-3 contiguous
    tokens are the location candidate. None when nothing plausible remains."""
    path = urlparse(url or "").path.lower()
    raw = [t for t in re.split(r"[^a-z0-9]+", path) if t]
    name_tokens = {t for t in re.split(r"[^a-z0-9]+", (restaurant_name or "").lower()) if t}
    residual = [
        t for t in raw
        if t not in name_tokens and t not in _NON_CITY_TOKENS and not t.isdigit()
    ]
    if not residual or len(residual) > 3:
        return None
    return "-".join(residual)


def menu_city_mismatch(url: str, address: str | None, restaurant_name: str = "") -> bool:
    """True when the menu-source URL names a city that clearly differs from the
    diner's city. False when they match, when the URL already contains the diner
    city, or when no city can be read (can't assert a mismatch -> don't warn)."""
    home = city_from_address(address)
    if not home:
        return False
    if url_has_city(url, home):
        return False
    shown = source_city_slug(url, restaurant_name)
    return bool(shown) and shown != home
