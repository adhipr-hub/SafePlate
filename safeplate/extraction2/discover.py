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
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from safeplate.concurrency import map_concurrent
from safeplate.config import get_cache_dir
from safeplate.extraction2.interpret_llm import DEFAULT_MODEL, _call_with_retry
from safeplate.gemini_menu import GeminiMenuError
from safeplate.page_fetch import PageFetchError, fetch_html_page
from safeplate.soup import make_soup

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
    "allergen": ("allerg", "allergi", "allergè", "alergen", "アレルゲン", "过敏", "過敏", "알레르"),
    "nutrition": ("nutrition", "nutritional", "nährwert", "valeurs nutri", "栄養", "营养", "영양"),
    "menu": ("menu", "carte", "speisekarte", "メニュー", "菜单", "菜單", "메뉴", "carta", "speise"),
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

    # The seeds (given URL + site root) are independent fetches; run them concurrently.
    # map_concurrent preserves input order and the dedupe is reapplied in that same
    # order, so the harvested `links` list is identical to the old sequential version.
    seeds = list(_seed_urls(website_url))
    for harvested in map_concurrent(_fetch_seed_links, seeds, max_workers=max(1, len(seeds))):
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
                website_url=website_url,
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
    Brave token bucket (which keeps us under the plan's per-second limit), processed
    in query order so the highest-value query still ranks first; early-break once we
    have enough.
    NOTE: off-domain copies can be stale -- scoring should prefer official+recent."""
    try:
        from safeplate.brave_search import BraveSearchError, brave_web_search
    except Exception:
        return []

    domain = urlparse(website_url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    city = _city_token(address)
    queries: list[str] = []
    if domain:
        queries.append(f"site:{domain} filetype:pdf allergen OR nutrition")
    queries.append(f'"{restaurant_name}" allergen filetype:pdf')          # off-domain PDF (high value)
    queries.append(
        f'"{restaurant_name}" "{city}" allergen menu' if city
        else f'"{restaurant_name}" allergen menu'                          # off-domain page
    )
    queries = list(dict.fromkeys(q for q in queries if q.strip()))

    def _run(query: str) -> tuple[str, list]:
        try:
            return query, brave_web_search(
                query=query, api_key=api_key, user_agent=user_agent, count=6
            )
        except BraveSearchError:
            return query, []

    out: list[Candidate] = []
    seen: set[str] = set()
    for query, results in map_concurrent(_run, queries, max_workers=len(queries)):
        for result in results:
            if result.url in seen:
                continue
            seen.add(result.url)
            base = result.url.lower().split("?")[0].split("#")[0]
            haystack = f"{result.url} {result.title or ''}".lower()
            if base.endswith(".pdf"):
                out.append(Candidate(url=result.url, anchor_text=result.title or "",
                                     kind="allergen", source="brave_pdf", reason=query))
            elif "allerg" in haystack or "nutrition" in haystack:
                out.append(Candidate(url=result.url, anchor_text=result.title or "",
                                     kind="allergen", source="brave", reason=query))
        if len(out) >= limit:
            break
    return out[:limit]


def _brave_menu_pdf_candidates(
    *,
    restaurant_name: str,
    address: str | None,
    api_key: str,
    user_agent: str,
    limit: int = 6,
) -> list[Candidate]:
    """Broad `"<name>" menu filetype:pdf` web search (city-biased when known), kept
    to real PDFs. The caller verifies each PDF actually names the restaurant."""
    try:
        from safeplate.brave_search import BraveSearchError, brave_web_search
    except Exception:
        return []
    queries: list[str] = []
    city = _city_token(address)
    if city:
        queries.append(f'"{restaurant_name}" "{city}" menu filetype:pdf')
    queries.append(f'"{restaurant_name}" menu filetype:pdf')

    def _run(query: str) -> tuple[str, list]:
        try:
            return query, brave_web_search(
                query=query, api_key=api_key, user_agent=user_agent, count=8
            )
        except BraveSearchError:
            return query, []

    out: list[Candidate] = []
    seen: set[str] = set()
    for query, results in map_concurrent(_run, queries, max_workers=len(queries)):
        for res in results:
            base = res.url.lower().split("?")[0].split("#")[0]
            if base.endswith(".pdf") and res.url not in seen:
                seen.add(res.url)
                out.append(Candidate(url=res.url, anchor_text=res.title or "",
                                     kind="menu", source="brave_menu_pdf", reason=query))
        if len(out) >= limit:
            break
    return out[:limit]


def _city_token(address: str | None) -> str | None:
    if not address:
        return None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    # "<street>, <city>, <state>, <zip>" -> the second segment is usually the city.
    return parts[1] if len(parts) >= 2 else None


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
_RESULT_CACHE_VERSION = "4"
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
):
    """End-to-end: find candidates -> acquire -> extract. Returns (candidates, result).

    `use_result_cache` (app: on; eval: off) returns a cached full extraction for
    this website within the TTL -- skipping ALL discovery + extraction API calls on
    a repeat open. Eval/benchmarks leave it OFF so they always measure fresh logic."""
    from safeplate.extraction2.acquire import acquire
    from safeplate.extraction2.classify import IMAGE_EXTS
    from safeplate.extraction2.pipeline import extract_menu
    from safeplate.extraction2.schema import MenuExtractionResult, Policy

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
            return cand.url, acquire(cand.url, source_type=source_type, user_agent=user_agent)
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
            gemini_api_key=api_key, gemini_model=model,
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
    seen_names: set[str] = set()
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
                for record in sub.items:
                    key = record.item_name.lower()
                    if key not in seen_names:
                        seen_names.add(key)
                        result.items.append(record)
                        if record.allergen_terms:
                            allergen_dishes += 1
                        if _is_structured_matrix(record.extraction_method):
                            matrix_dishes += 1
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

        seen = {it.item_name.lower() for it in result.items}
        for cand in candidates:
            if time.monotonic() >= overall_deadline:
                timed_out = True  # don't cache: we may have skipped an allergen source
                break
            if cand.kind not in ("allergen", "nutrition", "menu"):
                continue
            for record in capture_allergen_api(cand.url, user_agent=user_agent):
                if record.item_name.lower() not in seen:
                    seen.add(record.item_name.lower())
                    result.items.append(record)
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
        seen_names = {it.item_name.lower() for it in result.items}
        for cand in _brave_menu_pdf_candidates(
            restaurant_name=restaurant_name, address=address,
            api_key=brave_api_key, user_agent=user_agent,
        ):
            if time.monotonic() >= overall_deadline:
                timed_out = True  # budget hit mid-recovery -> partial, don't cache
                break
            if cand.url in seen_urls:
                continue
            seen_urls.add(cand.url)
            try:
                payload = acquire(cand.url, source_type="pdf", user_agent=user_agent)
            except Exception:
                continue
            if not _pdf_mentions(payload.text, restaurant_name):
                continue  # wrong-restaurant collision -> skip
            sub = extract_menu(
                [payload], policy=policy or Policy.HYBRID, llm_enabled=True,
                gemini_api_key=api_key, gemini_model=model,
            )
            for record in sub.items:
                if record.item_name.lower() not in seen_names:
                    seen_names.add(record.item_name.lower())
                    result.items.append(record)
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
        from safeplate.extraction2.allergy_signals import extract_allergy_signals

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

    # Don't cache a deadline-truncated extraction: it may be missing an allergen
    # source and would wrongly look safer on the next (cache-hit) open.
    if use_result_cache and not timed_out:
        _save_result_cache(cache_url, cache_model, result, cache_disc)
    return candidates, result


_TWO_LEVEL_TLDS = {
    "co.uk", "org.uk", "com.au", "net.au", "co.nz", "co.jp", "com.br", "co.za",
    "com.sg", "co.in", "com.mx", "co.kr", "com.hk", "com.tw",
}


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


def _registrable_domain(host: str) -> str:
    """eTLD+1 (approx) so subdomains of the SAME site count as on-site:
    orders.lazydogrestaurants.com -> lazydogrestaurants.com. Handles common
    two-level TLDs (co.uk etc.)."""
    host = (host or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    labels = [seg for seg in host.split(".") if seg]
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _TWO_LEVEL_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


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
        haystack = f"{url} {text}".lower()
        for kind, tokens in _KIND_TOKENS.items():
            if any(token in haystack for token in tokens):
                out.append(((url, text), kind))
                break
    return out


def _finalize(candidates: list[Candidate], max_candidates: int) -> list[Candidate]:
    best: dict[str, Candidate] = {}
    for cand in candidates:
        existing = best.get(cand.url)
        if existing is None or _KIND_PRIORITY[cand.kind] < _KIND_PRIORITY[existing.kind]:
            best[cand.url] = cand
    # Tier 0: prefer a STATIC allergen document (PDF) over a JS allergen tool of
    # the same kind -- a PDF is parseable; a JS tool often renders to nothing.
    ordered = sorted(
        best.values(),
        key=lambda c: (_KIND_PRIORITY[c.kind], 0 if _is_pdf_url(c.url) else 1),
    )
    return ordered[:max_candidates]
