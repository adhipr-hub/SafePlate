"""Search service: nearby-restaurant lookup (provider fetch + geocoding) and the
ranked result cards (prior-only + the menu-backed list, which reuses the menu
service's extraction/scoring). Depends on common + menu_service only."""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from safeplate.common import (
    DATA_DIR,
    _bounded_int,
    _default_provider,
    _is_ai_engine,
    _scoring_engine_from_payload,
    _severity_from_str,
    _user_profile_from_payload,
)
from safeplate.config import (
    get_geoapify_api_key,
    get_gemini_api_key,
    get_gemini_model,
    get_google_places_api_key,
    get_user_agent,
)
from safeplate.demo_fixtures import load_demo_search
from safeplate.export import build_output_paths, write_csv, write_json
from safeplate.geo import Coordinates, geocode_location
from safeplate.menu_service import _menu_backed_card, _write_assessment_into_card
from safeplate.providers.geoapify import GEOAPIFY_CATEGORIES
from safeplate.providers.geoapify import fetch_nearby_restaurants as fetch_geoapify
from safeplate.providers.google_places import GOOGLE_INCLUDED_TYPES
from safeplate.providers.google_places import fetch_nearby_restaurants as fetch_google
from safeplate.providers.osm import fetch_nearby_restaurants as fetch_osm
from safeplate.quality import build_quality_summary, write_quality_summary


def _persist_search_outputs(payload: dict[str, Any]) -> bool:
    """Write the JSON/CSV/summary files only when explicitly requested -- per-request
    `persist: true` or the SAFEPLATE_PERSIST_SEARCH env flag (for CLI/debug runs)."""
    if payload.get("persist"):
        return True
    return os.environ.get("SAFEPLATE_PERSIST_SEARCH", "").strip().lower() in ("1", "true", "yes")


def run_restaurant_search(payload: dict[str, Any], *, demo_mode: bool = False) -> dict[str, Any]:
    if demo_mode:
        return _run_demo_restaurant_search(payload)

    provider = str(payload.get("provider") or _default_provider()).strip().lower()
    if provider == "auto":
        provider = _default_provider()
    if provider not in ["google", "osm", "geoapify"]:
        raise ValueError("Provider must be google, osm, geoapify, or auto")
    severity = str(payload.get("severity") or "allergy")

    radius = _bounded_int(payload.get("radius"), default=1500, minimum=100, maximum=50000)
    limit = _bounded_int(payload.get("limit"), default=20, minimum=1, maximum=50)
    user_agent = get_user_agent()
    location_label, coordinates = _coordinates_from_payload(payload, user_agent)
    rows = _fetch_rows_for_provider(
        provider=provider,
        coordinates=coordinates,
        radius=radius,
        limit=limit,
        user_agent=user_agent,
    )

    summary = build_quality_summary(
        rows=rows,
        location=location_label,
        radius_meters=radius,
        limit=limit,
        provider=provider,
    )
    # Persisting JSON/CSV/summary to disk is CLI/debug heritage -- skip it on every
    # interactive request (latency + data/ clutter). Opt in via the request or env.
    files: dict[str, str] = {}
    if _persist_search_outputs(payload):
        json_path, csv_path, summary_path = build_output_paths(location_label, DATA_DIR)
        write_json(json_path, rows)
        write_csv(csv_path, rows)
        write_quality_summary(summary_path, summary)
        files = {"json": str(json_path), "csv": str(csv_path), "summary": str(summary_path)}

    # Progressive first paint: "prior" returns instant cuisine-prior cards (no
    # extraction) so the page is usable in <1s; the client upgrades each to
    # menu-backed via /api/menu in the background. Default stays menu-backed (one
    # batched response) so the existing "normal" flow is unchanged.
    list_mode = str(payload.get("listMode") or "menu_backed").strip().lower()
    if list_mode == "prior":
        cards = [_restaurant_payload(row, severity=severity) for row in rows]
    else:
        cards = _build_search_cards(rows, payload, severity=severity)

    return {
        "location": location_label,
        "coordinates": asdict(coordinates),
        "provider": provider,
        "radius": radius,
        "limit": limit,
        "listMode": list_mode,
        "rows": cards,
        "summary": summary,
        "files": files,
    }


def _run_demo_restaurant_search(payload: dict[str, Any]) -> dict[str, Any]:
    fixture = load_demo_search()
    severity = str(payload.get("severity") or "allergy")
    radius = _bounded_int(
        payload.get("radius"),
        default=fixture.radius,
        minimum=100,
        maximum=50000,
    )
    limit = _bounded_int(
        payload.get("limit"),
        default=fixture.limit,
        minimum=1,
        maximum=50,
    )
    rows = fixture.restaurants[:limit]
    summary = build_quality_summary(
        rows=rows,
        location=fixture.location,
        radius_meters=radius,
        limit=limit,
        provider="demo",
    )
    summary["demoMode"] = True
    summary["demoScenarios"] = [
        str(row.raw_payload.get("demo_scenario", "")) for row in rows
    ]
    return {
        "location": fixture.location,
        "coordinates": fixture.coordinates,
        "provider": "demo",
        "radius": radius,
        "limit": limit,
        "rows": [_restaurant_payload(row, severity=severity) for row in rows],
        "summary": summary,
        "files": {},
        "demoMode": True,
    }


def _coordinates_from_payload(
    payload: dict[str, Any],
    user_agent: str,
) -> tuple[str, Coordinates]:
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if latitude not in [None, ""] and longitude not in [None, ""]:
        coordinates = Coordinates(latitude=float(latitude), longitude=float(longitude))
        label = str(payload.get("location") or "browser_location").strip()
        return label or "browser_location", coordinates

    location = str(payload.get("location") or "").strip()
    if not location:
        raise ValueError("Enter a location or use browser location.")
    return location, geocode_location(location, user_agent=user_agent)


def _fetch_rows_for_provider(
    *,
    provider: str,
    coordinates: Coordinates,
    radius: int,
    limit: int,
    user_agent: str,
):
    if provider == "google":
        api_key = get_google_places_api_key()
        if not api_key:
            raise ValueError(
                "GOOGLE_PLACES_API_KEY is not set. Set it before starting the app, or use OSM."
            )
        return fetch_google(
            latitude=coordinates.latitude,
            longitude=coordinates.longitude,
            radius_meters=radius,
            limit=limit,
            api_key=api_key,
            user_agent=user_agent,
            included_types=GOOGLE_INCLUDED_TYPES,
        )

    if provider == "geoapify":
        api_key = get_geoapify_api_key()
        if not api_key:
            raise ValueError(
                "GEOAPIFY_API_KEY is not set. Set it before starting the app, or use OSM."
            )
        return fetch_geoapify(
            latitude=coordinates.latitude,
            longitude=coordinates.longitude,
            radius_meters=radius,
            limit=limit,
            api_key=api_key,
            user_agent=user_agent,
            categories=GEOAPIFY_CATEGORIES,
            conditions=[],
        )

    return fetch_osm(
        latitude=coordinates.latitude,
        longitude=coordinates.longitude,
        radius_meters=radius,
        limit=limit,
        user_agent=user_agent,
    )


def _restaurant_payload(row: Any, *, severity: str = "allergy") -> dict[str, Any]:
    """Prior-only card (no live menu fetch): the cuisine/location verdict from the
    same Layer #5 scorer the drawer uses, so the list and drawer speak one tier
    language. Used for the farther/degraded cards; opening one runs the full
    extraction and upgrades it to menu-backed."""
    from safeplate.allergen_prior import (
        normalize_cuisine,
        region_from_address,
        score_restaurant_prior,
    )
    from safeplate.allergen_score import UserProfile, score_restaurant_for_user

    payload = asdict(row)
    payload["categories"] = row.categories
    cuisines = normalize_cuisine(row.categories)
    region = region_from_address(
        row.address, latitude=row.latitude, longitude=row.longitude
    )
    # labeling_trust is exposed by the prior (not the assessment); compute it for the
    # UI's "allergen labeling" badge.
    prior = score_restaurant_prior(cuisines=cuisines, region=region, allergen="nuts")
    assessment = score_restaurant_for_user(
        UserProfile.for_nuts(_severity_from_str(severity)),
        cuisines=cuisines, region=region,
    )
    payload["allergenPrior"] = {
        "allergen": "nuts",
        "risk": round(assessment.overall_risk, 3),
        "confidence": round(assessment.overall_confidence, 2),
        "basis": assessment.evidence_basis,
        "rationale": assessment.rationale,
        "tier": assessment.tier,
        "labelingTrust": round(prior.labeling_trust, 2),
        "cuisines": cuisines,
        "region": region,
    }
    payload["coverageStatus"] = "cuisine_estimate"
    if isinstance(row.raw_payload, dict) and row.raw_payload.get("demo_scenario"):
        payload["demoScenario"] = row.raw_payload["demo_scenario"]
    return payload


# Concurrency for the menu-backed list. Front-loads ALL nearest-N restaurants at once
# (default 12 = the page size) now that PDF parsing is bounded (PyMuPDF + page caps)
# and Brave is concurrent under a shared rate limiter, so extractions finish within
# budget instead of trickling 4-at-a-time. Gemini is still globally capped by
# SAFEPLATE_GEMINI_CONCURRENCY; lower SAFEPLATE_LIST_WORKERS on a small box if memory
# is tight. The result cache makes repeat searches cheap regardless.
def _list_workers_default() -> int:
    try:
        return max(1, int(os.environ.get("SAFEPLATE_LIST_WORKERS", "12")))
    except ValueError:
        return 12


_LIST_ASSESS_WORKERS = _list_workers_default()
# Overall wall-clock budget for menu-backing the list. Brave's ~1 req/s limit +
# per-site fetch latency mean a cold list could take many minutes; this caps how
# long the page waits. Restaurants not finished by the deadline fall back to the
# cuisine prior for THIS response and upgrade to menu-backed once their extraction
# completes (cached) -- i.e. a later search or opening the drawer.
_LIST_ASSESS_BUDGET_S = 22.0
# Deep-extract only the nearest N restaurants per search (the most actionable) to
# keep the first load fast. Farther ones show the cuisine prior and upgrade to
# menu-backed when opened. Tune up for more coverage at the cost of latency/spend.
_LIST_MENU_BACKED_TOP_N = 12


def _row_distance(row: Any) -> float:
    d = getattr(row, "distance_meters", None)
    try:
        return float(d)
    except (TypeError, ValueError):
        return float("inf")


def _build_search_cards(
    rows: list[Any], payload: dict[str, Any], *, severity: str
) -> list[dict[str, Any]]:
    """Every card is menu-backed (same extraction + scorer + result cache as the
    drawer), computed concurrently so the list and the drawer agree.

    BOUNDED + robust: the whole list shares a wall-clock budget so one slow /
    rate-limited site can't stall the page. A restaurant that errors OR doesn't
    finish in time degrades to the cuisine prior for this response (and upgrades to
    menu-backed once its extraction completes and is cached). The drawer always runs
    the full extraction, so opening a 'cuisine estimate' card still gives the real
    menu-backed verdict and warms the cache for the next search."""
    rows = list(rows)

    from concurrent.futures import (
        ThreadPoolExecutor,
        TimeoutError as FuturesTimeout,
        as_completed,
    )

    profile = _user_profile_from_payload(payload)
    scoring_engine = _scoring_engine_from_payload(payload)
    user_agent = get_user_agent()
    api_key = get_gemini_api_key()

    def _prior(row: Any) -> dict[str, Any]:
        return _restaurant_payload(row, severity=severity)

    # Only the nearest N get a (bounded) live extraction; the rest are prior-only and
    # upgrade to menu-backed when opened. Keeps the first load fast without hiding the
    # farther options from the list.
    deep = set(sorted(range(len(rows)), key=lambda i: _row_distance(rows[i]))[:_LIST_MENU_BACKED_TOP_N])

    results: list[dict[str, Any] | None] = [None] * len(rows)
    contexts: dict[int, dict[str, Any]] = {}  # i -> ai_assisted re-scoring context (deep cards only)
    executor = ThreadPoolExecutor(max_workers=_LIST_ASSESS_WORKERS)
    try:
        futures = {
            executor.submit(
                _menu_backed_card, rows[i], profile=profile,
                user_agent=user_agent, api_key=api_key, scoring_engine=scoring_engine,
            ): i
            for i in deep
        }
        # Collect in COMPLETION order under a shared wall-clock budget, so one slow
        # or rate-limited site can't keep us from banking cards that already
        # finished. Anything unfinished when the budget expires falls back to the
        # cuisine prior below (and keeps running to warm the cache for next time).
        try:
            for fut in as_completed(futures, timeout=_LIST_ASSESS_BUDGET_S):
                i = futures[fut]
                try:
                    results[i], ctx = fut.result()
                    if ctx is not None:
                        contexts[i] = ctx
                except Exception:
                    results[i] = _prior(rows[i])
        except FuturesTimeout:
            pass  # budget hit; remaining restaurants degrade to the prior
    finally:
        # Don't wait on stragglers -- they keep running and warm the cache for next time.
        executor.shutdown(wait=False, cancel_futures=True)

    # AI engine: re-score every extracted card in ONE batched LLM call (N calls -> 1).
    # Each restaurant label-routes (chart / raw menu / context) + keeps its deterministic
    # floor + guardrails; the whole batch fails closed to the deterministic cards.
    if _is_ai_engine(scoring_engine) and api_key and contexts:
        from safeplate.allergen_score_llm import score_restaurants_with_llm_batch

        reqs = [
            {"id": str(i), "profile": ctx["profile"], "cuisines": ctx["cuisines"],
             "region": ctx["region"], "menu_items": ctx["menu_items"],
             "signals": ctx["signals"], "community": ctx.get("community"),
             "official_domain": ctx["official_domain"]}
            for i, ctx in contexts.items()
        ]
        try:
            scored = score_restaurants_with_llm_batch(
                reqs, api_key=api_key, model=get_gemini_model()
            )
            for i, ctx in contexts.items():
                assessment = scored.get(str(i))
                if assessment is not None and results[i] is not None:
                    _write_assessment_into_card(
                        results[i], assessment, scoring_engine=scoring_engine, **ctx["rebuild"]
                    )
        except Exception:
            pass  # keep the deterministic cards already built

    return [results[i] if results[i] is not None else _prior(rows[i]) for i in range(len(rows))]
