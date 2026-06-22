"""Community / anecdotal allergy signals from web search (Tier C feed for Layer #5).

Populates the ``CommunitySignal`` seam the scorer already understands
(``allergen_score._apply_community``) and, when a restaurant has NO machine-readable
menu, infers likely dishes from what diners mention -- low-confidence context that
feeds the dish-name PRIOR (never grounded allergen evidence).

Source = Brave web search (Reddit / blogs / listicles), NOT Google reviews: the
Places API returns 0 reviews for our key (not provisioned for the Enterprise SKU)
and Google content can't be cached per ToS. Web-search snippets are cacheable, cheap,
and ToS-clean. SAFETY-ASYMMETRIC by construction: adverse/allergen mentions raise risk
(handled in the scorer); positive handling only improves the handling signal; absence
of mentions never lowers risk. Every quote must be grounded (a verbatim substring of
the searched text) or it is dropped.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from safeplate.allergen_prior import NUTS, PEANUTS, TREE_NUTS
from safeplate.allergen_score import CommunitySignal
from safeplate.config import get_cache_dir
from safeplate.gemini_menu import GeminiMenuError
from safeplate.menu_text import MenuItemRecord

_CACHE_VERSION = "1"
_CACHE_TTL = 7 * 24 * 60 * 60
_MAX_SNIPPET_CHARS = 6000
_DISH_CONFIDENCE = 0.3   # inference from reviews -> low; feeds the dish-name prior only

_CLASSIFY_SYSTEM = (
    "You are given web-search snippets (reviews, blogs, forum posts) ABOUT ONE named "
    "restaurant. Extract ONLY what is explicitly stated. Two jobs:\n"
    "1) ALLERGY HANDLING: any statement about how the restaurant handles food "
    "allergies. Classify each as: 'adverse_event' (a diner had an allergic reaction), "
    "'allergen_presence' (an allergen is confirmed present in a dish), 'poor_handling' "
    "(careless/unsafe for allergies), 'good_handling' (accommodating/careful). Name the "
    "allergen if stated. Copy a VERBATIM quote for each.\n"
    "2) DISHES: list dish names diners mention by name (e.g. 'Cashew Chicken'). Names "
    "only, no commentary.\n"
    "Never invent restaurants, dishes, allergens, or quotes. If a snippet is about a "
    "different restaurant or has nothing relevant, ignore it. Empty arrays are fine."
)

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "handling": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": [
                        "adverse_event", "allergen_presence", "poor_handling",
                        "good_handling", "none"]},
                    "allergen": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["type", "quote"],
            },
        },
        "dishes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["handling", "dishes"],
}


@dataclass
class CommunityResult:
    signals: list[CommunitySignal] = field(default_factory=list)
    dishes: list[MenuItemRecord] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)  # grounded handling quotes, for display


def fetch_community_signals(
    *,
    restaurant_name: str,
    address: str | None,
    allergen: str = NUTS,
    user_agent: str,
    brave_api_key: str | None,
    gemini_api_key: str | None,
    gemini_model: str,
    want_dishes: bool = False,
) -> CommunityResult:
    """Web-search + LLM-classify community allergy signals (always) and dish mentions
    (only when ``want_dishes`` -- i.e. no menu was found). Cached by restaurant; fails
    closed to an empty result so it can never break the caller."""
    if not restaurant_name.strip() or not brave_api_key or not gemini_api_key:
        return CommunityResult()

    cached = _load_cache(restaurant_name, address, want_dishes)
    if cached is not None:
        return cached

    try:
        snippets, urls = _search(
            restaurant_name=restaurant_name, address=address,
            api_key=brave_api_key, user_agent=user_agent, want_dishes=want_dishes,
        )
    except Exception:
        return CommunityResult()
    if not snippets.strip():
        result = CommunityResult()
        _save_cache(restaurant_name, address, want_dishes, result)
        return result

    try:
        parsed = _classify(snippets, api_key=gemini_api_key, model=gemini_model)
    except GeminiMenuError:
        return CommunityResult()  # don't cache a transient LLM failure

    result = _build_result(
        parsed, snippets=snippets, urls=urls,
        restaurant_name=restaurant_name, want_dishes=want_dishes,
    )
    _save_cache(restaurant_name, address, want_dishes, result)
    return result


# --------------------------------------------------------------------------- #
def _city(address: str | None) -> str | None:
    if not address:
        return None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    return parts[1] if len(parts) >= 2 else None


def _search(
    *, restaurant_name: str, address: str | None, api_key: str,
    user_agent: str, want_dishes: bool,
) -> tuple[str, list[str]]:
    """Return (combined snippet text, source urls). Queries fire concurrently through
    the shared Brave token bucket (which enforces the per-second rate limit)."""
    from safeplate.brave_search import brave_web_search
    from safeplate.concurrency import map_concurrent

    city = _city(address)
    loc = f' "{city}"' if city else ""
    queries = [f'"{restaurant_name}"{loc} allergy OR allergic OR "cross contamination" OR nut']
    if want_dishes:
        queries.append(f'"{restaurant_name}"{loc} best dishes OR popular OR ordered')

    def _run(q: str) -> list:
        try:
            return brave_web_search(query=q, api_key=api_key, user_agent=user_agent, count=6)
        except Exception:
            return []

    chunks: list[str] = []
    urls: list[str] = []
    seen: set[str] = set()
    for results in map_concurrent(_run, queries, max_workers=len(queries)):
        for r in results:
            if r.url in seen:
                continue
            seen.add(r.url)
            parts = [r.title or "", r.description or ""] + list(r.extra_snippets or [])
            blob = " ".join(p for p in parts if p)
            # SAFETY: drop snippets that aren't clearly about THIS restaurant, so a
            # query like '"Golden Diner" allergy' can't pull in "Golden Corral" or a
            # generic city allergy guide and misattribute another place's info.
            if not _mentions(blob, restaurant_name):
                continue
            urls.append(r.url)
            chunks.append(blob)
    return " ".join(chunks)[:_MAX_SNIPPET_CHARS], urls


def _mentions(text: str, name: str) -> bool:
    """Strict relevance guard: the snippet must contain the full compacted restaurant
    name OR ALL of its >=3-char tokens (not just one common word like 'golden')."""
    low = " ".join((text or "").lower().split())
    if not low:
        return False
    compact = "".join(name.lower().split())
    if compact and compact in low.replace(" ", ""):
        return True
    tokens = [t for t in name.lower().split() if len(t) >= 3]
    return bool(tokens) and all(t in low for t in tokens)


def _classify(snippets: str, *, api_key: str, model: str) -> dict[str, Any]:
    from safeplate.extraction2.interpret_llm import _call_with_retry

    request = {
        "system_instruction": {"parts": [{"text": _CLASSIFY_SYSTEM}]},
        "contents": [{"parts": [{"text": "Web snippets:\n\n" + snippets}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": _CLASSIFY_SCHEMA,
        },
    }
    return _call_with_retry(request, api_key=api_key, model=model)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _allergen_key(raw: str | None) -> str | None:
    low = (raw or "").lower()
    if not low:
        return None
    if "peanut" in low or "groundnut" in low:
        return PEANUTS
    if any(t in low for t in ("tree nut", "almond", "cashew", "walnut", "pecan",
                              "hazelnut", "pistachio", "macadamia", "pine nut")):
        return TREE_NUTS
    if "nut" in low:
        return NUTS
    return low  # non-nut allergen -> scorer treats as a (mismatch) note for a nut user


def _build_result(
    parsed: dict[str, Any], *, snippets: str, urls: list[str],
    restaurant_name: str, want_dishes: bool,
) -> CommunityResult:
    grounded = _normalize(snippets)
    primary_url = urls[0] if urls else ""
    out = CommunityResult()

    for entry in parsed.get("handling", []):
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type", "")).strip().lower()
        quote = str(entry.get("quote", "")).strip()
        if kind in ("", "none") or not quote:
            continue
        if _normalize(quote) not in grounded:  # drop ungrounded / hallucinated quotes
            continue
        out.signals.append(CommunitySignal(
            type=kind, allergen=_allergen_key(entry.get("allergen")),
            quote=quote[:240], source="web_search", url=primary_url,
        ))
        if kind in ("adverse_event", "allergen_presence", "good_handling"):
            out.quotes.append(quote[:240])

    if want_dishes:
        now = datetime.now(timezone.utc).isoformat()
        seen: set[str] = set()
        for dish in parsed.get("dishes", []):
            name = str(dish).strip()
            key = name.lower()
            if not name or key in seen or len(name) > 80:
                continue
            seen.add(key)
            out.dishes.append(MenuItemRecord(
                restaurant_name=restaurant_name, restaurant_source_id="",
                menu_source_url="(mentioned in reviews)", category="",
                item_name=name, description="", price="",
                dietary_terms=[], allergen_terms=[], source_type="community",
                extraction_method="community_mention", confidence=_DISH_CONFIDENCE,
                raw_text="", fetched_at=now,
            ))
    return out


# --------------------------------------------------------------------------- #
def _cache_path(restaurant_name: str, address: str | None, want_dishes: bool):
    key = hashlib.sha1(
        f"{_CACHE_VERSION}:{restaurant_name}:{address or ''}:{int(want_dishes)}".encode("utf-8")
    ).hexdigest()
    return get_cache_dir() / "community_signals" / f"{key}.json"


def _load_cache(restaurant_name: str, address: str | None, want_dishes: bool) -> CommunityResult | None:
    try:
        blob = json.loads(_cache_path(restaurant_name, address, want_dishes).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - blob.get("at", 0) > _CACHE_TTL:
        return None
    try:
        return CommunityResult(
            signals=[CommunitySignal(**s) for s in blob.get("signals", [])],
            dishes=[MenuItemRecord(**d) for d in blob.get("dishes", [])],
            quotes=list(blob.get("quotes", [])),
        )
    except (TypeError, KeyError):
        return None


def _save_cache(restaurant_name: str, address: str | None, want_dishes: bool, result: CommunityResult) -> None:
    from dataclasses import asdict

    path = _cache_path(restaurant_name, address, want_dishes)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "at": time.time(),
            "signals": [asdict(s) for s in result.signals],
            "dishes": [asdict(d) for d in result.dishes],
            "quotes": result.quotes,
        }), encoding="utf-8")
    except OSError:
        pass
