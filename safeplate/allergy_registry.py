"""Curated registry of dedicated allergy-safe / nut-free kitchens.

Dedicated nut-free kitchens are rare and barely discoverable online (Places returns
gluten-free instead; the real ones often have defunct/anti-bot sites), so the only
reliable way to CREDIT them is a curated, human-verified data file -- data, not
scraping. A match sets a trusted nut-free / allergy-aware signal that flows into the
scorer's existing down-pull.

SAFETY: a wrong 'nut_free' entry under-reports risk for an allergic user, so entries
in data/allergy_registry.json MUST be human-verified and carry a `source`. Matching is
deliberately conservative (domain, or compact-name + city) to avoid false matches.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_PATH = Path(__file__).resolve().parent / "data" / "allergy_registry.json"
_ENTRIES: list[dict[str, Any]] | None = None


def _entries() -> list[dict[str, Any]]:
    global _ENTRIES
    if _ENTRIES is None:
        try:
            blob = json.loads(_PATH.read_text(encoding="utf-8"))
            _ENTRIES = [e for e in blob.get("entries", []) if isinstance(e, dict)]
        except (OSError, ValueError):
            _ENTRIES = []
    return _ENTRIES


def _norm(value: str | None) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _registrable(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    labels = [seg for seg in host.split(".") if seg]
    return ".".join(labels[-2:]) if len(labels) > 2 else ".".join(labels)


def lookup_registry(
    name: str | None,
    address: str | None = None,
    website_url: str | None = None,
) -> dict[str, Any] | None:
    """Return a verified registry entry for this restaurant, or None. Conservative:
    matches on registrable DOMAIN, or on (compact name match AND the entry's city
    appearing in the address) -- so a short name alone can't false-match."""
    q_name = _norm(name)
    q_addr = (address or "").lower()
    q_dom = _registrable(urlparse(website_url or "").netloc) if website_url else ""
    for e in _entries():
        dom = (e.get("domain") or "").lower()
        if dom and q_dom and (q_dom == dom or q_dom.endswith("." + dom)):
            return e
        ename = _norm(e.get("name"))
        city = (e.get("city") or "").lower()
        if ename and q_name and ename in q_name and (not city or city in q_addr):
            return e
    return None


def apply_registry(signals: Any, name: str | None, address: str | None,
                   website_url: str | None) -> dict[str, Any] | None:
    """OR the registry's trusted signals into a RestaurantSignals (a verified nut-free
    kitchen -> nut_free_claim; a dedicated-allergy kitchen -> allergy_disclaimer).
    Returns the matched entry (for provenance/UI) or None. Never lowers a signal."""
    entry = lookup_registry(name, address, website_url)
    if not entry:
        return None
    if entry.get("nut_free"):
        signals.nut_free_claim = True
    if entry.get("allergy_dedicated"):
        signals.allergy_disclaimer = True
    return entry
