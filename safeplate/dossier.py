"""Deep-Dive Dossier (prototype) -- a SINGLE-restaurant deep dive.

Instead of scanning ~12 nearby places, the user points at one restaurant and we
spend the full budget going deep: resolve it, run the production extraction +
scoring + community pipeline, add a "deeper site" scan (about / FAQ / allergy
pages + social links), and stream the crawl live as Server-Sent Events before
presenting a rich safety **dossier**.

This module is PURELY ADDITIVE. It reuses the production pipeline wholesale
(``search_service.run_restaurant_search`` for resolution,
``menu_service.run_menu_extraction`` for the deep extract/score/community, and
``extraction2.allergy_signals`` for the deeper-site lever) and never touches the
existing request paths. See ``docs/superpowers/specs/2026-07-06-deep-dive-dossier-design.md``.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urljoin, urlparse

from safeplate.config import (
    get_gemini_api_key,
    get_gemini_model,
    get_google_places_api_key,
    get_user_agent,
)
from safeplate.menu_service import run_menu_extraction
from safeplate.search_service import run_restaurant_search


def _f(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── SSE framing ────────────────────────────────────────────────────────────────

def _sse(event: str, data: Any) -> str:
    """Frame one Server-Sent Event. ``json.dumps`` keeps the data on one line so a
    stray newline can't split the SSE ``data:`` field."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Query params ────────────────────────────────────────────────────────────────

def params_from_query(query: str) -> dict[str, Any]:
    """Parse the dossier stream's query string into the params dict the orchestrator
    reads. EventSource is GET-only, so the target + profile ride as query params.
    Kept here (not in the HTTP handler) so it's unit-testable."""
    q = parse_qs(query or "", keep_blank_values=False)

    def one(key: str) -> str:
        vals = q.get(key)
        return vals[0].strip() if vals else ""

    params: dict[str, Any] = {
        "name": one("name"),
        "location": one("location"),
        "url": one("url"),
        "provider": one("provider") or "auto",
        "severity": one("severity") or "allergy",
        "scoringEngine": one("engine") or "rules",
    }
    cross = one("crossContact")
    if cross:
        params["crossContact"] = cross
    nut = one("nutTypes")
    if nut:
        params["nutTypes"] = [t for t in re.split(r"[,\s]+", nut) if t]
    radius = one("radius")
    if radius:
        params["radius"] = radius
    # Chosen-candidate / device-location extras (the dropdown supplies these so we
    # deep-dive the EXACT place picked, skipping a second ambiguous resolve).
    for key in ("website", "address", "lat", "lon", "phone", "rating", "reviewCount"):
        val = one(key)
        if val:
            params[key] = val
    return params


# ── Candidate lookup (the pick-a-location dropdown) ──────────────────────────────

def find_candidates(params: dict[str, Any], *, demo_mode: bool = False, limit: int = 8) -> list[dict[str, Any]]:
    """Return matching restaurants for a typed name within the presumed area, for the
    dropdown. Prefers Google Places Text Search (finds a specific chain like "Taco
    Bell" reliably); falls back to nearby-prior + name filter for other providers.
    Area = a typed ``location`` (geocoded) if given, else device ``lat``/``lon``."""
    name = str(params.get("name") or "").strip()
    if len(name) < 2:
        return []
    location = str(params.get("location") or "").strip()
    lat = _f(params.get("lat"))
    lon = _f(params.get("lon"))
    user_agent = get_user_agent()

    if location:  # a typed area overrides device location
        try:
            from safeplate.geo import geocode_location

            coord = geocode_location(location, user_agent=user_agent)
            lat, lon = coord.latitude, coord.longitude
        except Exception:
            pass

    api_key = get_google_places_api_key()
    if api_key:
        try:
            from safeplate.providers.google_places import text_search_restaurants

            records = text_search_restaurants(
                query=name, latitude=lat, longitude=lon,
                api_key=api_key, user_agent=user_agent, limit=limit,
            )
            if records:
                return [_candidate_from_record(r) for r in records][:limit]
        except Exception:
            pass  # fall through to the nearby filter

    return _nearby_candidates(name, location=location, lat=lat, lon=lon,
                              demo_mode=demo_mode, limit=limit)


def _candidate_from_record(rec: Any) -> dict[str, Any]:
    dm = getattr(rec, "distance_meters", None)
    return {
        "name": rec.name or "",
        "address": rec.address or "",
        "website": rec.website_url or "",
        "lat": rec.latitude,
        "lon": rec.longitude,
        "distanceKm": round(dm / 1000, 1) if isinstance(dm, (int, float)) and dm == dm and dm != float("inf") else None,
        "rating": rec.rating,
        "reviewCount": rec.review_count,
        "sourceId": rec.source_id or "",
    }


def _nearby_candidates(name: str, *, location: str, lat: float | None, lon: float | None,
                       demo_mode: bool, limit: int) -> list[dict[str, Any]]:
    """Fallback for non-Google providers: nearby-prior search + name filter."""
    payload: dict[str, Any] = {"provider": "auto", "listMode": "prior", "limit": 20, "radius": 8000}
    if location:
        payload["location"] = location
    elif lat is not None and lon is not None:
        payload["latitude"] = lat
        payload["longitude"] = lon
    else:
        return []
    try:
        search = run_restaurant_search(payload, demo_mode=demo_mode)
    except Exception:
        return []
    target = _norm(name)
    out: list[dict[str, Any]] = []
    for row in search.get("rows") or []:
        rn = _norm(row.get("name") or "")
        if not rn:
            continue
        if target in rn or rn in target or rn.startswith(target) or target.startswith(rn):
            dm = row.get("distance_meters")
            out.append({
                "name": row.get("name") or "",
                "address": row.get("address") or "",
                "website": row.get("website_url") or "",
                "lat": row.get("latitude"),
                "lon": row.get("longitude"),
                "distanceKm": round(dm / 1000, 1) if isinstance(dm, (int, float)) else None,
                "rating": row.get("rating"),
                "reviewCount": row.get("review_count"),
                "sourceId": row.get("source_id") or "",
            })
    return out[:limit]


# ── Stage 1: resolve one restaurant ─────────────────────────────────────────────

@dataclass
class Target:
    name: str
    website_url: str
    address: str = ""
    categories: list[str] = field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    phone: str = ""
    rating: float | None = None
    review_count: int | None = None
    resolved_via: str = "places"  # "places" | "url"


def _norm(text: str) -> str:
    """Fold a name to alphanumerics only (drop spaces/punctuation) so "Nando's",
    "Nandos", and "nando s" all compare equal."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _name_from_url(url: str) -> str:
    host = urlparse(_ensure_scheme(url)).netloc.lower()
    host = re.sub(r"^www\.", "", host)
    return host.split(".")[0].replace("-", " ").title() if host else "Restaurant"


def _ensure_scheme(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^https?://", url, re.I):
        return "https://" + url
    return url


def _best_name_match(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Pick the row whose name best matches the typed restaurant name. Rows arrive
    distance-sorted, so within a match tier the first (nearest) wins. Returns None
    when nothing plausibly matches (better to ask for a URL than guess wrong)."""
    if not name:
        return None
    target = _norm(name)
    if not target:
        return None
    exact: list[dict] = []
    prefix: list[dict] = []
    contains: list[dict] = []
    for row in rows:
        rn = _norm(row.get("name") or "")
        if not rn:
            continue
        if rn == target:
            exact.append(row)
        elif rn.startswith(target) or target.startswith(rn):
            prefix.append(row)
        elif target in rn or rn in target:
            contains.append(row)
    for bucket in (exact, prefix, contains):
        if bucket:
            return bucket[0]
    return None


def build_target(params: dict[str, Any], *, demo_mode: bool = False) -> Target | None:
    """Resolve name+location (or a direct URL) to ONE restaurant. Reuses the
    production search in cheap ``listMode:"prior"`` (no extraction) and name-matches
    the result. A direct URL bypasses resolution entirely."""
    name = str(params.get("name") or "").strip()
    location = str(params.get("location") or "").strip()
    url = _ensure_scheme(str(params.get("url") or params.get("website") or params.get("websiteUrl") or "").strip())
    address = str(params.get("address") or "").strip()

    # A chosen candidate (rich details from the dropdown) OR a pasted URL short-circuits
    # resolution -- we already know the exact place, so trust it and skip a second Places
    # call. A candidate carries an address; a bare URL does not.
    if url or address:
        return Target(
            name=name or (_name_from_url(url) if url else "Restaurant"),
            website_url=url,
            address=address,
            categories=list(params.get("categories") or []),
            latitude=_f(params.get("lat")),
            longitude=_f(params.get("lon")),
            phone=str(params.get("phone") or ""),
            rating=_f(params.get("rating")),
            review_count=_i(params.get("reviewCount")),
            resolved_via="places" if address else "url",
        )

    if not location:
        return None  # need a location, a candidate, or a URL to go on

    try:
        radius = int(str(params.get("radius") or "2000"))
    except ValueError:
        radius = 2000
    search = run_restaurant_search(
        {
            "location": location,
            "provider": params.get("provider") or "auto",
            "listMode": "prior",  # instant prior cards, NO menu extraction
            "limit": 20,
            "radius": max(200, min(radius, 20000)),
        },
        demo_mode=demo_mode,
    )
    row = _best_name_match(search.get("rows") or [], name)
    if row is None:
        # Couldn't match by name -- fall back to a supplied URL if there was one.
        if url:
            return Target(name=name or _name_from_url(url), website_url=url, resolved_via="url")
        return None
    return Target(
        name=row.get("name") or name,
        website_url=_ensure_scheme(row.get("website_url") or url or ""),
        address=row.get("address") or "",
        categories=list(row.get("categories") or []),
        latitude=row.get("latitude"),
        longitude=row.get("longitude"),
        phone=row.get("phone_number") or "",
        rating=row.get("rating"),
        review_count=row.get("review_count"),
        resolved_via="places",
    )


# ── Stage 3: the deeper-site lever (NEW) ────────────────────────────────────────

# Link tokens that tend to lead to allergy-handling prose (about/FAQ/allergy pages).
_DEEPER_LINK_RE = re.compile(r"allerg|about|faq|contact|dietary|nutrition|policies|info", re.I)
_SOCIAL_RE = re.compile(r"(instagram\.com|facebook\.com|tiktok\.com|twitter\.com|x\.com)/", re.I)
# Cheap pre-filter: only spend an LLM call on a page that actually mentions allergies.
_ALLERGY_TEXT_RE = re.compile(r"allerg|cross.?contam|nut[\s-]?free|gluten|dietary|intoleran|peanut|tree nut", re.I)


@dataclass
class DeeperSiteSignal:
    url: str
    statements: list[str]
    allergy_friendly_claim: bool
    cross_contact_warning: bool
    ask_staff: bool
    allergen_menu_available: bool
    nut_free_claim: bool


@dataclass
class DeeperSite:
    pages_scanned: list[str] = field(default_factory=list)
    signals: list[DeeperSiteSignal] = field(default_factory=list)
    social_links: list[str] = field(default_factory=list)
    error: str = ""


def _abs(base: str, href: str) -> str:
    try:
        return urljoin(base, href)
    except ValueError:
        return href


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def scan_deeper_site(
    website_url: str,
    *,
    user_agent: str,
    api_key: str | None,
    model: str,
    max_pages: int = 3,
) -> DeeperSite:
    """Fetch the homepage, follow a few internal about/FAQ/allergy links, and mine
    grounded allergy-handling statements from them (reusing the production
    ``extract_allergy_signals`` grounding). Also collects social links. Best-effort:
    any failure is recorded in ``error`` and never raises -- the dossier degrades,
    it doesn't break, and it NEVER moves the verdict toward 'safe'."""
    from safeplate.page_fetch import fetch_html_page
    from safeplate.soup import make_soup

    result = DeeperSite()
    if not website_url:
        result.error = "No website to scan."
        return result

    try:
        home = fetch_html_page(website_url, user_agent=user_agent, fetch_mode="auto")
    except Exception as exc:  # robots block, DNS, timeout, JS-only, ...
        result.error = f"Couldn't load the site ({type(exc).__name__})."
        return result

    soup = make_soup(home.html)
    base_host = urlparse(home.final_url).netloc

    # Social links (surfaced, not crawled -- IG/FB are bot-walled).
    socials = [
        _abs(home.final_url, a["href"])
        for a in soup.find_all("a", href=True)
        if _SOCIAL_RE.search(a["href"])
    ]
    result.social_links = _dedupe(socials)[:6]

    # Candidate internal pages: same host, allergy-ish anchor text/href, not a PDF
    # (matrices are already handled by the extraction stage).
    candidates: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True)
        if not (_DEEPER_LINK_RE.search(href) or _DEEPER_LINK_RE.search(text)):
            continue
        absu = _abs(home.final_url, href).split("#")[0]
        pu = urlparse(absu)
        if pu.scheme not in ("http", "https") or pu.netloc != base_host:
            continue
        if absu.lower().endswith(".pdf"):
            continue
        candidates.append(absu)
    picked = _dedupe(candidates)[:max_pages]

    # Homepage first, then the picked internal pages.
    pages: list[tuple[str, str]] = [(home.final_url, home.html)]
    for url in picked:
        try:
            page = fetch_html_page(url, user_agent=user_agent, fetch_mode="static")
            pages.append((page.final_url, page.html))
        except Exception:
            continue

    if not api_key:
        result.error = "Deeper-site scan needs a Gemini key (grounded quotes); skipped."
        result.pages_scanned = [u for u, _ in pages]
        return result

    from safeplate.extraction2.allergy_signals import extract_allergy_signals
    from safeplate.extraction2.schema import Payload, PayloadKind

    for url, html in pages[: max_pages + 1]:
        result.pages_scanned.append(url)
        visible = make_soup(html).get_text(" ", strip=True)
        if not _ALLERGY_TEXT_RE.search(visible):
            continue  # cheap pre-filter: no allergy prose -> no LLM call
        payload = Payload(url=url, source_type="website_link", kind=PayloadKind.TEXT, text=html)
        try:
            sig = extract_allergy_signals(payload, api_key=api_key, model=model)
        except Exception:
            continue
        if sig is None:
            continue
        result.signals.append(
            DeeperSiteSignal(
                url=sig.url,
                statements=list(sig.statements),
                allergy_friendly_claim=sig.allergy_friendly_claim,
                cross_contact_warning=sig.cross_contact_warning,
                ask_staff=sig.ask_staff,
                allergen_menu_available=sig.allergen_menu_available,
                nut_free_claim=sig.nut_free_claim,
            )
        )
    return result


# ── Assemble ────────────────────────────────────────────────────────────────────

def _menu_payload(target: Target, params: dict[str, Any]) -> dict[str, Any]:
    """Shape the target + profile into the payload ``run_menu_extraction`` reads.
    Profile defaults to nuts (the legacy nuts-only path stays byte-identical)."""
    payload: dict[str, Any] = {
        "name": target.name,
        "websiteUrl": target.website_url,
        "address": target.address,
        "categories": target.categories,
        "latitude": target.latitude,
        "longitude": target.longitude,
        "scoringEngine": params.get("scoringEngine") or "rules",
        "severity": params.get("severity") or "allergy",
    }
    if params.get("crossContact"):
        payload["crossContact"] = params["crossContact"]
    if params.get("nutTypes"):
        payload["nutTypes"] = params["nutTypes"]
    return payload


def assemble_dossier(
    *, target: Target, extraction: dict[str, Any], deeper: DeeperSite, elapsed: float
) -> dict[str, Any]:
    """Fold the target + extraction + deeper-site scan into the dossier payload the
    page renders. Missing/failed extraction degrades to an unverified verdict
    (``verified: false``) -- never a fabricated 'safe'."""
    summary = (extraction or {}).get("summary") or {}
    mbr = summary.get("menuBackedRisk") or {}
    item_count = int(summary.get("itemCount") or 0)
    riskiest = mbr.get("riskiestItems") or []

    verdict = {
        "tier": summary.get("tier") or "unknown",
        "risk": summary.get("overallRisk"),
        "confidence": summary.get("overallConfidence"),
        "basis": summary.get("evidenceBasis") or "",
        "rationale": mbr.get("rationale") or [],
        "coverageStatus": summary.get("coverageStatus") or "unknown",
        "verified": bool(summary),  # false => extraction failed; UI says "couldn't verify"
    }
    return {
        "header": {
            "name": target.name,
            "address": target.address,
            "website": target.website_url,
            "phone": target.phone,
            "rating": target.rating,
            "reviewCount": target.review_count,
            "cuisines": target.categories,
            "resolvedVia": target.resolved_via,
        },
        "verdict": verdict,
        "dishes": {
            "watch": riskiest,
            "parsedCount": item_count,
            "otherCount": max(0, item_count - len(riskiest)),
            "evidence": mbr.get("evidence") or [],
        },
        "restaurantSignals": summary.get("restaurantSignals") or {},
        "deeperSite": {
            "pagesScanned": deeper.pages_scanned,
            "signals": [asdict(s) for s in deeper.signals],
            "socialLinks": deeper.social_links,
            "error": deeper.error,
        },
        "community": (extraction or {}).get("communityQuotes") or [],
        "provenance": {
            "coverage": (extraction or {}).get("coverage") or [],
            "regionNotice": summary.get("regionNotice"),
            "locationNotice": summary.get("locationNotice"),
            "perAllergen": summary.get("perAllergen") or [],
        },
        "elapsedMs": int(elapsed * 1000),
    }


# ── Orchestrator: the streaming crawl ───────────────────────────────────────────

def iter_dossier_events(params: dict[str, Any], *, demo_mode: bool = False) -> Iterator[str]:
    """Run the deep dive as real, coarse stages, yielding SSE frames as each starts
    and finishes. The verdict comes ONLY from the extraction stage; a failed
    deeper-site scan is reported but can never move the verdict toward 'safe'."""
    t0 = time.monotonic()
    try:
        yield _sse("start", {"name": params.get("name"), "location": params.get("location")})

        # Stage 1 -- resolve.
        yield _sse("stage_start", {"key": "resolve", "label": "Finding the restaurant"})
        try:
            target = build_target(params, demo_mode=demo_mode)
        except ValueError as exc:
            yield _sse("error", {"message": str(exc)})
            return
        except Exception as exc:
            yield _sse("error", {"message": f"Couldn't resolve the restaurant ({type(exc).__name__})."})
            return
        if target is None or not (target.website_url or target.name):
            yield _sse(
                "error",
                {"message": "Couldn't find that restaurant. Pick one from the list, add the city, or paste the website URL."},
            )
            return
        yield _sse(
            "stage_done",
            {"key": "resolve", "summary": {"name": target.name, "address": target.address,
                                            "website": target.website_url, "via": target.resolved_via}},
        )

        # Stage 2 -- deep extract (menu + allergen chart + community + scoring), one call.
        yield _sse(
            "stage_start",
            {"key": "deep_extract", "label": "Reading the menu, allergen chart & community mentions"},
        )
        extraction: dict[str, Any] = {}
        try:
            extraction = run_menu_extraction(_menu_payload(target, params), demo_mode=demo_mode)
        except Exception as exc:
            yield _sse("stage_error", {"key": "deep_extract", "message": f"Extraction failed ({type(exc).__name__})."})
        summary = (extraction or {}).get("summary") or {}
        yield _sse(
            "stage_done",
            {"key": "deep_extract", "summary": {
                "itemCount": summary.get("itemCount", 0),
                "tier": summary.get("tier"),
                "quotes": len((extraction or {}).get("communityQuotes") or []),
            }},
        )

        # Stage 3 -- deeper-site scan (new lever). Best-effort.
        yield _sse(
            "stage_start",
            {"key": "deeper_site", "label": "Scanning about / FAQ / allergy pages & socials"},
        )
        try:
            deeper = scan_deeper_site(
                target.website_url,
                user_agent=get_user_agent(),
                api_key=get_gemini_api_key(),
                model=get_gemini_model(),
            )
        except Exception as exc:
            deeper = DeeperSite(error=f"{type(exc).__name__}")
        if deeper.error:
            yield _sse("stage_error", {"key": "deeper_site", "message": deeper.error})
        yield _sse(
            "stage_done",
            {"key": "deeper_site", "summary": {
                "pages": len(deeper.pages_scanned),
                "signals": len(deeper.signals),
                "socials": len(deeper.social_links),
            }},
        )

        # Assemble + emit the dossier.
        dossier = assemble_dossier(
            target=target, extraction=extraction, deeper=deeper, elapsed=time.monotonic() - t0
        )
        yield _sse("dossier", dossier)
        yield _sse("done", {"elapsedMs": int((time.monotonic() - t0) * 1000)})
    except Exception as exc:  # last-resort guard: never leak a traceback into the stream
        yield _sse("error", {"message": f"Unexpected error ({type(exc).__name__})."})


# ── Page template (hot-reloaded, mirrors api_server.app_html) ────────────────────

_TEMPLATE_PATH = Path(__file__).resolve().parent / "dossier_template.html"
_html_cache: dict[str, Any] = {"mtime": None, "html": ""}
_html_lock = threading.Lock()


def dossier_html() -> str:
    """Serve the dossier page, re-reading it when the file changes so edits show on a
    plain refresh (mirrors ``api_server.app_html``)."""
    with _html_lock:
        try:
            mtime = _TEMPLATE_PATH.stat().st_mtime
            if mtime != _html_cache["mtime"]:
                _html_cache["html"] = _TEMPLATE_PATH.read_text(encoding="utf-8")
                _html_cache["mtime"] = mtime
        except OSError:
            pass
        return _html_cache["html"]
