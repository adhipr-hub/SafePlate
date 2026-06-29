"""Tier 2a: capture the data API behind a JS allergen tool (free, no browser).

A JS allergen tool hydrates from a backend response. Often the endpoint URL is
discoverable statically -- referenced in an inline script or a same-origin JS
bundle. This harvests those URL strings, GETs the data-ish ones, and runs the
structured allergen detector over any JSON returned. No keyword/path list to
maintain: candidate endpoints come from the page's own code, and a hit is
whatever actually yields dish x allergen data (validation-by-extraction).

Limitation (-> Tier 2b): GraphQL/POST endpoints and build-time-only data have no
GETtable URL here; those need browser network capture.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from urllib.parse import urljoin, urlparse

from safeplate.concurrency import map_concurrent
from safeplate.extraction2.embedded_allergens import extract_allergen_items_from_obj
from safeplate.extraction2.region import detect_source_region
from safeplate.extraction2.schema import CoverageReport
from safeplate.fetching import fetch_url_bytes
from safeplate.menu_text import MenuItemRecord
from safeplate.page_fetch import PageFetchError, fetch_html_page
from safeplate.soup import make_soup


class _ApiCaptureError(RuntimeError):
    pass


# URL-or-absolute-path literals inside code/markup.
_URL_IN_CODE = re.compile(r"""["'`](https?://[^"'`\s]+|/[A-Za-z0-9_][^"'`\s]{2,})["'`]""")
# Data-ish signals that a URL is an API/JSON endpoint rather than an asset/page.
_API_HINTS = ("/api/", ".json", "/graphql", "allergen", "nutrition", "menu",
              "/items", "/products", "/content", "/catalog")
_CMS_HOSTS = ("contentful", "prismic", "sanity", "datocms", "graphcms",
              "cloudfront", "amazonaws", "googleapis", "cdn.")
_SKIP_EXT = (".js", ".mjs", ".css", ".png", ".jpg", ".jpeg", ".svg", ".webp",
             ".gif", ".woff", ".woff2", ".ttf", ".ico", ".map", ".mp4", ".pdf")


def capture_allergen_api(
    page_url: str,
    *,
    user_agent: str,
    max_bundles: int = 6,
    max_endpoints: int = 14,
    max_workers: int = 4,
) -> tuple[list[MenuItemRecord], list[CoverageReport]]:
    """Return (records, coverage) for dish x allergen data recovered from the page's
    backing API, or ([], []). Each yielding endpoint gets a CoverageReport carrying a
    content-locale ``region`` stamp -- so backend-captured allergen data participates
    in the from-another-region banner like any source read through extract_menu
    (without it, a wrong-country backend feed would show with no notice)."""
    try:
        html = fetch_html_page(page_url, user_agent=user_agent).html
    except PageFetchError:
        return [], []

    endpoints = _candidate_endpoints(html, page_url, user_agent, max_bundles)[:max_endpoints]
    if not endpoints:
        return [], []

    results = map_concurrent(
        lambda url: (url, *_allergens_from_endpoint(url, user_agent)),
        endpoints,
        max_workers=max_workers,
    )
    merged: dict[str, MenuItemRecord] = {}
    coverage: list[CoverageReport] = []
    for url, records, text in results:
        if not records:
            continue
        coverage.append(_capture_coverage(url, records, text))
        for record in records:
            merged.setdefault(record.item_name.lower(), record)
    return list(merged.values()), coverage


def _capture_coverage(url: str, records: list[MenuItemRecord], text: str) -> CoverageReport:
    """Honest per-endpoint coverage for an api_capture source, with the region read
    from the captured response text/URL (the endpoint host is often a country-neutral
    CDN, so the content tell -- a footer ccTLD domain -- is what reveals the locale)."""
    n = len(records)
    conf = round(sum(r.confidence for r in records) / n, 3) if n else 0.0
    return CoverageReport(
        url=url,
        found=bool(records),
        payload_kind="structured",
        item_count=n,
        interpreter="api_capture",
        confidence=conf,
        reason=f"{n} items via api_capture",
        region=detect_source_region(text, url) or "",
    )


def _candidate_endpoints(html: str, page_url: str, user_agent: str, max_bundles: int) -> list[str]:
    soup = make_soup(html)
    texts: list[str] = []
    bundle_srcs: list[str] = []
    for script in soup.find_all("script"):
        src = (script.get("src") or "").strip()
        if src:
            bundle_srcs.append(urljoin(page_url, src))
        else:
            inline = script.string or script.get_text() or ""
            if inline:
                texts.append(inline)

    base_host = urlparse(page_url).netloc
    same_origin_js = [
        url for url in bundle_srcs
        if urlparse(url).netloc == base_host and url.lower().split("?")[0].endswith(".js")
    ][:max_bundles]
    for body in map_concurrent(lambda u: _get_text(u, user_agent), same_origin_js, max_workers=4):
        if body:
            texts.append(body)

    found: set[str] = set()
    for text in texts:
        for match in _URL_IN_CODE.finditer(text):
            found.add(match.group(1))

    endpoints: list[str] = []
    for raw in found:
        full = urljoin(page_url, raw)
        if not full.startswith(("http://", "https://")):
            continue
        low = full.lower().split("?")[0]
        if low.endswith(_SKIP_EXT):
            continue
        if any(hint in full.lower() for hint in _API_HINTS) or any(h in full.lower() for h in _CMS_HOSTS):
            endpoints.append(full)

    endpoints = list(dict.fromkeys(endpoints))
    # Try the most allergen-relevant endpoints first.
    endpoints.sort(key=lambda u: 0 if ("allergen" in u.lower() or "nutrition" in u.lower()) else 1)
    return endpoints


def _get_text(url: str, user_agent: str) -> str:
    try:
        raw, _content_type = fetch_url_bytes(url, user_agent=user_agent, error_cls=_ApiCaptureError)
    except Exception:
        return ""
    return raw.decode("utf-8", errors="replace")


def _allergens_from_endpoint(url: str, user_agent: str) -> tuple[list[MenuItemRecord], str]:
    """Return (records, captured_text). The text is handed back so the caller can run
    content-locale detection over the actual response, not just the endpoint URL."""
    text = _get_text(url, user_agent).strip()
    if not text:
        return [], ""
    if text[0] in "{[":  # JSON API response
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return [], text
        records = extract_allergen_items_from_obj(payload)
    else:  # HTML matrix tool / page: reuse the structured allergen extractors
        from safeplate.allergen_matrix import extract_items_from_allergen_matrix
        from safeplate.extraction2.embedded_allergens import (
            extract_allergen_items_from_embedded_json,
        )

        records = (
            extract_allergen_items_from_embedded_json(text)
            or extract_items_from_allergen_matrix(text)
        )
    stamped = [
        replace(r, extraction_method="api_capture", menu_source_url=url) for r in records
    ]
    return stamped, text
