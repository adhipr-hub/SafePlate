"""Allergen-aware discovery for v2.

v1 finds allergen pages with hardcoded path lists (`_ALLERGEN_PDF_PATHS`) and
growing multilingual keyword lists (`STRICT_MENU_KEYWORDS`). That is the exact
"pile of rules" we are escaping: it breaks on any phrasing or language not in the
list. v2 instead harvests the site's REAL links and asks the LLM which ones lead
to a menu / allergen / nutrition page -- a semantic judgment that generalizes to
any wording or language, with no keyword list to maintain. A tiny token map is
kept ONLY as a no-API-key fallback.

Web-search (Brave) recovers allergen PDFs that live off-site (CDNs, upload dirs)
which on-site link harvesting can't see. Whether a candidate is "really" an
allergen source is decided downstream by extraction (validation-by-extraction),
not by another rule here.
"""

from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from safeplate.concurrency import map_concurrent
from safeplate.config import get_cache_dir
from safeplate.extraction2.interpret_llm import DEFAULT_MODEL, _call_with_retry
from safeplate.extraction2.recency import dated_duplicate_key, source_recency
from safeplate.gemini_menu import GeminiMenuError
from safeplate.page_fetch import PageFetchError, fetch_html_page
from safeplate.soup import make_soup
from safeplate.textutil import registrable_domain

RELEVANT_KINDS = ("allergen", "allergy_info", "nutrition", "menu")
_KIND_PRIORITY = {"allergen": 0, "allergy_info": 1, "nutrition": 2, "menu": 3}
_MENU_PDF_THIN = 8  # below this many extracted items, try off-site menu-PDF recovery


@dataclass
class Candidate:
    url: str
    anchor_text: str
    kind: str          # allergen | nutrition | menu
    source: str        # link | brave
    reason: str = ""


SELECT_SYSTEM = (
    "You are given links harvested from ONE restaurant's website. Classify each link "
    "that leads to one of:\n"
    "- 'allergen': a dish x allergen chart / matrix / guide, or per-dish allergen data\n"
    "- 'allergy_info': a NARRATIVE allergy page -- allergy policy, 'allergy-friendly "
    "kitchen', cross-contact / 'may contain' info, how they handle allergies, dietary "
    "requirements (text, not a per-dish chart)\n"
    "- 'nutrition': nutrition / calorie information\n"
    "- 'menu': the food/drink menu\n"
    "Ignore everything else (home, about, contact, careers, press, blog, social, login, "
    "gift cards, reservations, store locator, privacy/terms, ordering apps). Anchor text "
    "and URLs may be in ANY language. Return each relevant link's id and its kind (one "
    "of: allergen, allergy_info, nutrition, menu). Do not return irrelevant links."
)

SELECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "kind": {"type": "string"},
                },
                "required": ["id", "kind"],
            },
        }
    },
    "required": ["selected"],
}

# Fallback ONLY when there is no API key. Deliberately small + multilingual; the
# LLM selector above is the primary, non-hardcoded path.
# Order matters: specific allergy-policy phrases are checked before the generic
# "allerg" matrix token so narrative pages map to allergy_info, not allergen.
_KIND_TOKENS = {
    "allergy_info": ("allergy-friendly", "allergy friendly", "allergy policy",
                     "food allergy", "allergy advice", "dietary requirement", "may contain"),
    "allergen": ("allerg", "allergi", "allergè", "alergen", "アレルゲン", "过敏", "過敏", "알레르",
                 "αλλεργ", "аллерг"),   # Greek / Cyrillic
    "nutrition": ("nutrition", "nutritional", "nutri", "nährwert", "valeurs nutri",
                  "栄養", "营养", "영양", "διατροφ",  # "nutri" stem: nutrición/nutrição/nutrizione
                  "naering"),  # Nordic "næring[sberegner/sinnhold]" (folds æ->ae)
    "menu": ("menu", "carte", "speisekarte", "メニュー", "菜单", "菜單", "메뉴", "carta", "speise",
             # Nordic / other European menu words the English "menu" token missed
             # (all distinctive substrings -- low false-match risk): Norwegian/Swedish/
             # Danish "meny"/"spisekart"/"matseddel", Finnish "ruokalista", Portuguese
             # "cardapio", Polish "jadlospis", Croatian/Serbian "jelovnik", German bare
             # "karte" (die Karte), Greek "μενου", Cyrillic "меню". Accented forms
             # (menú/menü/menù) are handled by accent-folding in _heuristic_select.
             "meny", "spisekart", "matseddel", "ruokalista",
             "cardapio", "cardápio", "jadlospis", "jelovnik",
             "karte", "μενού", "μενου", "меню",
             # Menu words found in a 47-country live sweep: Romanian "meniu" (two
             # Bucharest sites the heuristic missed entirely), Dutch "menukaart",
             # Hungarian "étlap" (folds to "etlap"), Czech "jídelní [lístek]" (folds to
             # "jidelni"), plus distinctive native-script menu words for Hebrew / Arabic
             # / Thai / Hindi. All are collision-free substrings (no English false-match).
             "meniu", "menukaart", "etlap", "jidelni",
             "תפריט", "منيو", "เมนู", "मेन्यू", "मेनू"),
}


def discover_sources(
    website_url: str,
    *,
    user_agent: str,
    restaurant_name: str | None = None,
    address: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    brave_api_key: str | None = None,
    max_links: int = 150,
    max_candidates: int = 12,
) -> list[Candidate]:
    """Return menu / allergen / nutrition page candidates for a restaurant site."""
    candidates: list[Candidate] = []

    # Harvest the given URL AND the site root: a chain's Places URL is often a
    # location landing (/locations/<city>) whose menu just links to a JS ordering
    # page, while the actual menu/category links live on the homepage.
    links: list[tuple[str, str]] = []
    seen_link_urls: set[str] = set()

    def _fetch_seed_links(seed: str) -> list[tuple[str, str]]:
        try:
            return _harvest_links(fetch_html_page(seed, user_agent=user_agent).html, seed)
        except PageFetchError:
            return []

    # If Places handed us a social / Maps link instead of a real site, there is no
    # menu to harvest there: skip the seed (and below, blank the Brave domain) so the
    # menu is recovered by NAME instead of chasing an Instagram profile.
    own_site = "" if is_noise_website(website_url) else website_url

    # The seeds (given URL + site root) are independent fetches; run them concurrently.
    # map_concurrent preserves input order and the dedupe is reapplied in that same
    # order, so the harvested `links` list is identical to the old sequential version.
    seeds = list(_seed_urls(own_site)) if own_site else []
    if seeds:
        for harvested in map_concurrent(_fetch_seed_links, seeds, max_workers=max(1, len(seeds))):
            for url, text in harvested:
                if url not in seen_link_urls:
                    seen_link_urls.add(url)
                    links.append((url, text))

    # Multi-location brands: the per-location menu lives on a subdomain the apex only
    # links to as a place. Follow the subdomain matching the diner's address so its
    # menu links get harvested too (the apex alone yields no menu otherwise).
    location_seeds = _address_matched_subdomain_seeds(
        links, website_url=own_site, address=address or ""
    )
    if location_seeds:
        for harvested in map_concurrent(
            _fetch_seed_links, location_seeds, max_workers=max(1, len(location_seeds))
        ):
            for url, text in harvested:
                if url not in seen_link_urls:
                    seen_link_urls.add(url)
                    links.append((url, text))

    for (url, text), kind in _select_links(links[:max_links], api_key=api_key, model=model):
        candidates.append(Candidate(url=url, anchor_text=text, kind=kind, source="link"))

    # Second hop: a "Menu"/"Allergens" link often points to a PAGE that itself just
    # links to the real menu/allergen PDFs or sub-pages (common on Squarespace/Wix --
    # e.g. a /menu page whose body is buttons to food/dinner/drinks PDFs). The homepage
    # harvest only sees the page, not the PDFs on it, so we follow a few such pages one
    # level deeper. Mechanical + heuristic (no extra LLM call); bounded by page count.
    candidates.extend(_harvest_second_hop(candidates, user_agent=user_agent))

    # Web-search fallback for off-site / CDN allergen PDFs. Fire it unless we
    # already found a STATIC allergen document on-site: a JS allergen tool (e.g. a
    # "nutrition calculator" link) yields nothing to the static parser, so it must
    # not suppress the hunt for a parseable PDF.
    has_static_allergen_doc = any(
        c.kind in ("allergen", "nutrition") and _is_pdf_url(c.url)
        for c in candidates
    )
    if brave_api_key and restaurant_name and not has_static_allergen_doc:
        # Single consolidated allergen web-search (on-domain PDF + off-domain PDF +
        # off-domain page) -- replaces two overlapping helpers that fired ~6 queries.
        candidates.extend(
            _brave_allergen_sources(
                website_url=own_site,
                restaurant_name=restaurant_name,
                address=address,
                api_key=brave_api_key,
                user_agent=user_agent,
            )
        )

    return _finalize(candidates, max_candidates)


def _brave_allergen_sources(
    *,
    website_url: str,
    restaurant_name: str,
    address: str | None,
    api_key: str,
    user_agent: str,
    limit: int = 8,
) -> list[Candidate]:
    """Consolidated allergen web search: a small set of DISTINCT, high-value queries
    (on-domain PDF, off-domain PDF, off-domain page) instead of the prior two
    helpers' ~6 overlapping queries. Queries fire CONCURRENTLY through the shared
    Brave token bucket (which keeps us under the plan's per-second limit); results
    from ALL queries are collected, then ranked together by provenance (query order
    is only a stable tiebreak within a rank).
    NOTE: off-domain copies can be stale -- scoring should prefer official+recent.
    Provenance guard: queries are biased to the home region, and foreign-ccTLD
    results are DEMOTED to a last-resort fallback (not dropped -- they may be the only
    allergen data; content-locale validation later labels them as from-another-region
    rather than silently trusting them)."""
    try:
        from safeplate.brave_search import BraveSearchError, brave_web_search
    except Exception:
        return []

    domain = urlparse(website_url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    city = _city_token(address)
    home_country = _home_country(address, website_url)
    region = _region_token(home_country)
    official_regdomain = registrable_domain(domain)
    queries = _allergen_queries(
        domain=domain, restaurant_name=restaurant_name, city=city, region=region
    )

    def _run(query: str) -> tuple[str, list]:
        try:
            return query, brave_web_search(
                query=query, api_key=api_key, user_agent=user_agent, count=6
            )
        except BraveSearchError:
            return query, []

    # Collect across all queries first, THEN rank/filter by provenance, so a
    # foreign result returned by the high-value query can't crowd out the correct
    # one (ranking, not raw query order, decides the final priority).
    collected: list[Candidate] = []
    seen: set[str] = set()
    for query, results in map_concurrent(_run, queries, max_workers=len(queries)):
        for result in results:
            if result.url in seen:
                continue
            seen.add(result.url)
            base = result.url.lower().split("?")[0].split("#")[0]
            haystack = f"{result.url} {result.title or ''}".lower()
            if base.endswith(".pdf"):
                collected.append(Candidate(url=result.url, anchor_text=result.title or "",
                                           kind="allergen", source="brave_pdf", reason=query))
            elif "allerg" in haystack or "nutrition" in haystack:
                collected.append(Candidate(url=result.url, anchor_text=result.title or "",
                                           kind="allergen", source="brave", reason=query))
    ranked = _rank_sources(
        collected, official_regdomain=official_regdomain, home_country=home_country
    )
    return ranked[:limit]


def _brave_menu_pdf_candidates(
    *,
    restaurant_name: str,
    address: str | None,
    api_key: str,
    user_agent: str,
    website_url: str = "",
    limit: int = 6,
) -> list[Candidate]:
    """Broad `"<name>" menu filetype:pdf` web search (city/region-biased when known
    -- e.g. 'Din Tai Fung USA menu pdf'), kept to real PDFs. The caller verifies
    each PDF names the restaurant; foreign-ccTLD results are DEMOTED to a last-resort
    fallback (not dropped) and the official domain (when known) ranks first."""
    try:
        from safeplate.brave_search import BraveSearchError, brave_web_search
    except Exception:
        return []
    city = _city_token(address)
    # website_url lets home detection use the site ccTLD when the address lacks a
    # country, and lets the restaurant's own domain rank ahead of off-site copies.
    home_country = _home_country(address, website_url)
    region = _region_token(home_country)
    official_regdomain = registrable_domain(urlparse(website_url or "").netloc)
    queries = _menu_pdf_queries(restaurant_name=restaurant_name, city=city, region=region)

    def _run(query: str) -> tuple[str, list]:
        try:
            return query, brave_web_search(
                query=query, api_key=api_key, user_agent=user_agent, count=8
            )
        except BraveSearchError:
            return query, []

    collected: list[Candidate] = []
    seen: set[str] = set()
    for query, results in map_concurrent(_run, queries, max_workers=len(queries)):
        for res in results:
            base = res.url.lower().split("?")[0].split("#")[0]
            if base.endswith(".pdf") and res.url not in seen:
                seen.add(res.url)
                collected.append(Candidate(url=res.url, anchor_text=res.title or "",
                                           kind="menu", source="brave_menu_pdf", reason=query))
    ranked = _rank_sources(
        collected, official_regdomain=official_regdomain, home_country=home_country
    )
    return ranked[:limit]


def _city_token(address: str | None) -> str | None:
    if not address:
        return None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    # "<street>, <city>, <state>, <zip>" -> the second segment is usually the city.
    return parts[1] if len(parts) >= 2 else None


# --- Home-country / official-source provenance guard --------------------------
# The global chain benchmark surfaced a safety-critical failure: the Brave
# fallback would win a wrong-country allergen matrix (US Burger King <- Malta
# .mt, Starbucks <- Switzerland .ch). A foreign allergen chart is dangerous for
# an allergy app -- different recipes, suppliers, and labelling laws. So we (a)
# bias web-search queries toward the home country and (b) RANK home/official
# sources above foreign ones. Foreign sources are kept as a last-resort fallback
# (per product intent) -- content-locale validation then labels them in the UI as
# from-another-region rather than silently trusting them. Region primitives live
# in region.py (shared, import-cycle-free); re-exported here under the private
# names existing callers/tests use.
from safeplate.extraction2.region import (  # noqa: E402
    home_country as _home_country,
    host_country as _host_country,
    is_foreign_source as _is_foreign_source,
    region_token as _region_token,
)


def _rank_sources(
    cands: list[Candidate], *, official_regdomain: str, home_country: str | None
) -> list[Candidate]:
    """Order web-search candidates by provenance: the restaurant's own domain
    first, then a home-country ccTLD, then country-neutral hosts, and a foreign
    ccTLD LAST (kept as a fallback, not dropped -- it may be the only allergen
    data, and the UI labels it as from-another-region). Stable within a rank, so
    the original query priority is preserved as a tiebreak."""

    def _provenance(c: Candidate) -> int:
        host = urlparse(c.url).netloc.lower()
        if official_regdomain and registrable_domain(host) == official_regdomain:
            return 0
        if home_country and _host_country(host) == home_country:
            return 1
        if _is_foreign_source(c.url, home_country):
            return 3  # wrong country -> last resort, surfaced with a region notice
        return 2  # country-neutral host (CDN/aggregator); can't tell the country

    # Provenance dominates (a foreign-but-newer chart must NOT beat the official one);
    # recency breaks ties so the freshest official/home copy wins.
    return sorted(cands, key=lambda c: (_provenance(c), -source_recency(c.url)))


def _allergen_queries(
    *, domain: str, restaurant_name: str, city: str | None, region: str
) -> list[str]:
    """Brave allergen queries: on-domain PDF, off-domain PDF, off-domain page.
    The off-domain queries are biased toward the home region (city if known, else
    country) so the search surfaces the correct-country chart, not a foreign one."""
    queries: list[str] = []
    if domain:
        queries.append(f"site:{domain} filetype:pdf allergen OR nutrition")
    if region:
        queries.append(f'"{restaurant_name}" {region} allergen filetype:pdf')
    else:
        queries.append(f'"{restaurant_name}" allergen filetype:pdf')
    if city:
        queries.append(f'"{restaurant_name}" "{city}" allergen menu')
    elif region:
        queries.append(f'"{restaurant_name}" {region} allergen menu')
    else:
        queries.append(f'"{restaurant_name}" allergen menu')
    return list(dict.fromkeys(q for q in queries if q.strip()))


def _menu_pdf_queries(
    *, restaurant_name: str, city: str | None, region: str
) -> list[str]:
    """Brave menu-PDF queries, region-biased (e.g. 'Din Tai Fung USA menu pdf')."""
    queries: list[str] = []
    if city:
        queries.append(f'"{restaurant_name}" "{city}" menu filetype:pdf')
    if region:
        queries.append(f'"{restaurant_name}" {region} menu filetype:pdf')
    queries.append(f'"{restaurant_name}" menu filetype:pdf')
    return list(dict.fromkeys(q for q in queries if q.strip()))


def _pdf_mentions(text: str, restaurant_name: str) -> bool:
    """Collision guard: the PDF must actually name this restaurant (compact match,
    or a majority of its >=3-char name tokens)."""
    if not text:
        return False
    low = text.lower()
    compact = "".join(restaurant_name.lower().split())
    if compact and compact in "".join(low.split()):
        return True
    tokens = [t for t in restaurant_name.lower().split() if len(t) >= 3]
    if tokens:
        hits = sum(1 for token in tokens if token in low)
        return hits >= max(1, (len(tokens) + 1) // 2)
    return False


# High-value STRUCTURED allergen sources: a dish x allergen grid (HTML or PDF-vision)
# or app-embedded allergen JSON. Matching items are grounded per-dish allergen data --
# the strongest, most complete signal for a safety app.
_MATRIX_METHODS = frozenset(
    {"allergen_matrix", "gemini_allergen_matrix", "gemini_pdf_matrix", "embedded_allergens"}
)


def _is_structured_matrix(method: str | None) -> bool:
    low = (method or "").lower()
    return low in _MATRIX_METHODS or "matrix" in low


# Bump when extraction logic changes so cached results don't go stale across code
# changes; menus themselves are stable within the TTL.
# v2: second-hop discovery (menu-page -> menu-PDF) -- invalidates results cached
# before it (e.g. restaurants that wrongly cached as "no menu").
# v3: nut-free-claim detection (+ scanning nut-free-mentioning pages for signals).
# v4: matrix early-exit (stop once a menu-covering allergen matrix is found).
# v5: per-source region stamp on coverage (content-locale provenance) -- old
# entries lack it and would skip the from-another-region notice.
# v6: region stamps in cached coverage were computed from RAW html (CSS classes /
# font-license credits could vote a false foreign region, e.g. "from Mexico" via a
# typemade.mx font credit) -- invalidate so every result is re-stamped from visible text.
_RESULT_CACHE_VERSION = "6"
_RESULT_CACHE_TTL = 7 * 24 * 60 * 60
# "Nothing found" (no items + no signals) is cached too -- so a dead/empty site
# doesn't re-run discovery + the Brave fallback every search -- but with a SHORTER
# TTL than a real hit, since the restaurant may add a menu/allergen page soon.
_RESULT_CACHE_NEGATIVE_TTL = 24 * 60 * 60


# Hosts where MANY distinct restaurants live under one domain, so website_url alone
# can collide two different restaurants. Keying by website_url (NOT name) is otherwise
# deliberate: a chain's branches share one site + menu, so they SHOULD reuse the same
# cache entry. We only add a name discriminator on these shared platforms, preserving
# chain reuse on real own-domain sites.
_SHARED_PLATFORM_HOSTS = (
    "facebook.com", "instagram.com", "toasttab.com", "order.online", "square.site",
    "sites.google.com", "business.site", "linktr.ee", "clover.com", "yelp.com",
    "doordash.com", "ubereats.com", "grubhub.com", "wixsite.com", "godaddysites.com",
    "wordpress.com", "weebly.com", "blogspot.com",
)


def _cache_discriminator(website_url: str, restaurant_name: str | None) -> str:
    """Empty for normal own-domain sites (so chain branches share a cache entry);
    the normalized restaurant name on shared platforms (so two restaurants under the
    same aggregator domain don't collide)."""
    host = urlparse(website_url or "").netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    if any(host == h or host.endswith("." + h) for h in _SHARED_PLATFORM_HOSTS):
        return " ".join((restaurant_name or "").split()).lower()
    return ""


def _result_cache_path(website_url: str, model: str, discriminator: str = ""):
    digest = hashlib.sha1(
        f"{_RESULT_CACHE_VERSION}:{model}:{website_url}:{discriminator}".encode("utf-8")
    ).hexdigest()
    return get_cache_dir() / "extraction2_result" / f"{digest}.json"


def _load_result_cache(website_url: str, model: str, discriminator: str = ""):
    from safeplate.diet_score import DietSignal
    from safeplate.extraction2.schema import (
        AllergySignal,
        CoverageReport,
        MenuExtractionResult,
    )
    from safeplate.menu_text import MenuItemRecord

    try:
        blob = json.loads(
            _result_cache_path(website_url, model, discriminator).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    # A negative (empty) cache entry expires sooner so we re-try dead/thin sites.
    is_negative = not blob.get("items") and not blob.get("signals")
    ttl = _RESULT_CACHE_NEGATIVE_TTL if is_negative else _RESULT_CACHE_TTL
    if time.time() - blob.get("at", 0) > ttl:
        return None
    try:
        return MenuExtractionResult(
            items=[MenuItemRecord(**i) for i in blob["items"]],
            coverage=[CoverageReport(**c) for c in blob["coverage"]],
            allergy_signals=[AllergySignal(**s) for s in blob["signals"]],
            # Older cache blobs predate diet signals -- default to [] rather than KeyError.
            diet_signals=[DietSignal(**d) for d in blob.get("diet_signals", [])],
            llm_calls=0,
        )
    except (KeyError, TypeError):
        return None


def _save_result_cache(website_url: str, model: str, result, discriminator: str = "") -> None:
    from dataclasses import asdict

    path = _result_cache_path(website_url, model, discriminator)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "at": time.time(),
                "items": [asdict(i) for i in result.items],
                "coverage": [asdict(c) for c in result.coverage],
                "signals": [asdict(s) for s in result.allergy_signals],
                "diet_signals": [asdict(s) for s in result.diet_signals],
            }),
            encoding="utf-8",
        )
    except OSError:
        pass


def discover_and_extract(
    website_url: str,
    *,
    user_agent: str,
    restaurant_name: str | None = None,
    address: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    brave_api_key: str | None = None,
    policy=None,
    use_result_cache: bool = False,
    use_cache: bool = True,
):
    """End-to-end: find candidates -> acquire -> extract. Returns (candidates, result).

    `use_result_cache` (app: on; eval: off) returns a cached full extraction for
    this website within the TTL -- skipping ALL discovery + extraction API calls on
    a repeat open. Eval/benchmarks leave it OFF so they always measure fresh logic.
    `use_cache=False` additionally bypasses the per-source caches (HTTP fetch, vision
    matrix, text-LLM) so the run hits the LIVE website -- the 'raw' / no-cache test."""
    from safeplate.extraction2.acquire import acquire
    from safeplate.extraction2.classify import IMAGE_EXTS
    from safeplate.extraction2.pipeline import _fold_allergen_evidence, extract_menu
    from safeplate.extraction2.schema import MenuExtractionResult, Policy

    def _merge_records(records, index, items):
        # Fold each record into `items` by lower-cased name. A name collision UNIONs
        # allergen evidence into the kept record instead of dropping it (R5: completion
        # order must not decide which allergens survive); a new name is appended and
        # indexed. `index` (lower-name -> position in `items`) is updated in place.
        for record in records:
            key = record.item_name.lower()
            if key in index:
                items[index[key]] = _fold_allergen_evidence(items[index[key]], record)
            elif key:
                index[key] = len(items)
                items.append(record)

    cache_model = model or DEFAULT_MODEL
    cache_url = _normalize_cache_url(website_url)  # share one entry across utm/clean URLs
    cache_disc = _cache_discriminator(website_url, restaurant_name)
    if use_result_cache:
        cached = _load_result_cache(cache_url, cache_model, cache_disc)
        if cached is not None:
            return [], cached

    # One wall-clock budget for the ENTIRE per-restaurant extraction -- discovery,
    # acquisition, the extraction loop, AND the post-loop phases (api_capture, Brave
    # menu-PDF recovery, signals) all run under it, so a pathologically slow site
    # can't tie up a worker for minutes on the drawer path. A budget-truncated result
    # is not cached (it may be missing an allergen source and look wrongly safe).
    _EXTRACT_BUDGET_S = 90.0
    overall_deadline = time.monotonic() + _EXTRACT_BUDGET_S
    timed_out = False

    candidates = discover_sources(
        website_url, user_agent=user_agent, restaurant_name=restaurant_name,
        address=address, api_key=api_key, model=model, brave_api_key=brave_api_key,
    )
    # Acquire all candidates CONCURRENTLY (network-bound -- big latency win, no
    # accuracy change).
    def _acquire(cand: Candidate):
        low = cand.url.lower().split("?")[0]
        source_type = "pdf" if low.endswith(".pdf") else (
            "image" if low.endswith(IMAGE_EXTS) else "website_link")
        try:
            return cand.url, acquire(cand.url, source_type=source_type,
                                     user_agent=user_agent, use_cache=use_cache)
        except Exception:
            return cand.url, None

    # If discovery alone already ate the budget, don't start acquisition/extraction.
    if time.monotonic() >= overall_deadline:
        return candidates, MenuExtractionResult(items=[], coverage=[], llm_calls=0)

    payload_by_url: dict[str, object] = {
        url: payload
        for url, payload in map_concurrent(_acquire, candidates, max_workers=8)
        if payload is not None
    }

    def _extract_one(payload: Any):
        return extract_menu(
            [payload], policy=policy or Policy.HYBRID, llm_enabled=bool(api_key),
            gemini_api_key=api_key, gemini_model=model, use_cache=use_cache,
        )

    # Extract candidates in priority order (allergen first) with EARLY-STOP and a
    # ROLLING concurrency window: keep `_BATCH` extractions in flight and absorb each
    # as it COMPLETES (as_completed) so one slow PDF/page no longer blocks the others
    # (the old per-batch map_concurrent waited for the slowest in each batch). Stop
    # submitting once a matrix is found AND we have enough items, or the source cap is
    # hit. An overall deadline guards against a pathologically slow site; a
    # deadline-truncated (partial) result is NOT cached -- a partial extraction could
    # miss an allergen and wrongly look safer. Preserves validation-by-extraction.
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    # _MAX_SOURCES lowered 8 -> 4: extract only the few best candidate sources per
    # restaurant (allergen/menu pages are tried first), so a cold search finishes more
    # of its restaurants inside the list budget instead of grinding through long tails.
    _BATCH, _ENOUGH_MENU, _MAX_SOURCES = 3, 30, 4
    # A confident, menu-covering structured allergen matrix (dish x allergen grid) IS
    # the whole menu plus per-dish allergens in one deterministic parse -- so once one
    # yields this many dishes, stop crawling/LLM-ing other sources (speed + the most
    # complete, grounded allergen coverage). Floor of 10 guards against a stray/partial
    # matrix triggering an early exit on a thin grid.
    _MATRIX_ENOUGH = 10
    result = MenuExtractionResult(items=[], coverage=[], llm_calls=0)
    items_by_name: dict[str, int] = {}  # name -> index in result.items (for allergen-union merge)
    allergen_dishes = 0
    matrix_dishes = 0
    processed = 0
    have_allergens = False
    work = [c for c in candidates if c.kind != "allergy_info"]
    work_iter = iter(work)
    executor = ThreadPoolExecutor(max_workers=_BATCH)
    inflight: dict[Any, Any] = {}

    def _submit_next() -> bool:
        nonlocal processed
        while processed < _MAX_SOURCES:
            cand = next(work_iter, None)
            if cand is None:
                return False
            if have_allergens and cand.kind in ("allergen", "nutrition"):
                continue  # already have a matrix -> skip redundant allergen sources
            payload = payload_by_url.get(cand.url)
            if payload is None:
                continue
            inflight[executor.submit(_extract_one, payload)] = cand
            processed += 1
            return True
        return False

    try:
        deadline = overall_deadline
        for _ in range(_BATCH):
            if not _submit_next():
                break
        while inflight:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            done, _pending = wait(set(inflight), timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                timed_out = True
                break
            for fut in done:
                inflight.pop(fut, None)
                try:
                    sub = fut.result()
                except Exception:
                    continue
                result.llm_calls += sub.llm_calls
                result.coverage.extend(sub.coverage)
                if sub.incomplete:
                    # A menu chunk's LLM call failed -> this source is partial. Treat
                    # it like a deadline truncation: keep the items we got this run but
                    # don't cache, so the next open re-extracts the missing chunk.
                    timed_out = True
                _merge_records(sub.items, items_by_name, result.items)
            # Recompute coverage counters from the MERGED set so a folded-in matrix
            # row counts toward the early-stop thresholds.
            allergen_dishes = sum(1 for it in result.items if it.allergen_terms)
            matrix_dishes = sum(
                1 for it in result.items if _is_structured_matrix(it.extraction_method)
            )
            if allergen_dishes >= 3:
                have_allergens = True
            # Matrix early-exit: the dish x allergen grid already covers the menu, so
            # don't pay to crawl/LLM the remaining sources.
            if matrix_dishes >= _MATRIX_ENOUGH:
                have_allergens = True
                break
            if have_allergens and len(result.items) >= _ENOUGH_MENU:
                break
            for _ in range(len(done)):  # refill the window as slots free up
                if not _submit_next():
                    break
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # Tier 2a: if nothing carried allergen data, the tool likely fetches it from a
    # backend -- try to capture that API directly (free, no browser). Targeted to
    # the allergen/nutrition/menu candidates so it only runs when it's worth it.
    if not any(it.allergen_terms for it in result.items) and time.monotonic() < overall_deadline:
        from safeplate.extraction2.api_capture import capture_allergen_api

        idx = {it.item_name.lower(): i for i, it in enumerate(result.items)}
        for cand in candidates:
            if time.monotonic() >= overall_deadline:
                timed_out = True  # don't cache: we may have skipped an allergen source
                break
            if cand.kind not in ("allergen", "nutrition", "menu"):
                continue
            captured, cap_coverage = capture_allergen_api(cand.url, user_agent=user_agent)
            _merge_records(captured, idx, result.items)
            # Record per-endpoint coverage (carries the region stamp) so captured
            # backend allergen data can trigger the from-another-region notice.
            result.coverage.extend(cap_coverage)
            if any(it.allergen_terms for it in result.items):
                break  # got allergen data; stop probing further candidates

    # Gated menu-PDF recovery: when on-site extraction is THIN (e.g. a fully
    # JS-rendered menu), a static menu PDF often exists off-site. Fire only on thin
    # results, and require the PDF to actually name this restaurant -- a collision
    # guard against same-name-different-restaurant PDFs for common independents.
    if (
        brave_api_key
        and api_key
        and restaurant_name
        and len(result.items) < _MENU_PDF_THIN
        and time.monotonic() < overall_deadline
    ):
        seen_urls = {c.url for c in candidates}
        idx = {it.item_name.lower(): i for i, it in enumerate(result.items)}
        for cand in _brave_menu_pdf_candidates(
            restaurant_name=restaurant_name, address=address,
            api_key=brave_api_key, user_agent=user_agent, website_url=website_url,
        ):
            if time.monotonic() >= overall_deadline:
                timed_out = True  # budget hit mid-recovery -> partial, don't cache
                break
            if cand.url in seen_urls:
                continue
            seen_urls.add(cand.url)
            try:
                payload = acquire(cand.url, source_type="pdf", user_agent=user_agent,
                                  use_cache=use_cache)
            except Exception:
                continue
            if not _pdf_mentions(payload.text, restaurant_name):
                continue  # wrong-restaurant collision -> skip
            sub = extract_menu(
                [payload], policy=policy or Policy.HYBRID, llm_enabled=True,
                gemini_api_key=api_key, gemini_model=model, use_cache=use_cache,
            )
            _merge_records(sub.items, idx, result.items)
            result.coverage.extend(sub.coverage)
            result.llm_calls += sub.llm_calls
            candidates.append(cand)
            if len(result.items) >= _MENU_PDF_THIN:
                break

    # Directive #3: capture restaurant-level allergy-handling signals from narrative
    # allergy / allergen pages (e.g. "allergy-friendly kitchen", cross-contact, "ask
    # staff") -- valuable even when no dish x allergen matrix exists. Bounded to a few
    # pages to keep cost down; grounded quotes only. Skipped if the overall budget is
    # already spent.
    if api_key and time.monotonic() < overall_deadline:
        from safeplate.extraction2.allergy_signals import (
            extract_allergy_signals,
            extract_diet_signals,
        )

        signal_payloads: list[Any] = []
        # 1) Narrative allergy/nutrition pages (richest allergy-handling prose).
        for cand in candidates:
            if len(signal_payloads) >= 2:  # 2 is plenty; chain location pages dupe
                break
            if cand.kind not in ("allergy_info", "allergen", "nutrition"):
                continue
            payload = payload_by_url.get(cand.url)
            # Narrative signals live on HTML pages; PDFs are matrices (no prose).
            if payload is None or getattr(payload, "source_type", "") == "pdf":
                continue
            signal_payloads.append(payload)
        # 2) Fill remaining slots with any extracted HTML page that LITERALLY mentions
        #    nut-free (catches a dedicated nut-free kitchen whose claim sits on a menu/
        #    main page, not a narrative allergy page). Cheap text pre-filter -> no extra
        #    LLM call unless the page actually says it.
        if len(signal_payloads) < 2:
            chosen = {getattr(p, "url", "") for p in signal_payloads}
            for url, payload in payload_by_url.items():
                if len(signal_payloads) >= 2:
                    break
                if url in chosen or getattr(payload, "source_type", "") == "pdf":
                    continue
                if _mentions_nut_free(getattr(payload, "text", "") or ""):
                    signal_payloads.append(payload)
        seen_sig: set[tuple] = set()
        for signal in map_concurrent(
            lambda p: extract_allergy_signals(p, api_key=api_key, model=model),
            signal_payloads,
            max_workers=2,
        ):
            if signal is None:
                continue
            # Dedupe near-identical signals (same flags + same statements).
            sig_key = (
                signal.allergy_friendly_claim, signal.cross_contact_warning,
                signal.ask_staff, signal.allergen_menu_available, signal.nut_free_claim,
                tuple(sorted(signal.statements)),
            )
            if sig_key not in seen_sig:
                seen_sig.add(sig_key)
                result.allergy_signals.append(signal)

        # Diet accommodation ("dishes can be made vegan/vegetarian") -- reuses the
        # SAME cached page-LLM call above (no extra network request); feeds diet
        # compatibility only, never allergen risk.
        seen_diet: set[tuple] = set()
        for diet_sigs in map_concurrent(
            lambda p: extract_diet_signals(p, api_key=api_key, model=model),
            signal_payloads,
            max_workers=2,
        ):
            for sig in diet_sigs or []:
                diet_key = (sig.diet, sig.quote, sig.url)
                if diet_key not in seen_diet:
                    seen_diet.add(diet_key)
                    result.diet_signals.append(sig)

    # Don't cache a deadline-truncated extraction: it may be missing an allergen
    # source and would wrongly look safer on the next (cache-hit) open.
    if use_result_cache and not timed_out:
        _save_result_cache(cache_url, cache_model, result, cache_disc)
    return candidates, result


def _normalize_cache_url(url: str) -> str:
    """Cache key normalization: drop query/fragment + trailing slash and lowercase the
    host, so the SAME page cached under tracking params (a provider's
    '...html?utm_source=Google') and a clean URL share ONE entry. Otherwise the list
    (provider URL with utm) and the drawer (clean URL) extract the restaurant twice
    and their menu-backed verdicts can diverge. Only the cache KEY is normalized;
    discovery still fetches the original URL."""
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}" if host else (url or "")


def _is_pdf_url(url: str) -> bool:
    """True for PDF links even with a query/fragment (Shopify & many CDNs serve
    '...menu.pdf?v=123' -- a bare endswith('.pdf') misses those)."""
    return url.lower().split("?")[0].split("#")[0].endswith(".pdf")


def _mentions_nut_free(text: str) -> bool:
    """Cheap pre-filter: does the page text literally claim nut-free? Used to decide
    whether a non-narrative page is worth an allergy-signal LLM call."""
    low = (text or "").lower()
    return any(p in low for p in (
        "nut free", "nut-free", "no nuts", "without nuts", "free of nuts",
        "free from nuts", "peanut free", "peanut-free", "tree nut free", "tree-nut-free",
    ))


# Shared canonical eTLD+1 helper (re-exported under the old private name so existing
# callers/tests are unchanged).
_registrable_domain = registrable_domain


# A place's Google-Places "website" is sometimes a social profile or a Maps link,
# not a real site with a menu. Seeding those wastes a fetch, pollutes provenance
# (an Instagram URL is not the official domain), and sends a useless `site:instagram`
# query to Brave. Flag them so discovery skips the seed and recovers the menu by name.
# Delivery / menu aggregators (Uber Eats, e-food, Zomato, TripAdvisor) are deliberately
# NOT here: they carry menu content and stay usable as sources.
_SOCIAL_DOMAINS = (
    "instagram.com", "facebook.com", "fb.com", "fb.me", "tiktok.com",
    "twitter.com", "x.com", "youtube.com", "youtu.be", "linkedin.com",
    "pinterest.com", "snapchat.com", "threads.net", "wa.me", "t.me",
    "vk.com", "weibo.com",
)
_MAPS_HINTS = ("maps.google.", "google.com/maps", "goo.gl/maps", "maps.app.goo.gl")


def is_noise_website(url: str) -> bool:
    """True when a Places "website" is a social-media or Maps link (no menu to read),
    so discovery should skip seeding it and recover the menu by name instead. Delivery
    / menu aggregators are NOT noise -- they carry menus and stay usable as sources."""
    if not url or not url.strip():
        return False
    parsed = urlparse(url if "//" in url else "http://" + url.strip())
    host = parsed.netloc.lower().split(":")[0].strip(".")
    if not host:
        return False
    if any(host == d or host.endswith("." + d) for d in _SOCIAL_DOMAINS):
        return True
    full = (host + parsed.path).lower()
    return any(hint in full for hint in _MAPS_HINTS)


def _seed_urls(website_url: str) -> list[str]:
    """Discovery seeds: the given URL, plus the site ROOT when the given URL is a
    deeper page (e.g. a chain's /locations/<city> landing). The full menu / per-
    category links frequently live only on the homepage, not the location page."""
    seeds = [website_url]
    p = urlparse(website_url)
    if p.netloc and p.path.strip("/"):
        root = f"{p.scheme or 'https'}://{p.netloc}/"
        if root != website_url:
            seeds.append(root)
    return seeds


# Multi-location brands host each location's menu on its own subdomain
# (radhusplassen.derpepperngror.no). Seeded on the brand apex, the menu is two hops
# away behind a location link the classifier reads as a place, not a menu -- so
# discovery finds nothing. Follow the subdomain whose label matches the diner's
# address locality (which we already have) as an extra seed, so that location's menu
# links get harvested. Bounded (a couple of matches) so a brand can't fan us out.
_NORDIC_FOLD = str.maketrans({
    "ø": "o", "æ": "ae", "å": "a", "ä": "a", "ö": "o", "ü": "u",
    "ð": "d", "þ": "th", "ß": "ss",
})


def _norm_locality(text: str) -> str:
    folded = text.lower().translate(_NORDIC_FOLD)
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(c for c in decomposed if c.isalnum() and not unicodedata.combining(c))


def _fold_accents(text: str) -> str:
    """Strip diacritics but keep word structure (spaces/punct), so an accented menu
    word matches its ASCII token: 'menú'/'menü'/'menù' -> 'menu', 'nutrición' ->
    'nutricion', 'à la carte' -> 'a la carte'. Greek/Cyrillic/CJK are left legible."""
    folded = text.lower().translate(_NORDIC_FOLD)
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _address_locality_tokens(address: str) -> set[str]:
    """Normalized locality tokens from an address: each comma segment collapsed to
    one token (so 'Aker Brygge' -> 'akerbrygge') plus its individual words, accents
    folded ('Rådhusplassen' -> 'radhusplassen'). Short tokens are dropped to avoid
    spurious subdomain matches."""
    tokens: set[str] = set()
    for segment in address.split(","):
        collapsed = _norm_locality(segment)
        if len(collapsed) >= 4:
            tokens.add(collapsed)
        for word in segment.split():
            normalized = _norm_locality(word)
            if len(normalized) >= 4:
                tokens.add(normalized)
    return tokens


def _subdomain_label(host: str, base_reg: str) -> str:
    host = host.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host.endswith("." + base_reg) and host != base_reg:
        return host[: -len("." + base_reg)]
    return ""


def _address_matched_subdomain_seeds(
    links: list[tuple[str, str]], *, website_url: str, address: str, limit: int = 2
) -> list[str]:
    """Same-registrable-domain subdomains (from the harvested links) whose label
    matches the address locality -> extra seed home URLs. The current host, the apex,
    www, and off-site hosts are never seeded; unmatched locations are ignored."""
    if not address.strip():
        return []
    base_reg = registrable_domain(urlparse(website_url).netloc)
    if not base_reg:
        return []
    current = urlparse(website_url).netloc.lower().split(":")[0]
    if current.startswith("www."):
        current = current[4:]
    tokens = _address_locality_tokens(address)
    if not tokens:
        return []
    matched: list[str] = []
    seen_hosts: set[str] = set()
    for url, _text in links:
        host = urlparse(url).netloc.lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        if host == current or host in seen_hosts:
            continue
        label = _norm_locality(_subdomain_label(host, base_reg))
        if not label:
            continue
        hit = label in tokens or any(
            len(token) >= 6 and (token in label or label in token) for token in tokens
        )
        if hit:
            seen_hosts.add(host)
            matched.append(f"https://{host}/")
            if len(matched) >= limit:
                break
    return matched


def _harvest_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """All same-SITE links (any subdomain of the registrable domain -- restaurants
    often put the menu on order./orders./menu. subdomains) plus any PDF (allergen
    PDFs frequently live on a CDN host), as (absolute_url, anchor_text). Mechanical,
    no keyword filtering."""
    soup = make_soup(html)
    base_reg = _registrable_domain(urlparse(base_url).netloc)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        host = urlparse(url).netloc.lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        is_pdf = _is_pdf_url(url)
        same_site = bool(base_reg) and (host == base_reg or host.endswith("." + base_reg))
        if not same_site and not is_pdf:
            continue  # drop genuinely off-site non-PDF links (social, partners, etc.)
        seen.add(url)
        text = " ".join((anchor.get_text(" ", strip=True) or "").split())[:140]
        out.append((url, text))
    return out


# Follow at most this many menu/allergen PAGES one hop deeper. Usually 1 (the "Menu"
# page); capped so a link-heavy site can't explode the fetch count.
_SECOND_HOP_PAGES = 3
_SECOND_HOP_TOKENS = (
    "menu", "food", "lunch", "dinner", "breakfast", "brunch", "drink", "wine",
    "cocktail", "beverage", "allergen", "allergy", "nutrition", "dietary", ".pdf",
)


def _harvest_second_hop(candidates: list[Candidate], *, user_agent: str) -> list[Candidate]:
    """Follow menu/allergen HTML PAGE candidates one level deeper and return the
    PDFs / menu sub-pages they link to. Catches the homepage -> menu-page -> menu-PDF
    pattern that one-hop harvesting misses. Validation-by-extraction still decides
    whether each new candidate is real, so over-collecting is harmless."""
    from safeplate.extraction2.classify import IMAGE_EXTS

    seen = {c.url for c in candidates}
    eligible = [
        c for c in candidates
        if c.kind in ("menu", "allergen", "nutrition", "allergy_info")
        and not c.url.lower().split("?")[0].endswith((".pdf",) + IMAGE_EXTS)
    ]
    # Follow allergen/nutrition pages FIRST (they hold the matrices) and dedupe
    # near-identical URLs (e.g. /menu vs /menu?utm_source=...) so the few hops we
    # spend aren't wasted on the same page or on low-value pages before the allergen one.
    _hop_rank = {"allergen": 0, "nutrition": 1, "allergy_info": 2, "menu": 3}
    eligible.sort(key=lambda c: _hop_rank.get(c.kind, 9))
    pages: list[Candidate] = []
    seen_norm: set[str] = set()
    for c in eligible:
        norm = c.url.lower().split("?")[0].split("#")[0]
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        pages.append(c)
        if len(pages) >= _SECOND_HOP_PAGES:
            break

    def _children(parent: Candidate) -> list[Candidate]:
        try:
            html = fetch_html_page(parent.url, user_agent=user_agent).html
        except PageFetchError:
            return []
        found: list[Candidate] = []
        for url, text in _harvest_links(html, parent.url):
            low = url.lower().split("?")[0]
            is_doc = low.endswith(".pdf") or low.endswith(IMAGE_EXTS)
            haystack = f"{url} {text}".lower()
            if is_doc or any(tok in haystack for tok in _SECOND_HOP_TOKENS):
                found.append(
                    Candidate(url=url, anchor_text=text, kind=parent.kind, source="link2")
                )
        return found

    # Fetch the (few) second-hop pages concurrently. map_concurrent preserves page
    # order and the `seen` dedupe is reapplied sequentially in that order, so the
    # resulting candidate list is identical to the old sequential fetch.
    out: list[Candidate] = []
    for child_list in map_concurrent(_children, pages, max_workers=max(1, len(pages))):
        for cand in child_list:
            if cand.url in seen:
                continue
            seen.add(cand.url)
            out.append(cand)
    return out


def _select_links(
    links: list[tuple[str, str]], *, api_key: str | None, model: str | None
) -> list[tuple[tuple[str, str], str]]:
    if not links:
        return []
    # COST WIN: when the cheap token heuristic already found an unambiguous
    # high-value page (an allergen/allergy/nutrition link, or a menu/allergen PDF),
    # skip the Gemini link-select call entirely -- the LLM is only worth its quota on
    # AMBIGUOUS sites (generic anchor text, no obvious tokens).
    heuristic = _heuristic_select(links)
    if _heuristic_is_confident(heuristic):
        return heuristic
    if api_key:
        try:
            return _llm_select(links, api_key=api_key, model=model or DEFAULT_MODEL)
        except GeminiMenuError:
            pass  # fall back to the token heuristic
    return heuristic


def _heuristic_is_confident(selected: list[tuple[tuple[str, str], str]]) -> bool:
    """True when the heuristic found something we'd be happy to extract without the
    LLM: an allergen/allergy/nutrition page (the matrices we most want), or any
    PDF (a static, parseable document). A plain '/menu' alone is NOT enough -- the
    LLM often finds a better allergen page among ambiguous links."""
    for (url, _text), kind in selected:
        if kind in ("allergen", "allergy_info", "nutrition") or _is_pdf_url(url):
            return True
    return False


def _llm_select(
    links: list[tuple[str, str]], *, api_key: str, model: str
) -> list[tuple[tuple[str, str], str]]:
    lines = [
        f"{i}: text={text!r} path={urlparse(url).path or '/'}"
        for i, (url, text) in enumerate(links)
    ]
    request = {
        "system_instruction": {"parts": [{"text": SELECT_SYSTEM}]},
        "contents": [{"parts": [{"text": "Links:\n" + "\n".join(lines)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": SELECT_SCHEMA,
        },
    }
    parsed = _call_with_retry(request, api_key=api_key, model=model)
    out: list[tuple[tuple[str, str], str]] = []
    for entry in parsed.get("selected", []):
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        kind = str(entry.get("kind", "")).lower().strip()
        if 0 <= idx < len(links) and kind in RELEVANT_KINDS:
            out.append((links[idx], kind))
    return out


def _heuristic_select(links: list[tuple[str, str]]) -> list[tuple[tuple[str, str], str]]:
    out: list[tuple[tuple[str, str], str]] = []
    for url, text in links:
        raw = f"{url} {text}".lower()
        # Match tokens against BOTH the raw text and an accent-folded copy: raw keeps
        # CJK/Cyrillic intact, folded lets an ASCII token match its accented form.
        folded = _fold_accents(raw)
        for kind, tokens in _KIND_TOKENS.items():
            if any(token in raw or token in folded for token in tokens):
                out.append(((url, text), kind))
                break
    return out


def _finalize(candidates: list[Candidate], max_candidates: int) -> list[Candidate]:
    best: dict[str, Candidate] = {}
    for cand in candidates:
        existing = best.get(cand.url)
        if existing is None or _KIND_PRIORITY[cand.kind] < _KIND_PRIORITY[existing.kind]:
            best[cand.url] = cand
    deduped = _collapse_dated_pdf_reuploads(list(best.values()))
    # Order by: kind (allergen first), then a STATIC PDF over a JS tool of the same
    # kind (a PDF is parseable; a JS tool often renders to nothing), then RECENCY so a
    # current menu is reached before a stale one (and survives the candidate cap /
    # extraction early-exit). Recency is only ever a within-kind tiebreak -- a dated
    # plain menu never outranks an allergen chart.
    ordered = sorted(
        deduped,
        key=lambda c: (_KIND_PRIORITY[c.kind],
                       0 if _is_pdf_url(c.url) else 1,
                       -source_recency(c.url)),
    )
    return ordered[:max_candidates]


def _collapse_dated_pdf_reuploads(cands: list[Candidate]) -> list[Candidate]:
    """Drop an older copy of a menu PDF that has been re-uploaded under a newer date
    (same host + same date-stripped filename stem), keeping the freshest. Only dated
    PDFs are eligible; undated PDFs and non-PDF candidates always pass through, so a
    breakfast vs dinner menu (different stems) and a stable undated URL are untouched."""
    newest: dict[str, Candidate] = {}
    passthrough: list[Candidate] = []
    for c in cands:
        recency = source_recency(c.url)
        if not _is_pdf_url(c.url) or recency == 0.0:
            passthrough.append(c)
            continue
        key = dated_duplicate_key(c.url)
        incumbent = newest.get(key)
        if incumbent is None or recency > source_recency(incumbent.url):
            newest[key] = c
    return passthrough + list(newest.values())
