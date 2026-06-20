from __future__ import annotations

import base64
from collections import deque
from dataclasses import asdict
import hmac
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from safeplate.coerce import optional_float as _optional_float
from safeplate.config import (
    get_brave_search_api_key,
    get_engine,
    get_geoapify_api_key,
    get_gemini_api_key,
    get_gemini_fallback_models,
    get_gemini_model,
    get_google_places_api_key,
    get_user_agent,
    normalize_extraction_engine,
    normalize_scoring_engine,
)
from safeplate.demo_fixtures import DEFAULT_DEMO_LOCATION
from safeplate.demo_fixtures import DemoFixtureError
from safeplate.demo_fixtures import load_demo_menu
from safeplate.demo_fixtures import load_demo_search
from safeplate.export import build_output_paths, write_csv, write_json
from safeplate.geo import Coordinates, geocode_location
from safeplate.providers.geoapify import GEOAPIFY_CATEGORIES
from safeplate.providers.geoapify import fetch_nearby_restaurants as fetch_geoapify
from safeplate.providers.google_places import GOOGLE_INCLUDED_TYPES
from safeplate.providers.google_places import fetch_nearby_restaurants as fetch_google
from safeplate.providers.osm import fetch_nearby_restaurants as fetch_osm
from safeplate.quality import build_quality_summary, write_quality_summary


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
GEMINI_MENU_VALIDATION_CHUNK_SIZE = 45


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _basic_auth_credentials() -> tuple[str, str] | None:
    """The (username, password) the app requires, or None to run open.

    Auth turns on only when SAFEPLATE_PASSWORD is set, so local use stays
    friction-free; a public deploy MUST set it. Username defaults to 'safeplate'
    (override with SAFEPLATE_USERNAME)."""
    password = os.environ.get("SAFEPLATE_PASSWORD", "").strip()
    if not password:
        return None
    username = os.environ.get("SAFEPLATE_USERNAME", "safeplate").strip() or "safeplate"
    return username, password


class _RateLimiter:
    """Per-client sliding-window limiter (in-memory, thread-safe). Bounds API
    spend/abuse on the paid endpoints even for authenticated users. A limit <= 0
    disables it."""

    def __init__(self, *, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        if self._max <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - self._window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True


def create_app_handler(*, demo_mode: bool = False) -> type[BaseHTTPRequestHandler]:
    auth = _basic_auth_credentials()
    rate_limiter = _RateLimiter(
        max_requests=_int_env("SAFEPLATE_RATE_LIMIT_PER_MIN", 20),
        window_seconds=60.0,
    )

    class SafePlateRequestHandler(BaseHTTPRequestHandler):
        server_version = "SafePlateLocalApp/0.1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/healthz":
                self._send_json({"status": "ok"})
                return
            if not self._check_auth():
                return
            if path == "/":
                self._send_html(app_html())
                return
            if path == "/api/config":
                self._send_json(
                    {
                        "demoMode": demo_mode,
                        "defaultDemoLocation": DEFAULT_DEMO_LOCATION if demo_mode else "",
                        "googleConfigured": bool(get_google_places_api_key()),
                        "geoapifyConfigured": bool(get_geoapify_api_key()),
                        "braveConfigured": bool(get_brave_search_api_key()),
                        "geminiConfigured": bool(get_gemini_api_key()),
                        "geminiModel": get_gemini_model(),
                        "geminiFallbackModels": get_gemini_fallback_models(),
                        "defaultProvider": _default_provider(),
                    }
                )
                return
            self.send_error(404)

        def do_POST(self) -> None:
            if not self._check_auth():
                return
            path = urlparse(self.path).path
            if path in ("/api/search", "/api/menu") and not rate_limiter.check(
                self._client_ip()
            ):
                self._send_json(
                    {"error": "Rate limit exceeded -- please wait a minute and try again."},
                    status=429,
                )
                return
            if path == "/api/search":
                self._handle_search()
                return
            if path == "/api/menu":
                self._handle_menu()
                return
            self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle_search(self) -> None:
            try:
                payload = self._read_json()
                response = run_restaurant_search(payload, demo_mode=demo_mode)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(response)

        def _handle_menu(self) -> None:
            try:
                payload = self._read_json()
                response = run_menu_extraction(payload, demo_mode=demo_mode)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(response)

        def _check_auth(self) -> bool:
            """Gate every route except /healthz behind HTTP Basic auth when a
            password is configured. We guard the top-level page too (not just the
            APIs): a 401 from fetch() won't open the browser's login dialog -- only
            a navigation does -- and once the page has prompted, same-origin API
            fetches reuse the cached credentials automatically."""
            if auth is None:
                return True
            header = self.headers.get("Authorization", "")
            if header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(header[6:]).decode("utf-8")
                except Exception:
                    decoded = ""
                user, _, password = decoded.partition(":")
                if hmac.compare_digest(user, auth[0]) and hmac.compare_digest(
                    password, auth[1]
                ):
                    return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="SafePlate"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def _client_ip(self) -> str:
            """Real client IP for rate limiting. Behind Render's proxy the socket
            peer is the proxy, so trust the first hop of X-Forwarded-For."""
            forwarded = self.headers.get("X-Forwarded-For", "").strip()
            if forwarded:
                return forwarded.split(",")[0].strip()
            return self.client_address[0] if self.client_address else "unknown"

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8")
            if not raw:
                return {}
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Request JSON must be an object")
            return payload

        def _send_html(self, html: str, status: int = 200) -> None:
            encoded = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return SafePlateRequestHandler


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
    engine = normalize_extraction_engine(payload.get("engine") or get_engine())
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

    return {
        "location": location_label,
        "coordinates": asdict(coordinates),
        "provider": provider,
        "radius": radius,
        "limit": limit,
        "rows": _build_search_cards(rows, payload, engine=engine, severity=severity),
        "summary": summary,
        "files": files,
    }


def _run_demo_restaurant_search(payload: dict[str, Any]) -> dict[str, Any]:
    fixture = load_demo_search()
    engine = normalize_extraction_engine(payload.get("engine") or get_engine())
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
        "rows": [_restaurant_payload(row, engine=engine, severity=severity) for row in rows],
        "summary": summary,
        "files": {},
        "demoMode": True,
    }


def run_menu_extraction(payload: dict[str, Any], *, demo_mode: bool = False) -> dict[str, Any]:
    if demo_mode:
        return _run_demo_menu_extraction(payload)

    engine = normalize_extraction_engine(payload.get("engine") or get_engine())
    if engine == "structured":
        return _run_structured_menu_extraction(payload)

    from safeplate.legacy_extraction import run_legacy_menu_extraction
    return run_legacy_menu_extraction(payload)


def _build_restaurant_signals(allergy_signals, *, name, address, website_url):
    """Build the Layer-#5 RestaurantSignals from extracted allergy signals + the
    curated registry. Shared so the deterministic score and the batched-LLM bundle
    speak from the IDENTICAL signals (no drift between list extraction and re-score)."""
    from safeplate.allergen_score import RestaurantSignals
    from safeplate.allergy_registry import apply_registry

    signals = RestaurantSignals.from_allergy_signals(allergy_signals)
    # A verified dedicated nut-free / allergy kitchen gets a trusted nut-free /
    # allergy-aware signal even when its own site yields nothing extractable.
    apply_registry(signals, name, address, website_url)
    return signals


def _extract_and_assess_structured(
    *,
    name: str,
    website_url: str,
    address: str,
    categories: list[str],
    latitude: float | None,
    longitude: float | None,
    profile: Any,
    user_agent: str,
    api_key: str | None,
    cuisines: list[str] | None = None,
    region: str | None = None,
    scoring_engine: str = "rules",
):
    """Run the structured extraction (result-cached) + Layer #5 assessment for one
    restaurant. Shared by the menu drawer and the menu-backed search list so both
    speak from the SAME extraction + scorer; the result cache means the second
    caller (whichever fires later) pays nothing. ``cuisines`` / ``region`` are
    derived by the scorer when not supplied; callers that already have them (the
    search card renders the prior first) pass them in to skip the re-derivation.
    Returns (assessment, menu_items, allergy_signals, coverage, errors)."""
    from safeplate.allergen_score import assess_restaurant_record
    from safeplate.extraction2.discover import discover_and_extract

    errors: list[dict[str, str]] = []
    menu_items: list[Any] = []
    allergy_signals: list[Any] = []
    coverage: list[Any] = []

    if website_url:
        try:
            _candidates, result = discover_and_extract(
                website_url,
                user_agent=user_agent,
                restaurant_name=name,
                address=address,
                api_key=api_key,
                model=get_gemini_model(),
                brave_api_key=get_brave_search_api_key(),
                use_result_cache=True,  # repeat opens of a restaurant skip all API calls
            )
            menu_items = result.items
            allergy_signals = result.allergy_signals
            coverage = result.coverage
        except Exception as exc:  # never let extraction break the response
            errors.append({"source": "extraction2", "error": str(exc)})
    else:
        errors.append({"source": "website_lookup", "error": "No website URL provided."})

    signals = _build_restaurant_signals(
        allergy_signals, name=name, address=address, website_url=website_url
    )
    record = SimpleNamespace(
        categories=categories,
        address=address,
        latitude=latitude,
        longitude=longitude,
        website_url=website_url,  # lets the scorer judge source provenance
    )
    if _is_ai_engine(scoring_engine):
        # AI scorer: label-routes (trust a chart / judge the raw menu / context only),
        # with the deterministic floor + guardrails. Falls back to the deterministic
        # assessment if no key / the LLM call fails.
        from safeplate.allergen_score_llm import assess_restaurant_record_with_llm
        assessment = assess_restaurant_record_with_llm(
            record, profile, menu_items=menu_items, signals=signals,
            api_key=api_key, model=get_gemini_model(),
        )
    else:
        assessment = assess_restaurant_record(
            record, profile, menu_items=menu_items, signals=signals,
            cuisines=cuisines, region=region,
        )
    return assessment, menu_items, allergy_signals, coverage, errors


def _structured_menu_response(
    *,
    restaurant_name: str,
    website_url: str,
    assessment: Any,
    menu_items: list[Any],
    allergy_signals: list[Any],
    coverage: list[Any],
    errors: list[dict[str, str]],
    scoring_engine: str = "rules",
) -> dict[str, Any]:
    """Build the structured drawer payload (menuItems + allergySignals + assessment + the
    legacy-shaped summary the UI drawer reads). Shared so the SEARCH can embed this exact
    payload per menu-backed card -- letting the drawer open instantly with no
    /api/menu round-trip -- and so /api/menu can return it on demand for cards that
    weren't pre-extracted."""
    item_payloads = _menu_item_payloads(menu_items)
    riskiest_items: list[dict[str, Any]] = []
    for per_allergen in assessment.per_allergen:
        riskiest_items.extend(per_allergen.riskiest_items)
    coverage_status = "menu_backed" if menu_items else "cuisine_estimate"
    return {
        "engine": "structured",
        "restaurantName": restaurant_name,
        "websiteUrl": website_url,
        "menuItems": item_payloads,
        "allergySignals": [asdict(sig) for sig in allergy_signals],
        "assessment": asdict(assessment),
        "coverage": [asdict(report) for report in coverage],
        "coverageStatus": coverage_status,
        "summary": {
            "engine": "structured",
            "scoringEngine": scoring_engine,
            "itemCount": len(item_payloads),
            "allergenItemCount": sum(
                1 for item in menu_items if getattr(item, "allergen_terms", None)
            ),
            "allergySignalCount": len(allergy_signals),
            "tier": assessment.tier,
            "overallRisk": round(assessment.overall_risk, 3),
            "overallConfidence": round(assessment.overall_confidence, 2),
            "evidenceBasis": assessment.evidence_basis,
            "menuSourceErrors": errors,
            "coverageStatus": coverage_status,
            # legacy-compatible shapes the UI drawer reads:
            "menuBackedRisk": {
                "risk": round(assessment.overall_risk, 3),
                "confidence": round(assessment.overall_confidence, 2),
                "rationale": assessment.rationale,
                "isMenuBacked": bool(menu_items),
                "tier": assessment.tier,
                "riskiestItems": riskiest_items,
                "scoringEngine": scoring_engine,
            },
            "restaurantSignals": {
                "has_allergy_disclaimer": assessment.handling.allergy_aware,
                "has_cross_contact_warning": assessment.handling.cross_contact_warning,
                "mentions_staff_allergy_instruction": assessment.handling.ask_staff,
                "has_nut_free_claim": assessment.handling.nut_free_claim,
            },
        },
        "files": {},
    }


def _run_structured_menu_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    """Engine 'structured': clean-architecture extraction (extraction2) fused with the
    Layer #5 per-user scorer. Returns the same menuItems shape as legacy (same
    MenuItemRecord), plus an `assessment` (tiered per-user risk) and
    `allergySignals` (restaurant-level allergy-handling evidence)."""
    restaurant_name = str(payload.get("name") or "").strip()
    website_url = str(payload.get("websiteUrl") or "").strip()
    address = str(payload.get("address") or "").strip()
    categories = _string_list(payload.get("categories"))
    if not restaurant_name:
        raise ValueError("Restaurant name is required.")

    profile = _user_profile_from_payload(payload)
    scoring_engine = _scoring_engine_from_payload(payload)
    latitude = _optional_float(payload.get("latitude"))
    longitude = _optional_float(payload.get("longitude"))
    # Derive cuisines/region once and reuse for both the extraction-stage score and
    # the community re-score below, instead of each call re-deriving them.
    from safeplate.allergen_prior import normalize_cuisine, region_from_address

    cuisines = normalize_cuisine(categories)
    region = region_from_address(address, latitude=latitude, longitude=longitude)
    assessment, menu_items, allergy_signals, coverage, errors = _extract_and_assess_structured(
        name=restaurant_name,
        website_url=website_url,
        address=address,
        categories=categories,
        latitude=latitude,
        longitude=longitude,
        profile=profile,
        user_agent=get_user_agent(),
        api_key=get_gemini_api_key(),
        cuisines=cuisines,
        region=region,
        scoring_engine=scoring_engine,
    )

    # Community layer (DRAWER ONLY -- one restaurant, cacheable; the list stays cheap):
    # web-sourced allergy-handling signals fold into the score (safety-asymmetric), and
    # when NO menu was found, diner-mentioned dishes seed the dish-name prior so even a
    # menu-less place beats a bare cuisine guess. Never grounded allergen evidence.
    community_quotes: list[str] = []
    try:
        from safeplate.allergen_score import RestaurantSignals, assess_restaurant_record
        from safeplate.community_signals import fetch_community_signals

        cres = fetch_community_signals(
            restaurant_name=restaurant_name, address=address,
            user_agent=get_user_agent(), brave_api_key=get_brave_search_api_key(),
            gemini_api_key=get_gemini_api_key(), gemini_model=get_gemini_model(),
            want_dishes=not menu_items,
        )
        community_quotes = cres.quotes
        if cres.signals or (not menu_items and cres.dishes):
            if not menu_items and cres.dishes:
                menu_items = cres.dishes  # no-menu dish-context -> feeds the dish prior
            record = SimpleNamespace(
                categories=categories, address=address,
                latitude=latitude, longitude=longitude,
                website_url=website_url,
            )
            from safeplate.allergy_registry import apply_registry
            sig = RestaurantSignals.from_allergy_signals(allergy_signals)
            apply_registry(sig, restaurant_name, address, website_url)
            if _is_ai_engine(scoring_engine):
                from safeplate.allergen_score_llm import assess_restaurant_record_with_llm
                assessment = assess_restaurant_record_with_llm(
                    record, profile, menu_items=menu_items, signals=sig,
                    community=cres.signals or None,
                    api_key=get_gemini_api_key(), model=get_gemini_model(),
                )
            else:
                assessment = assess_restaurant_record(
                    record, profile, menu_items=menu_items, signals=sig,
                    community=cres.signals or None, cuisines=cuisines, region=region,
                )
    except Exception as exc:  # community is best-effort; never break the drawer
        errors.append({"source": "community_signals", "error": str(exc)})

    response = _structured_menu_response(
        restaurant_name=restaurant_name,
        website_url=website_url,
        assessment=assessment,
        menu_items=menu_items,
        allergy_signals=allergy_signals,
        coverage=coverage,
        errors=errors,
        scoring_engine=scoring_engine,
    )
    response["communityQuotes"] = community_quotes
    return response


def _severity_from_str(value: Any):
    from safeplate.allergen_score import Severity

    return {
        "avoid_preference": Severity.AVOID_PREFERENCE,
        "intolerance": Severity.INTOLERANCE,
        "allergy": Severity.ALLERGY,
        "anaphylaxis": Severity.ANAPHYLAXIS,
    }.get(str(value or "").lower(), Severity.ALLERGY)


def _scoring_engine_from_payload(payload: dict[str, Any]) -> str:
    """Which SCORING engine to use: 'ai' = label-routing LLM scorer (DEFAULT), 'rules'
    = deterministic. Per-request `scoringEngine`, else env SAFEPLATE_SCORING_ENGINE.
    Legacy 'v2'/'v3'/'ai_assisted'/'ai_full_menu' values are still accepted."""
    return normalize_scoring_engine(
        payload.get("scoringEngine") or os.environ.get("SAFEPLATE_SCORING_ENGINE", "")
    )


def _is_ai_engine(scoring_engine: str) -> bool:
    """True for the LLM scoring engine ('ai'); False for the deterministic 'rules'.
    The AI engine label-routes per restaurant (labeled / raw_menu / no_menu)."""
    return scoring_engine == "ai"


def _cross_contact_from_str(value: Any):
    """Map the UI's cross-contact choice to a CrossContactSensitivity. Returns None
    for unset/unknown so the scorer derives a sensible level from severity."""
    from safeplate.allergen_score import CrossContactSensitivity

    return {
        "not_concerned": CrossContactSensitivity.NOT_CONCERNED,
        "moderate": CrossContactSensitivity.MODERATE,
        "strict": CrossContactSensitivity.STRICT,
    }.get(str(value or "").lower())


def _user_profile_from_payload(payload: dict[str, Any]):
    """Build a scorer UserProfile from the request. Nuts-only today (matches the
    prior/UI); severity is honoured so the same risk trips a worse tier for an
    anaphylactic user than a mild-preference one, and cross-contact sensitivity is
    honoured INDEPENDENTLY of severity (trace tolerance vs ingestion reaction)."""
    from safeplate.allergen_score import UserProfile

    return UserProfile.for_nuts(
        _severity_from_str(payload.get("severity")),
        cross_contact=_cross_contact_from_str(payload.get("crossContact")),
    )


def _run_demo_menu_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    restaurant_name = str(payload.get("name") or "").strip()
    restaurant_source_id = str(payload.get("sourceId") or "").strip()
    if not restaurant_source_id:
        restaurant_source_id = _demo_source_id_for_name(restaurant_name)
    if not restaurant_source_id:
        raise ValueError("Demo restaurant sourceId is required.")

    try:
        fixture = load_demo_menu(restaurant_source_id)
    except DemoFixtureError as exc:
        raise ValueError(str(exc)) from exc

    if not restaurant_name:
        for source in fixture.menu_sources:
            if source.restaurant_name:
                restaurant_name = source.restaurant_name
                break
    website_url = str(payload.get("websiteUrl") or "")
    if not website_url and fixture.menu_sources:
        website_url = fixture.menu_sources[0].website_url

    displayed_menu_items = _menu_item_payloads(fixture.menu_items)
    for item in displayed_menu_items:
        item.update(
            {
                "llm_validation_status": "demo_fixture",
                "llm_validated": False,
                "llm_is_menu_item": None,
                "llm_confidence": None,
                "llm_rejection_reason": "",
                "llm_evidence_quote": "",
            }
        )

    summary = _menu_summary(
        fixture.menu_sources,
        fixture.menu_text,
        displayed_menu_items,
        parsed_item_count=len(fixture.menu_items),
        rejected_items=[],
        validation_summary=_empty_validation_summary(),
        menu_source_errors=[],
        website_url=website_url,
        website_recovery=None,
        brave_fallback_used=False,
        restaurant_payload=payload,
        demo_scenario=fixture.scenario,
    )
    return {
        "restaurantName": restaurant_name,
        "websiteUrl": website_url,
        "websiteRecovery": None,
        "menuSources": [_safe_payload(row) for row in fixture.menu_sources],
        "menuText": [_safe_payload(row) for row in fixture.menu_text],
        "menuItems": displayed_menu_items,
        "rejectedMenuItems": [],
        "summary": summary,
        "files": {},
        "demoMode": True,
    }


def _demo_source_id_for_name(name: str) -> str:
    if not name:
        return ""
    try:
        fixture = load_demo_search()
    except DemoFixtureError:
        return ""
    normalized_name = name.strip().lower()
    for row in fixture.restaurants:
        if str(row.name or "").strip().lower() == normalized_name:
            return row.source_id
    return ""


def _is_gemini_model_fallback_error(message: str) -> bool:
    lower_message = message.lower()
    return any(
        marker in lower_message
        for marker in [
            "http 429",
            "http 503",
            "high demand",
            "unavailable",
            "resource_exhausted",
            "is not found for api version",
            "is not supported for generatecontent",
        ]
    )


def _menu_item_payloads(menu_items: list[Any]) -> list[dict[str, Any]]:
    payloads = []
    for index, item in enumerate(menu_items, start=1):
        payload = _safe_payload(item)
        payload["candidate_id"] = f"c{index:04d}"
        payloads.append(payload)
    return payloads


def _empty_validation_summary() -> dict[str, Any]:
    return {
        "enabled": False,
        "model": get_gemini_model(),
        "modelUsed": "",
        "fallbackModels": get_gemini_fallback_models(),
        "candidateRows": 0,
        "validatedRows": 0,
        "acceptedRows": 0,
        "rejectedRows": 0,
        "missingRows": 0,
        "warnings": [],
        "attemptErrors": [],
        "error": "",
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


def _restaurant_payload(row: Any, *, engine: str = "legacy", severity: str = "allergy") -> dict[str, Any]:
    from safeplate.allergen_prior import (
        normalize_cuisine,
        region_from_address,
        score_restaurant_prior,
    )

    payload = asdict(row)
    payload["categories"] = row.categories
    cuisines = normalize_cuisine(row.categories)
    region = region_from_address(
        row.address, latitude=row.latitude, longitude=row.longitude
    )
    # labeling_trust is exposed by the prior (not the assessment), so compute it
    # either way to keep the UI's "allergen labeling" badge working.
    prior = score_restaurant_prior(cuisines=cuisines, region=region, allergen="nuts")
    if engine == "structured":
        # Same Layer #5 scorer as the drawer, prior-only (no menu fetch at list
        # time), so the list and the drawer speak the same tier language.
        from safeplate.allergen_score import UserProfile, score_restaurant_for_user

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
    else:
        payload["allergenPrior"] = {
            "allergen": "nuts",
            "risk": round(prior.risk, 3),
            "confidence": round(prior.confidence, 2),
            "basis": prior.basis,
            "rationale": prior.rationale,
            "labelingTrust": round(prior.labeling_trust, 2),
            "cuisines": cuisines,
            "region": region,
        }
    payload["coverageStatus"] = "cuisine_estimate"
    if isinstance(row.raw_payload, dict) and row.raw_payload.get("demo_scenario"):
        payload["demoScenario"] = row.raw_payload["demo_scenario"]
    return payload


# Concurrency for the menu-backed list. Each restaurant runs a full extraction
# (HTTP + Gemini + possibly Brave, which is rate-limited ~1/s), so keep this modest
# to avoid 429 storms; the result cache makes repeat searches cheap regardless.
_LIST_ASSESS_WORKERS = 4
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


def _write_assessment_into_card(
    payload: dict[str, Any], assessment: Any, *,
    prior: Any, cuisines: list[str], region: str, name: str, website_url: str,
    menu_items: list[Any], allergy_signals: list[Any], coverage: list[Any],
    errors: list[dict[str, str]], scoring_engine: str = "rules",
) -> None:
    """Write an assessment (and its menu-backed detail) into a result card. Used for
    both the deterministic build and the ai_assisted batched re-score, so the two stay in
    lockstep -- the only thing that changes between them is ``assessment`` (and which
    scoring engine produced it, surfaced so the drawer can label the explanation)."""
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
        "scoringEngine": scoring_engine,
    }
    payload["coverageStatus"] = "menu_backed" if menu_items else "cuisine_estimate"
    # We just extracted the full menu to score the card -- carry it along so opening
    # the drawer is INSTANT (no /api/menu round-trip). Only for menu-backed cards;
    # cuisine-estimate ones have nothing to embed and fetch fresh on open.
    if menu_items:
        payload["menuDetail"] = _structured_menu_response(
            restaurant_name=name,
            website_url=website_url,
            assessment=assessment,
            menu_items=menu_items,
            allergy_signals=allergy_signals,
            coverage=coverage,
            errors=errors,
            scoring_engine=scoring_engine,
        )
    else:
        payload.pop("menuDetail", None)


def _menu_backed_card(row: Any, *, profile: Any, user_agent: str, api_key: str | None,
                      scoring_engine: str = "rules") -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Build a result-card payload whose ``allergenPrior`` IS the menu-backed Layer
    #5 assessment (same extraction + scorer + result cache as the drawer), so the
    list card and the drawer show the IDENTICAL score -- the drawer just adds the
    item-level detail. Falls back to the cuisine prior only if extraction yields
    nothing (no website / nothing found).

    Scoring is always DETERMINISTIC here. For ``scoring_engine == "ai_assisted"`` the card is
    re-scored by ONE batched LLM call over the whole list (see ``_build_search_cards``),
    not per restaurant -- so this also returns a re-scoring context (inputs + the bits
    needed to rewrite the card with the batched assessment), or ``None`` for rules."""
    from safeplate.allergen_prior import (
        normalize_cuisine,
        region_from_address,
        score_restaurant_prior,
    )
    from safeplate.allergen_score import _domain_of

    payload = asdict(row)
    payload["categories"] = row.categories
    cuisines = normalize_cuisine(row.categories)
    region = region_from_address(
        row.address, latitude=row.latitude, longitude=row.longitude
    )
    prior = score_restaurant_prior(cuisines=cuisines, region=region, allergen="nuts")

    name = str(row.name or "").strip()
    website_url = str(row.website_url or "").strip()
    address = str(row.address or "")
    assessment, menu_items, allergy_signals, coverage, errors = _extract_and_assess_structured(
        name=name,
        website_url=website_url,
        address=address,
        categories=row.categories,
        latitude=row.latitude,
        longitude=row.longitude,
        profile=profile,
        user_agent=user_agent,
        api_key=api_key,
        cuisines=cuisines,  # already derived above for the prior; don't recompute
        region=region,
        scoring_engine="rules",  # ai_assisted re-scores the whole list in ONE batched call
    )
    rebuild = dict(
        prior=prior, cuisines=cuisines, region=region, name=name,
        website_url=website_url, menu_items=menu_items,
        allergy_signals=allergy_signals, coverage=coverage, errors=errors,
    )
    _write_assessment_into_card(payload, assessment, scoring_engine="rules", **rebuild)
    if isinstance(row.raw_payload, dict) and row.raw_payload.get("demo_scenario"):
        payload["demoScenario"] = row.raw_payload["demo_scenario"]

    ctx: dict[str, Any] | None = None
    if _is_ai_engine(scoring_engine):
        ctx = {
            "profile": profile,
            "cuisines": cuisines,
            "region": region,
            "menu_items": menu_items,
            "signals": _build_restaurant_signals(
                allergy_signals, name=name, address=address, website_url=website_url
            ),
            "official_domain": _domain_of(website_url),
            "rebuild": rebuild,
        }
    return payload, ctx


def _build_search_cards(
    rows: list[Any], payload: dict[str, Any], *, engine: str, severity: str
) -> list[dict[str, Any]]:
    """Structured extraction -> every card is menu-backed (same extraction + scorer + result
    cache as the drawer), computed concurrently so the list and the drawer agree.
    Other engines -> the fast cuisine prior.

    BOUNDED + robust: the whole list shares a wall-clock budget so one slow /
    rate-limited site can't stall the page. A restaurant that errors OR doesn't
    finish in time degrades to the cuisine prior for this response (and upgrades to
    menu-backed once its extraction completes and is cached). The drawer always runs
    the full extraction, so opening a 'cuisine estimate' card still gives the real
    menu-backed verdict and warms the cache for the next search."""
    rows = list(rows)
    if engine != "structured":
        return [_restaurant_payload(row, engine=engine, severity=severity) for row in rows]

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
        return _restaurant_payload(row, engine=engine, severity=severity)

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
             "signals": ctx["signals"], "community": None,
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


def _safe_payload(row: Any) -> dict[str, Any]:
    return asdict(row)


def _menu_summary(
    menu_sources: list[Any],
    menu_text: list[Any],
    menu_items: list[Any],
    *,
    parsed_item_count: int | None = None,
    rejected_items: list[Any] | None = None,
    validation_summary: dict[str, Any] | None = None,
    menu_source_errors: list[dict[str, str]] | None = None,
    website_url: str = "",
    website_recovery: dict[str, Any] | None = None,
    brave_fallback_used: bool = False,
    restaurant_payload: dict[str, Any] | None = None,
    demo_scenario: str = "",
) -> dict[str, Any]:
    method_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    dietary_rows = 0
    allergen_rows = 0
    priced_rows = 0
    for item in menu_items:
        extraction_method = str(_item_value(item, "extraction_method") or "")
        source_type = str(_item_value(item, "source_type") or "")
        method_counts[extraction_method] = method_counts.get(extraction_method, 0) + 1
        source_counts[source_type] = source_counts.get(source_type, 0) + 1
        category = str(_item_value(item, "category") or "Uncategorized")
        category_counts[category] = category_counts.get(category, 0) + 1
        if _item_terms(item, "dietary_terms"):
            dietary_rows += 1
        if _item_terms(item, "allergen_terms"):
            allergen_rows += 1
        if _item_value(item, "price"):
            priced_rows += 1

    validation_summary = validation_summary or _empty_validation_summary()
    rejected_items = rejected_items or []
    coverage_status = _coverage_status(menu_sources, menu_text, menu_items)
    menu_backed_risk = _menu_backed_nut_risk(restaurant_payload or {}, menu_items)
    restaurant_signals = restaurant_signals_from_evidence(menu_text, menu_items)
    return {
        "sourceCount": len(menu_sources),
        "textRecordCount": len(menu_text),
        "itemCount": len(menu_items),
        "parsedItemCount": parsed_item_count
        if parsed_item_count is not None
        else len(menu_items),
        "shownItemCount": len(menu_items),
        "rejectedItemCount": len(rejected_items),
        "pricedRows": priced_rows,
        "dietaryRows": dietary_rows,
        "allergenRows": allergen_rows,
        "geminiValidationEnabled": bool(validation_summary.get("enabled")),
        "geminiModel": validation_summary.get("model", ""),
        "geminiModelUsed": validation_summary.get("modelUsed", ""),
        "geminiFallbackModels": validation_summary.get("fallbackModels", []),
        "geminiValidatedRows": validation_summary.get("validatedRows", 0),
        "geminiAcceptedRows": validation_summary.get("acceptedRows", 0),
        "geminiRejectedRows": validation_summary.get("rejectedRows", 0),
        "geminiMissingRows": validation_summary.get("missingRows", 0),
        "geminiValidationError": validation_summary.get("error", ""),
        "geminiValidationWarnings": validation_summary.get("warnings", []),
        "geminiAttemptErrors": validation_summary.get("attemptErrors", []),
        "menuSourceErrors": menu_source_errors or [],
        "websiteUrl": website_url,
        "websiteRecoveryStatus": (website_recovery or {}).get("status", ""),
        "braveFallbackUsed": brave_fallback_used,
        "coverageStatus": coverage_status,
        "menuBackedRisk": menu_backed_risk,
        "restaurantSignals": restaurant_signals,
        "demoScenario": demo_scenario,
        "methodCounts": dict(
            sorted(method_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "sourceTypeCounts": dict(
            sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "categoryCounts": dict(
            sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "textCharacters": sum(row.char_count for row in menu_text),
        "priceHits": sum(row.price_count for row in menu_text),
    }


def _coverage_status(
    menu_sources: list[Any],
    menu_text: list[Any],
    menu_items: list[Any],
) -> str:
    if menu_items:
        return "menu_backed"
    if menu_sources or menu_text:
        return "cuisine_estimate"
    return "no_menu_found"


def _menu_backed_nut_risk(
    restaurant_payload: dict[str, Any],
    menu_items: list[Any],
) -> dict[str, Any]:
    from safeplate.allergen_prior import normalize_cuisine
    from safeplate.allergen_prior import region_from_address
    from safeplate.allergen_prior import restaurant_nut_risk

    categories = _string_list(restaurant_payload.get("categories"))
    cuisines = normalize_cuisine(categories)
    latitude = _optional_float(restaurant_payload.get("latitude"))
    longitude = _optional_float(restaurant_payload.get("longitude"))
    region = region_from_address(
        str(restaurant_payload.get("address") or ""),
        latitude=latitude,
        longitude=longitude,
    )
    item_rows = [
        {
            "item_name": str(_item_value(item, "item_name") or ""),
            "description": str(_item_value(item, "description") or ""),
        }
        for item in menu_items
    ]
    risk = restaurant_nut_risk(
        cuisines=cuisines,
        region=region,
        menu_items=item_rows,
        allergen="nuts",
    )
    return {
        "allergen": "nuts",
        "risk": round(risk.risk, 3),
        "confidence": round(risk.confidence, 2),
        "basis": "menu_items" if item_rows else "cuisine_location_prior",
        "rationale": risk.rationale,
        "labelingTrust": round(risk.labeling_trust, 2),
        "riskiestItems": [
            {"itemName": name, "risk": round(item_risk, 3)}
            for name, item_risk in risk.riskiest_items
        ],
        "isMenuBacked": bool(item_rows),
        "cuisines": cuisines,
        "region": region,
    }


def restaurant_signals_from_evidence(
    menu_text: list[Any],
    menu_items: list[Any],
) -> dict[str, bool]:
    text = _normalized_evidence_text(menu_text, menu_items)
    return {
        "has_allergy_disclaimer": _has_any(
            text,
            [
                "food allergy",
                "food allergies",
                "allergy notice",
                "allergen notice",
                "allergen information",
                "allergen guide",
            ],
        ),
        "has_cross_contact_warning": _has_any(
            text,
            [
                "cross contact",
                "cross-contact",
                "cross contamination",
                "cross-contamination",
                "shared fryer",
                "shared fryers",
                "may contain",
                "cannot guarantee",
            ],
        ),
        "mentions_staff_allergy_instruction": _has_any(
            text,
            [
                "tell your server",
                "inform your server",
                "please inform",
                "please alert",
                "notify your server",
                "let us know",
                "speak to a manager",
            ],
        ),
        "has_nut_free_claim": _has_any(
            text,
            [
                "nut free",
                "nut-free",
                "peanut free",
                "peanut-free",
                "tree nut free",
                "tree-nut-free",
                "no peanuts",
                "no tree nuts",
            ],
        ),
    }


def _normalized_evidence_text(menu_text: list[Any], menu_items: list[Any]) -> str:
    pieces = []
    for row in menu_text:
        pieces.append(str(_item_value(row, "extracted_text") or ""))
        pieces.extend(_item_terms(row, "dietary_terms"))
        pieces.extend(_item_terms(row, "allergen_terms"))
    for item in menu_items:
        for field in ["item_name", "description", "raw_text", "price"]:
            pieces.append(str(_item_value(item, field) or ""))
        pieces.extend(_item_terms(item, "dietary_terms"))
        pieces.extend(_item_terms(item, "allergen_terms"))
    return " ".join(pieces).lower().replace("-", " ")


def _has_any(text: str, needles: list[str]) -> bool:
    return any(needle.replace("-", " ") in text for needle in needles)


def _item_value(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _item_terms(item: Any, name: str) -> list[str]:
    value = _item_value(item, name)
    if isinstance(value, list):
        return [str(term) for term in value if str(term).strip()]
    if isinstance(value, str):
        return [term.strip() for term in value.split(";") if term.strip()]
    return []


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(";") if item.strip()]
    return []


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _default_provider() -> str:
    if get_google_places_api_key():
        return "google"
    return "osm"


def run_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    demo_mode: bool = False,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), create_app_handler(demo_mode=demo_mode))


_APP_TEMPLATE_PATH = Path(__file__).resolve().parent / "app_template.html"
_app_html_cache: dict[str, Any] = {"mtime": None, "html": ""}


def app_html() -> str:
    """Serve the page template, re-reading it when the file changes so edits show on
    a plain browser refresh -- no server restart needed. Only re-reads when the file's
    mtime changes (a cheap stat per request); on a transient read error (e.g. the file
    caught mid-save) it keeps serving the last good copy."""
    try:
        mtime = _APP_TEMPLATE_PATH.stat().st_mtime
        if mtime != _app_html_cache["mtime"]:
            _app_html_cache["html"] = _APP_TEMPLATE_PATH.read_text(encoding="utf-8")
            _app_html_cache["mtime"] = mtime
    except OSError:
        pass  # keep serving the last good copy
    return _app_html_cache["html"]



def server_namespace(host: str, port: int) -> SimpleNamespace:
    return SimpleNamespace(host=host, port=port, url=f"http://{host}:{port}")
