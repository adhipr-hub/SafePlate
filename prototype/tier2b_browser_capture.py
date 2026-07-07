"""Tier 2b PROTOTYPE -- headless-browser network capture (NOT wired into the pipeline).

Measures how a headless browser performs on JS restaurant sites that the static /
api_capture path can't read. It scores everything it finds with SafePlate's OWN
extractors (extract_allergen_items_from_obj / _from_embedded_json / allergen_matrix)
-- the same code production would feed -- pulling data from THREE places: intercepted
XHR/GraphQL JSON, the rendered DOM (post-JS embedded JSON + HTML matrices), and
validated allergen PDFs.

v2 -- optimized for speed AND coverage:
  * REAL Chrome (channel=chrome) over bundled Chromium: passes Akamai/Cloudflare WAFs
    that reset bundled Chromium's TLS/HTTP2 fingerprint.
  * ADAPTIVE CASCADE per site: (1) load the URL directly (capture XHR JSON + the
    landing page if it IS/redirects-to a PDF); if no dishes, (2) find + follow the
    in-page "allergen/nutrition" link, harvesting the rendered DOM or validating a
    downloadable PDF (national allergen pages/PDFs are the best source, esp. UK/EU);
    if still nothing, (3) drive a location/store picker. Stops at the first hit.
  * Deadline-aware: every phase is bounded by one overall budget; dead pages
    (1-request/0-JSON SSR/bot shells) bail fast instead of running to timeout.
  * DOM allergen-GRID scraper + MULTILINGUAL lexicon (prototype/allergen_grid.py):
    reconstructs dish x allergen rows from HTML/ARIA allergen tables, mapping localized
    headers (EN/DE/FR/ES/IT/NL/JP + EU-14) to canonical allergens -- with safety guards
    that decline mis-aligned/merged tables rather than emit false-negative allergen data.
  * QUIET-PERIOD early-stop: stop as soon as `enough` dishes are captured OR no new
    dish has arrived for `quiet_ms` -- instead of always waiting a fixed timeout.
  * Aggressive request blocking (resource type + third-party host list).
  * Generalized locale unwrap (en / en-US / locale / value wrappers).
  * Dish-shape guard: drop category/nav nodes ("Menu", "Seafood", "More v").

Run:  python prototype/tier2b_browser_capture.py <url> [<url> ...] [--no-store]
Each URL prints one JSON metrics line. Multiple URLs reuse one browser.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

# Allow running as a loose script (`python prototype/...py`) from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from safeplate.allergen_matrix import extract_items_from_allergen_matrix  # noqa: E402
from safeplate.extraction2.embedded_allergens import (  # noqa: E402
    extract_allergen_items_from_embedded_json,
    extract_allergen_items_from_obj,
)
from prototype.allergen_grid import extract_from_allergen_grid  # noqa: E402

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Request types that never carry menu data -- abort to save bytes + time.
_BLOCK_TYPES = {"image", "font", "media", "stylesheet"}
# Third-party hosts that are pure tracking/maps/marketing noise on these sites.
_BLOCK_HOST_HINTS = (
    "google-analytics", "googletagmanager", "doubleclick", "facebook", "connect.facebook",
    "tiktok", "snapchat", "bat.bing", "onetrust", "cookielaw", "hotjar", "segment.",
    "branch.io", "optimizely", "datadoghq", "sentry", "fonts.googleapis", "fonts.gstatic",
    "fullstory", "mouseflow", "clarity.ms", "adservice", "amplitude", "braze", "launchdarkly",
    "maps.googleapis", "maps.gstatic", "googletag", "adsrvr", "criteo", "pinterest",
    "newrelic", "nr-data", "cdn.cookielaw", "consent", "tealium", "mparticle", "kustomer",
    "yotpo", "attentivemobile", "bluecore", "klaviyo", "pendo", "googleadservices",
)
_MAX_BODY = 8_000_000  # don't json-parse multi-MB blobs
_GEO_NYC = {"latitude": 40.7484, "longitude": -73.9857}  # dense store coverage

# --- accuracy guards ---------------------------------------------------------
# Category / section / nav labels that the allergen walker sometimes emits as
# "dishes" because a section node carries an aggregate allergen field.
_NAV_STOP = {
    "menu", "our menu", "full menu", "all", "all items", "food", "drinks", "drink",
    "beverages", "sides", "sides & sweets", "desserts", "sweets", "sauces", "extras",
    "breakfast", "lunch", "dinner", "brunch", "kids", "kids meals", "family meals",
    "seafood", "featured items", "limited time items", "wings", "tenders", "sandwiches",
    "salads", "favorites", "new", "popular", "combos", "meals", "categories", "home",
    "order", "order now", "sign up", "gift cards", "careers", "catering", "specials",
    "more", "view all", "see all", "explore all", "starters", "mains", "burgers",
    "customer services", "customer service", "contact us", "contact", "faqs", "faq",
    "about us", "our story", "rewards", "sign in", "log in", "register", "press",
    "privacy policy", "terms", "delivery", "help", "support", "locations", "find us",
    "nutrition", "allergens", "allergy", "nutritional information", "gift card",
}
_GLYPHS = "▾▴►◄◀▶→←•·»«…"


def _is_dishlike(name: str) -> bool:
    n = " ".join(name.split()).strip().lower()
    if len(n) < 3 or n in _NAV_STOP:
        return False
    if any(g in name for g in _GLYPHS):
        return False
    return any(c.isalpha() for c in name)


# --- localized-string unwrap -------------------------------------------------
# Sanity/GraphQL/i18n wrap display strings as small objects keyed by a locale.
# Collapse those to the string so the extractor's name lookup succeeds.
_LOCALE_KEYS = ("locale", "en", "en-us", "en_us", "en-gb", "en_gb", "value", "text", "default")


def _unwrap_locale(obj):
    if isinstance(obj, dict):
        if "__typename" in obj or len(obj) <= 3:
            for k in obj:
                if isinstance(k, str) and k.lower() in _LOCALE_KEYS and isinstance(obj[k], str) and obj[k].strip():
                    return obj[k]
        return {k: _unwrap_locale(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unwrap_locale(v) for v in obj]
    return obj


# --- in-page link discovery (find the allergen/nutrition page) ---------------
# Returns a RANKED list of candidate allergen/nutrition links (PDFs first, self-links
# excluded). The caller validates PDFs and follows HTML candidates in order.
_FIND_ALLERGEN_JS = r"""
() => {
  const kws = ['allergen','allergy','allerg','allergene','allergène','alergen','alérgeno',
               'nutrition','nutritional','nutri','nährwert','nahrwert','ingredient'];
  const here = location.href.split('#')[0].toLowerCase();
  const seen = new Set();
  const scored = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href || '';
    if (!href.startsWith('http')) continue;
    const hl = href.toLowerCase();
    const base = hl.split('#')[0].split('?')[0];
    if (base === here || seen.has(base)) continue;   // skip self + dupes
    const text = (a.innerText || a.getAttribute('aria-label') || '').toLowerCase();
    const hay = text + ' ' + hl;
    if (!kws.some(k => hay.includes(k))) continue;
    let s = 0;
    if (base.endsWith('.pdf')) s += 20;              // a downloadable guide is the prize
    if (hay.includes('allerg') || hay.includes('alerg') || hay.includes('alérg')) s += 10;
    if (hay.includes('nutri') || hay.includes('nähr') || hay.includes('nahr')) s += 4;
    if (text.includes('allerg') || text.includes('nutri')) s += 2;  // visible link > buried url
    seen.add(base);
    scored.push([s, href]);
  }
  scored.sort((x, y) => y[0] - x[0]);
  return scored.slice(0, 8).map(x => x[1]);
}
"""

# --- store-selection heuristics ----------------------------------------------
_CONSENT_TEXTS = ("accept all", "accept cookies", "allow all", "i accept", "accept", "i agree",
                  "agree", "got it", "ok", "continue")
_LOCATION_INPUT_HINTS = ("address", "zip", "postal", "postcode", "city", "location", "find a",
                         "search location", "delivery", "suburb")
_LOCATION_BTN_TEXTS = (
    "use my location", "use current location", "near me", "nearby", "find a restaurant",
    "find restaurants", "find stores", "find a store", "start order", "start your order",
    "order now", "order pickup", "pickup", "find food", "let's go", "view menu",
)
_RESULT_TEXTS = ("order here", "select store", "select", "choose", "order now", "pickup here",
                 "start order", "view menu", "order pickup")


def _click_first_by_text(page, texts, *, timeout=2200) -> str | None:
    for t in texts:
        try:
            loc = page.get_by_text(t, exact=False).first
            if loc.count() and loc.is_visible(timeout=350):
                loc.click(timeout=timeout)
                return t
        except Exception:
            continue
    return None


def _attempt_store_selection(page, zip_code: str = "10001") -> list[str]:
    """Best-effort site-agnostic location selection. Returns a log of what it did."""
    steps: list[str] = []
    hit = _click_first_by_text(page, _CONSENT_TEXTS)
    if hit:
        steps.append(f"consent:{hit!r}")
        page.wait_for_timeout(500)
    typed = False
    try:
        inputs = page.locator("input:visible, textarea:visible")
        for i in range(min(inputs.count(), 10)):
            el = inputs.nth(i)
            hint = " ".join([
                (el.get_attribute("placeholder") or ""), (el.get_attribute("aria-label") or ""),
                (el.get_attribute("name") or ""), (el.get_attribute("id") or ""),
            ]).lower()
            if any(h in hint for h in _LOCATION_INPUT_HINTS):
                el.click(timeout=1500)
                el.fill(zip_code, timeout=1500)
                page.wait_for_timeout(900)
                el.press("Enter")
                steps.append(f"typed_zip:{zip_code}")
                typed = True
                page.wait_for_timeout(1600)
                break
    except Exception:
        pass
    if not typed:
        hit = _click_first_by_text(page, _LOCATION_BTN_TEXTS)
        if hit:
            steps.append(f"loc_button:{hit!r}")
            page.wait_for_timeout(1800)
    hit = _click_first_by_text(page, _RESULT_TEXTS)
    if hit:
        steps.append(f"result:{hit!r}")
        page.wait_for_timeout(1800)
    return steps or ["no-affordance-found"]


_LAUNCH_ARGS = ["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox",
                "--disable-extensions", "--disable-background-networking"]


def _launch(pw, *, headless: bool = True):
    """Prefer the REAL installed Chrome/Edge over Playwright's bundled Chromium: many
    chains sit behind Akamai/Cloudflare WAFs that reset bundled Chromium's HTTP/2+TLS
    fingerprint (net::ERR_HTTP2_PROTOCOL_ERROR) but accept genuine Chrome. Falls back to
    bundled Chromium when no system browser is installed."""
    for channel in ("chrome", "msedge"):
        try:
            return pw.chromium.launch(headless=headless, channel=channel, args=_LAUNCH_ARGS)
        except Exception:
            continue
    return pw.chromium.launch(headless=headless, args=_LAUNCH_ARGS)


def _validated_pdf(page, url: str, *, timeout: int = 12000) -> int | None:
    """Fetch `url` through the browser's request context (reuses cookies/UA) and return
    its byte length ONLY if it is a real PDF (magic bytes), not an SPA HTML shell served
    with a .pdf path (e.g. BK-DE). Returns None otherwise."""
    try:
        resp = page.context.request.get(url, timeout=timeout)
        body = resp.body()
    except Exception:
        return None
    if body and body[:5].startswith(b"%PDF"):
        return len(body)
    return None


@dataclass
class Endpoint:
    url: str
    bytes: int
    items: int


@dataclass
class Capture:
    target: str
    ok: bool
    elapsed_s: float
    time_to_first_data_s: float | None
    strategy: str
    requests_seen: int
    requests_blocked: int
    json_responses: int
    yielding_endpoints: int
    dishes_with_allergens: int
    early_exit: bool
    outcome: str = "none"           # dishes | pdf | none
    allergen_pdf_url: str = ""
    allergen_pdf_bytes: int = 0
    phases: list = field(default_factory=list)
    store_steps: list = field(default_factory=list)
    top_endpoints: list = field(default_factory=list)
    sample_items: list = field(default_factory=list)
    error: str = ""


def capture(
    url: str,
    *,
    timeout_ms: int = 20000,
    enough: int = 25,
    quiet_ms: int = 1400,
    headless: bool = True,
    browser=None,
    store_select: bool = True,
    follow_link: bool = True,
) -> Capture:
    """Adaptive headless capture. `browser` lets a caller reuse one Chromium."""
    from playwright.sync_api import sync_playwright

    seen = [0]
    blocked = [0]
    json_count = [0]
    endpoints: list[Endpoint] = []
    items_by_name: dict[str, object] = {}
    state = {"first_hit": None, "last_hit": None}
    early = {"hit": False}
    phases: list[str] = []
    store_steps: list[str] = []
    strategy = ""
    pdf = {"url": "", "bytes": 0}
    t0 = time.monotonic()

    def _on_route(route):
        req = route.request
        host = urlparse(req.url).netloc.lower()
        seen[0] += 1
        if req.resource_type in _BLOCK_TYPES or any(h in host for h in _BLOCK_HOST_HINTS):
            blocked[0] += 1
            try:
                return route.abort()
            except Exception:
                return
        try:
            return route.continue_()
        except Exception:
            return

    def _merge(recs, src_url: str = "") -> int:
        """Add dish-like records not seen before; update timing + early-exit. Returns
        how many new dishes were added."""
        added = 0
        for r in recs:
            if not _is_dishlike(r.item_name):
                continue
            key = r.item_name.lower()
            if key not in items_by_name:
                items_by_name[key] = r
                added += 1
        if added:
            now = time.monotonic()
            state["last_hit"] = now
            if state["first_hit"] is None:
                state["first_hit"] = now
            if src_url:
                endpoints.append(Endpoint(url=src_url, bytes=0, items=added))
        if len(items_by_name) >= enough:
            early["hit"] = True
        return added

    def _on_response(resp):
        if early["hit"]:
            return
        try:
            ct = (resp.headers or {}).get("content-type", "").lower()
            low_url = resp.url.lower()
            if "json" not in ct and "graphql" not in low_url and not low_url.split("?")[0].endswith(".json"):
                return
            body = resp.body()
            if not body or len(body) > _MAX_BODY:
                return
            try:
                obj = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                return
            json_count[0] += 1
            recs = extract_allergen_items_from_obj(_unwrap_locale(obj))
            if any(_is_dishlike(r.item_name) for r in recs):
                _merge(recs, src_url=resp.url)
        except Exception:
            return

    def _harvest_dom():
        """Mine the RENDERED HTML (post-JS) for embedded-JSON dishes + HTML allergen
        matrices -- many allergen pages hydrate data into the DOM rather than a clean
        XHR (e.g. Chick-fil-A's nutrition-allergens page = 200+ embedded items)."""
        if early["hit"]:
            return
        try:
            html = page.content()
        except Exception:
            return
        for fn in (extract_allergen_items_from_embedded_json,
                   extract_items_from_allergen_matrix,
                   extract_from_allergen_grid):
            try:
                recs = fn(html)
            except Exception:
                continue
            if recs:
                _merge(recs, src_url=f"dom:{fn.__name__}")

    def _wait(phase_deadline: float, *, require_quiet: bool = True, barren_ms: int = 4500):
        """Wait until enough dishes, a quiet period after first data, or the deadline.
        Polls the rendered DOM every ~750ms so DOM-hydrated allergen pages (no XHR)
        are caught the moment they render. Bails early on a DEAD page -- if no JSON
        response at all has fired within `barren_ms`, the site is an SSR/bot-wall shell
        that won't yield, so don't burn the whole window on it."""
        start = time.monotonic()
        i = 0
        while time.monotonic() < phase_deadline and not early["hit"]:
            lh = state["last_hit"]
            if require_quiet and lh is not None and (time.monotonic() - lh) * 1000 >= quiet_ms:
                return
            if json_count[0] == 0 and (time.monotonic() - start) * 1000 >= barren_ms:
                return
            i += 1
            if i % 6 == 0:
                _harvest_dom()
            page.wait_for_timeout(120)

    owns_browser = browser is None
    pw = None
    try:
        if owns_browser:
            pw = sync_playwright().start()
            browser = _launch(pw, headless=headless)
        ctx = browser.new_context(
            user_agent=_UA, viewport={"width": 1280, "height": 800}, locale="en-US",
            geolocation=_GEO_NYC, permissions=["geolocation"],
        )
        page = ctx.new_page()
        page.route("**/*", _on_route)
        page.on("response", _on_response)
        overall = t0 + timeout_ms / 1000.0

        # Phase 1: load the URL directly.
        nav = None
        try:
            nav = page.goto(url, wait_until="domcontentloaded", timeout=min(12000, timeout_ms))
        except Exception:
            pass
        # The URL may itself BE / redirect to an allergen PDF (e.g. McDonald's UK
        # allergen-booklet.html -> a /content/dam/... PDF). Catch that before waiting.
        if nav is not None and not pdf["url"]:
            cty = (nav.headers or {}).get("content-type", "").lower()
            if "application/pdf" in cty or page.url.lower().split("?")[0].endswith(".pdf"):
                n = _validated_pdf(page, page.url)
                if n:
                    pdf["url"], pdf["bytes"] = page.url, n
                    phases.append(f"landing_pdf({n}b)")
                    strategy = "allergen_pdf"
        _wait(min(time.monotonic() + 6.0, overall))
        _harvest_dom()
        phases.append(f"direct->{len(items_by_name)}")
        if items_by_name:
            strategy = "direct"

        # Phase 2: follow the in-page allergen/nutrition link (may be an HTML page
        # that hydrates dishes into the DOM, OR a downloadable allergen PDF).
        def _try_pdf(candidate: str) -> bool:
            if not candidate or not candidate.lower().split("?")[0].split("#")[0].endswith(".pdf"):
                return False
            budget = int(max(2000, min(10000, (overall - time.monotonic()) * 1000)))
            n = _validated_pdf(page, candidate, timeout=budget)
            if n:
                pdf["url"], pdf["bytes"] = candidate, n
                phases.append(f"pdf:{candidate[:60]}({n}b)")
                return True
            return False

        if follow_link and len(items_by_name) < enough and not pdf["url"] and time.monotonic() < overall:
            try:
                cands = page.evaluate(_FIND_ALLERGEN_JS) or []
            except Exception:
                cands = []
            # Validate any PDF candidates first (cheap: a GET, no full nav).
            for c in cands:
                if time.monotonic() >= overall or _try_pdf(c):
                    break
            # No PDF + still no dishes -> follow a FEW HTML allergen pages and harvest.
            # Capped + deadline-aware: each goto is bounded by the remaining budget so a
            # site with many candidate links (per-dish pages) can't blow past `overall`.
            if not pdf["url"] and len(items_by_name) < enough:
                for c in cands[:3]:
                    remaining = overall - time.monotonic()
                    if remaining < 2.0:
                        break
                    if c.lower().split("?")[0].split("#")[0].endswith(".pdf"):
                        continue
                    before = len(items_by_name)
                    try:
                        page.goto(c, wait_until="domcontentloaded",
                                  timeout=int(min(10000, remaining * 1000)))
                    except Exception:
                        continue
                    _wait(min(time.monotonic() + 8.0, overall))
                    _harvest_dom()
                    phases.append(f"follow:{c[:64]}->{len(items_by_name)}")
                    # The HTML hub may itself link the real PDF -- try after landing.
                    if len(items_by_name) < 3 and not pdf["url"] and time.monotonic() < overall:
                        try:
                            for c2 in (page.evaluate(_FIND_ALLERGEN_JS) or []):
                                if _try_pdf(c2):
                                    break
                        except Exception:
                            pass
                    if len(items_by_name) > before or pdf["url"]:
                        break
            if len(items_by_name) and not strategy:
                strategy = "follow_allergen_link"
            if pdf["url"] and not strategy:
                strategy = "allergen_pdf"

        # Phase 3: drive a store/location picker (only if we still have nothing AND
        # there's real budget left -- the picker does ~5s of fixed waits/clicks).
        if store_select and len(items_by_name) < 3 and not pdf["url"] and (overall - time.monotonic()) > 5:
            before = len(items_by_name)
            try:
                store_steps = _attempt_store_selection(page)
            except Exception as exc:
                store_steps = [f"selection-error:{type(exc).__name__}"]
            # Only wait for a post-selection menu fetch if we actually drove a picker --
            # otherwise there's nothing coming and the 10s wait is pure latency.
            acted = store_steps and store_steps != ["no-affordance-found"] \
                and not any("selection-error" in s for s in store_steps)
            if acted:
                _wait(min(time.monotonic() + 10.0, overall))
                _harvest_dom()
            phases.append(f"store_select->{len(items_by_name)}")
            if len(items_by_name) > before and not strategy:
                strategy = "store_select"

        ctx.close()
        elapsed = time.monotonic() - t0
        endpoints.sort(key=lambda e: e.items, reverse=True)
        ttfd = round(state["first_hit"] - t0, 2) if state["first_hit"] else None
        # A handful of dishes is real; 1-2 is usually DOM noise (nav/footer rows) -- if a
        # validated PDF also exists, it's the trustworthy asset, so prefer reporting it.
        if len(items_by_name) >= 3:
            outcome = "dishes"
        elif pdf["url"]:
            outcome = "pdf"
        elif items_by_name:
            outcome = "dishes"
        else:
            outcome = "none"
        return Capture(
            target=url, ok=True, elapsed_s=round(elapsed, 2), time_to_first_data_s=ttfd,
            strategy=strategy or "none", requests_seen=seen[0], requests_blocked=blocked[0],
            json_responses=json_count[0], yielding_endpoints=len(endpoints),
            dishes_with_allergens=len(items_by_name), early_exit=early["hit"],
            outcome=outcome, allergen_pdf_url=pdf["url"], allergen_pdf_bytes=pdf["bytes"],
            phases=phases, store_steps=store_steps,
            top_endpoints=[asdict(e) for e in endpoints[:5]],
            sample_items=[
                {"name": r.item_name, "allergens": r.allergen_terms}
                for r in list(items_by_name.values())[:8]
            ],
        )
    except Exception as exc:  # pragma: no cover - prototype
        return Capture(
            target=url, ok=False, elapsed_s=round(time.monotonic() - t0, 2),
            time_to_first_data_s=None, strategy=strategy or "error",
            requests_seen=seen[0], requests_blocked=blocked[0], json_responses=json_count[0],
            yielding_endpoints=0, dishes_with_allergens=len(items_by_name), early_exit=False,
            outcome="dishes" if items_by_name else ("pdf" if pdf["url"] else "none"),
            allergen_pdf_url=pdf["url"], allergen_pdf_bytes=pdf["bytes"],
            phases=phases, store_steps=store_steps, error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if owns_browser:
            try:
                if browser:
                    browser.close()
            finally:
                if pw:
                    pw.stop()


def main(argv: list[str]) -> int:
    store_select = "--no-store" not in argv
    urls = [a for a in argv if not a.startswith("--")] or ["https://www.bk.com/menu"]
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = _launch(pw, headless=True)
    try:
        for url in urls:
            result = capture(url, browser=browser, store_select=store_select)
            print(json.dumps(asdict(result), ensure_ascii=False))
    finally:
        browser.close()
        pw.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
