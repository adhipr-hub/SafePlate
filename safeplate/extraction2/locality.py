"""City/locality slug helpers for menu-source provenance (dependency-free).

A restaurant chain serves DIFFERENT menus per location, but SafePlate's menu
discovery can fall back to another location's menu (a Cupertino query whose JS
menu is unreadable falling back to a Santa Monica PDF). These helpers detect that
mismatch from STRUCTURE -- the diner's Places address city vs. the menu-source URL
slug, or an address-SHAPED locality declaration inside the document itself -- never
from free menu prose, mirroring region.py's stance that only structural signals may
vote. Depends only on the stdlib (plus the equally stdlib-only region module) so
menu_service and discover can both import it with no cycle.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from safeplate.extraction2.region import _visible_text

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
    -> 'cupertino'). Mirrors discover._city_token for 3+-segment addresses; for a
    2-segment address it returns the first segment (the city) rather than the second.
    None when there is no city-like segment."""
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
    upload-path segments, and pure-number/date tokens; the residual 1-3 tokens
    (in original order) are the location candidate. None when nothing plausible remains."""
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


# --- In-document locality declarations ----------------------------------------
# An off-site (web-search) menu document usually prints its restaurant's ADDRESS
# somewhere -- an address-shaped comma sequence ending in a country name or a US
# "ST 12345" tail. That declaration is structural (not menu prose), so it may vote
# on whether the document belongs to the diner's restaurant at all: a same-name
# aggregator PDF for the wrong city passes the name check (_pdf_mentions) but its
# printed address gives it away (Cicero's Pizza San Jose CA <- an Interlochen MI
# menuweb.menu PDF whose text says "2408 M 137, Interlochen, United States").

_US_STATE_ABBRS = (
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
    "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
    "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
)

# Country names/aliases that can TERMINATE an address-shaped comma sequence.
# Multiword names and 3+-letter aliases only: 2-letter aliases ("us", "uk") match
# far too much prose ("join us, ...") to anchor a rejection on.
_ADDR_COUNTRY_NAMES = (
    "united states of america", "united states", "usa", "united kingdom",
    "great britain", "england", "scotland", "wales", "canada", "australia",
    "new zealand", "ireland", "germany", "france", "spain", "italy",
    "netherlands", "belgium", "austria", "switzerland", "sweden", "norway",
    "denmark", "finland", "portugal", "poland", "malta", "singapore",
    "philippines", "japan", "taiwan", "india", "mexico", "brazil",
    "south africa", "south korea", "hong kong", "china",
    "united arab emirates", "saudi arabia",
)

# "<segment>, <country>" -- the comma-segment right before a country name is the
# locality candidate ("..., Interlochen, United States" -> "Interlochen").
_ADDR_COUNTRY_RE = re.compile(
    r"([^,]{1,40}),\s*(?:"
    + "|".join(sorted(map(re.escape, _ADDR_COUNTRY_NAMES), key=len, reverse=True))
    + r")\b"
)
# "<segment>, <ST> <zip>" -- the US state+ZIP tail many address blocks end with
# ("..., Springfield, IL 62704" -> "Springfield").
_ADDR_STATE_ZIP_RE = re.compile(
    r"([^,]{1,40}),\s*(?:" + "|".join(_US_STATE_ABBRS) + r")\s+\d{5}\b"
)


def _locality_candidate(segment: str, restaurant_name: str = "") -> str | None:
    """The city slug an address-shaped segment plausibly declares, filtered like
    source_city_slug: drop restaurant-name tokens, menu descriptors, and numbers;
    the residual must be 1-3 tokens with at least one real (3+-char) word."""
    name_tokens = {t for t in re.split(r"[^a-z0-9]+", (restaurant_name or "").lower()) if t}
    tokens = [t for t in re.split(r"[^a-z0-9]+", segment.lower()) if t]
    residual = [
        t for t in tokens
        if t not in name_tokens and t not in _NON_CITY_TOKENS and not t.isdigit()
    ]
    if not residual or len(residual) > 3:
        return None
    if not any(len(t) >= 3 for t in residual):
        return None
    return "-".join(residual)


def text_locality_contradiction(
    text: str, address: str | None, restaurant_name: str = ""
) -> bool:
    """True when a menu document's visible text DECLARES a locality (an
    address-shaped '..., <city>, <country>' or '<city>, <ST> <zip>') that differs
    from the diner's city while the diner's city appears NOWHERE in the text.
    Used to reject off-site web-search documents BEFORE their items merge --
    safety-asymmetric: a same-name wrong-city menu is worse than no menu at all.
    Conservative in both inputs: no readable home city, no declared locality, or
    any mention of the home city -> False (never reject on a guess)."""
    home = city_from_address(address)
    if not home or not (text or "").strip():
        return False
    # Fold ALL whitespace so a line-wrapped country ('United\nStates') still reads
    # as one phrase; strip markup first so hidden HTML can't vote either way.
    low = " ".join(_visible_text(text).lower().split())
    # Corroboration first: the diner's city anywhere in the text clears the doc
    # (also protects a home address whose own ', CA 95129, USA' tail would
    # otherwise read as a non-city candidate).
    home_re = r"\b" + r"[\W_]+".join(re.escape(t) for t in home.split("-")) + r"\b"
    if re.search(home_re, low):
        return False
    for pattern in (_ADDR_COUNTRY_RE, _ADDR_STATE_ZIP_RE):
        for match in pattern.finditer(low):
            declared = _locality_candidate(match.group(1), restaurant_name)
            if declared and declared != home:
                return True
    return False


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
