from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from safeplate.concurrency import TokenBucket as _TokenBucket
from safeplate.menu_sources import (
    _dedupe_records,
    _evidence_grade,
    _score_candidate,
    _source_type,
    _validate_record,
)
from safeplate.page_fetch import PageFetchError, fetch_html_page
from safeplate.soup import make_soup
from safeplate.schemas import MenuSourceRecord, RestaurantRecord
from safeplate.textutil import registrable_domain as _registrable_domain


BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


# One shared bucket + concurrency semaphore for the whole process, built lazily from
# config on first use (so .env is loaded). The semaphore bounds in-flight
# sockets/memory; the bucket bounds the actual request RATE -- both honored together.
_BRAVE_GOVERNOR_LOCK = threading.Lock()
_BRAVE_BUCKET: _TokenBucket | None = None
_BRAVE_SEMAPHORE: threading.Semaphore | None = None


def _brave_governor() -> tuple[_TokenBucket, threading.Semaphore]:
    global _BRAVE_BUCKET, _BRAVE_SEMAPHORE
    if _BRAVE_BUCKET is None or _BRAVE_SEMAPHORE is None:
        with _BRAVE_GOVERNOR_LOCK:
            if _BRAVE_BUCKET is None or _BRAVE_SEMAPHORE is None:
                from safeplate.config import get_brave_concurrency, get_brave_rps

                _BRAVE_BUCKET = _TokenBucket(get_brave_rps())
                _BRAVE_SEMAPHORE = threading.Semaphore(get_brave_concurrency())
    return _BRAVE_BUCKET, _BRAVE_SEMAPHORE

KNOWN_NON_OFFICIAL_HOSTS = [
    "allmenus.com",
    "bbb.org",
    "buzzfile.com",
    "chamberofcommerce.com",
    "city-data.com",
    "clustrmaps.com",
    "doordash.com",
    "facebook.com",
    "find-open.com",
    "findmeglutenfree.com",
    "foursquare.com",
    "google.com",
    "grubhub.com",
    "instagram.com",
    "loc8nearme.com",
    "mapquest.com",
    "menupix.com",
    "menupages.com",
    "manta.com",
    "nicelocal.com",
    "opentable.com",
    "places.singleplatform.com",
    "restaurantguru.com",
    "restaurantji.com",
    "seamless.com",
    "sirved.com",
    "tripadvisor.",
    "ubereats.com",
    "usarestaurants.info",
    "wheree.com",
    "whereorg.com",
    "yellowpages.com",
    "yelp.",
    "zmenu.com",
]

DIRECTORY_HOST_HINTS = [
    "business",
    "directory",
    "listing",
    "places",
    "reviews",
    "restaurants",
]

MENU_ALLOWED_PLATFORM_HINTS = [
    "beyondmenu.com",
    "toasttab.com",
    "chownow.com",
    "clover.com",
    "square.site",
]

NAME_STOPWORDS = {
    "and",
    "bar",
    "cafe",
    "coffee",
    "food",
    "grill",
    "kitchen",
    "llc",
    "restaurant",
    "the",
}

GENERIC_CATEGORY_TOKENS = {
    "amenity",
    "bar",
    "cafe",
    "catering",
    "food",
    "meal",
    "primary",
    "restaurant",
    "restaurants",
    "type",
}

STREET_SUFFIXES = {
    "ave",
    "avenue",
    "blvd",
    "boulevard",
    "cir",
    "circle",
    "ct",
    "court",
    "dr",
    "drive",
    "hwy",
    "highway",
    "ln",
    "lane",
    "pkwy",
    "parkway",
    "pl",
    "place",
    "rd",
    "road",
    "st",
    "street",
    "ter",
    "terrace",
    "way",
}


class BraveSearchError(RuntimeError):
    """Raised when Brave Search cannot return usable results."""


@dataclass(frozen=True)
class BraveSearchResult:
    title: str
    url: str
    description: str
    extra_snippets: list[str]
    raw_payload: dict[str, Any]


def brave_web_search(
    *,
    query: str,
    api_key: str,
    user_agent: str,
    count: int = 5,
    extra_snippets: bool = True,
) -> list[BraveSearchResult]:
    params = {
        "q": query,
        "count": str(max(1, min(count, 20))),
        "safesearch": "moderate",
        "search_lang": "en",
    }
    if extra_snippets:
        params["extra_snippets"] = "true"

    request = Request(
        f"{BRAVE_WEB_SEARCH_URL}?{urlencode(params)}",
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
            "X-Subscription-Token": api_key,
        },
    )

    # Rate + concurrency governance: the semaphore caps in-flight requests, the token
    # bucket caps the request rate (<= configured RPS) so callers can fire Brave
    # queries concurrently without tripping the plan's per-second 429 limit.
    bucket, semaphore = _brave_governor()
    with semaphore:
        bucket.acquire()
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            with exc:  # HTTPError is an open response; close it after reading the body
                details = exc.read().decode("utf-8", errors="replace")
            raise BraveSearchError(
                f"Brave Search request failed with HTTP {exc.code}: {details}"
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise BraveSearchError(f"Brave Search request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise BraveSearchError("Brave Search returned non-JSON data") from exc

    return _search_results_from_payload(payload)


def recover_restaurant_website_with_brave(
    row: RestaurantRecord,
    *,
    api_key: str,
    user_agent: str,
    results_per_query: int = 5,
) -> dict[str, Any]:
    queries = _website_recovery_queries(row)
    if not queries:
        return {
            "status": "skipped",
            "reason": "restaurant row does not have enough name/location context",
            "website_url": "",
            "confidence": 0.0,
            "queries": [],
            "candidates": [],
        }

    candidates: list[dict[str, Any]] = []
    for query in queries:
        results = brave_web_search(
            query=query,
            api_key=api_key,
            user_agent=user_agent,
            count=results_per_query,
            extra_snippets=True,
        )
        for result in results:
            candidates.append(
                _evaluate_website_candidate(
                    row=row,
                    result=result,
                    query=query,
                    user_agent=user_agent,
                )
            )
        accepted = [candidate for candidate in candidates if candidate["accepted"]]
        if accepted:
            break

    accepted_candidates = [candidate for candidate in candidates if candidate["accepted"]]
    if accepted_candidates:
        best = max(accepted_candidates, key=lambda candidate: candidate["confidence"])
        return {
            "status": "recovered",
            "reason": best["reason"],
            "website_url": best["url"],
            "confidence": best["confidence"],
            "queries": queries,
            "candidates": candidates,
        }

    return {
        "status": "not_found",
        "reason": "no Brave result matched restaurant name plus address or phone",
        "website_url": "",
        "confidence": 0.0,
        "queries": queries,
        "candidates": candidates,
    }


def discover_menu_sources_with_brave(
    *,
    restaurant_name: str,
    restaurant_source_id: str,
    website_url: str,
    address: str,
    api_key: str,
    user_agent: str,
    limit: int = 8,
    fetch_mode: str = "static",
) -> list[MenuSourceRecord]:
    queries = _menu_source_queries(
        restaurant_name=restaurant_name,
        website_url=website_url,
        address=address,
    )
    if not queries:
        return []

    records: list[MenuSourceRecord] = []
    fetched_at = datetime.now(timezone.utc).isoformat()
    seen_urls: set[str] = set()
    for query in queries:
        results = brave_web_search(
            query=query,
            api_key=api_key,
            user_agent=user_agent,
            count=5,
            extra_snippets=True,
        )
        for result in results:
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            record = _menu_source_record_from_result(
                result=result,
                query=query,
                website_url=website_url,
                restaurant_name=restaurant_name,
                restaurant_source_id=restaurant_source_id,
                address=address,
                fetched_at=fetched_at,
            )
            if record is not None:
                records.append(record)

    records = _dedupe_records(records)
    validated = [
        _validate_record(record, user_agent=user_agent, fetch_mode=fetch_mode)
        for record in records
    ]
    validated = [
        record
        for record in validated
        if record.validation_status == "validated"
        or (record.source_type == "image" and record.is_primary_menu_candidate)
    ]
    validated.sort(key=lambda record: record.confidence, reverse=True)
    return validated[:limit]


def discover_allergen_pdfs_with_brave(
    *,
    restaurant_name: str,
    restaurant_source_id: str,
    website_url: str,
    address: str,
    api_key: str,
    user_agent: str,
    limit: int = 5,
) -> list[MenuSourceRecord]:
    """Search the web specifically for a restaurant's allergen PDF/page.

    Finds allergen documents that live anywhere — CDNs, upload folders,
    non-standard paths — that path-probing and crawling miss. Returns
    UN-validated candidate records (the caller validates them with everything
    else). Only allergen-relevant results are kept.
    """
    if not restaurant_name:
        return []

    domain = _host_without_www(website_url)
    queries: list[str] = []
    if domain:
        queries.append(f"site:{domain} filetype:pdf allergen OR nutrition")
        queries.append(f"site:{domain} allergen menu")
    location = _city_region(address) or _street_line(address)
    if location:
        queries.append(
            f"{_quoted(restaurant_name)} {_quoted(location)} allergen menu filetype:pdf"
        )
        queries.append(f"{_quoted(restaurant_name)} {_quoted(location)} allergens")
    else:
        queries.append(f"{_quoted(restaurant_name)} allergen menu filetype:pdf")
    queries = list(dict.fromkeys(query for query in queries if query.strip()))

    records: list[MenuSourceRecord] = []
    fetched_at = datetime.now(timezone.utc).isoformat()
    seen_urls: set[str] = set()
    for query in queries:
        try:
            results = brave_web_search(
                query=query, api_key=api_key, user_agent=user_agent, count=5
            )
        except BraveSearchError:
            continue
        for result in results:
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            record = _menu_source_record_from_result(
                result=result,
                query=query,
                website_url=website_url,
                restaurant_name=restaurant_name,
                restaurant_source_id=restaurant_source_id,
                address=address,
                fetched_at=fetched_at,
            )
            if record is None:
                continue
            haystack = f"{record.candidate_url} {record.link_text}".lower()
            if record.source_type == "nutrition_or_allergen_page" or (
                "allergen" in haystack or "allergy" in haystack
            ):
                records.append(record)
        if records:
            break

    return _dedupe_records(records)[:limit]


def _search_results_from_payload(payload: dict[str, Any]) -> list[BraveSearchResult]:
    rows = payload.get("web", {}).get("results", [])
    results = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not _is_http_url(url):
            continue
        extra_snippets = row.get("extra_snippets") or []
        if not isinstance(extra_snippets, list):
            extra_snippets = []
        results.append(
            BraveSearchResult(
                title=str(row.get("title") or "").strip(),
                url=url,
                description=str(row.get("description") or "").strip(),
                extra_snippets=[
                    str(snippet).strip()
                    for snippet in extra_snippets
                    if str(snippet).strip()
                ],
                raw_payload=row,
            )
        )
    return results


def _website_recovery_queries(row: RestaurantRecord) -> list[str]:
    if not row.name:
        return []

    queries = []
    primary_parts = [_quoted(row.name)]
    street = _street_line(row.address)
    if street:
        primary_parts.append(_quoted(street))
    if row.phone_number:
        primary_parts.append(_quoted(row.phone_number))
    primary_parts.append("official website")
    queries.append(" ".join(part for part in primary_parts if part))

    city = _city_region(row.address)
    if city:
        queries.append(f"{_quoted(row.name)} {_quoted(city)} restaurant website")

    core_name = " ".join(_name_tokens(row.name)[:2])
    city_name = _city_name(row.address)
    if core_name and city_name:
        for category_term in _category_query_terms(row.categories):
            queries.append(f"{core_name} {category_term} restaurant {city_name} website")
        queries.append(f"{core_name} restaurant {city_name} website")

    return list(dict.fromkeys(query for query in queries if query.strip()))


def _menu_source_queries(
    *,
    restaurant_name: str,
    website_url: str,
    address: str,
) -> list[str]:
    if not restaurant_name:
        return []

    queries = []
    domain = _host_without_www(website_url)
    if domain:
        queries.append(f"site:{domain} menu")
        queries.append(f"site:{domain} filetype:pdf menu")
        # Allergen/nutrition matrices map dish -> allergen directly and are the
        # most valuable source we can find, especially for chains whose ordering
        # menus hide prices behind a location picker.
        queries.append(f"site:{domain} allergen OR nutrition")
        queries.append(f"site:{domain} filetype:pdf allergen OR nutrition")

    location = _city_region(address) or _street_line(address)
    if location:
        queries.append(f"{_quoted(restaurant_name)} {_quoted(location)} menu")
        queries.append(f"{_quoted(restaurant_name)} {_quoted(location)} menu pdf")
        queries.append(f"{_quoted(restaurant_name)} {_quoted(location)} allergen menu")
    else:
        queries.append(f"{_quoted(restaurant_name)} menu")
        queries.append(f"{_quoted(restaurant_name)} allergen menu")

    return list(dict.fromkeys(query for query in queries if query.strip()))


def _evaluate_website_candidate(
    *,
    row: RestaurantRecord,
    result: BraveSearchResult,
    query: str,
    user_agent: str,
) -> dict[str, Any]:
    url = result.url
    host = _host_without_www(url)
    title_text = _result_text(result)
    fetched_text, fetch_status = _verification_text(url, user_agent=user_agent)
    verification_text = _normalize_text(f"{title_text} {fetched_text} {host}")

    reasons = []
    rejected_reason = ""
    confidence = 0.0

    if _is_known_non_official_host(host):
        rejected_reason = f"known third-party/listing host: {host}"
    elif not _is_http_url(url):
        rejected_reason = "not an HTTP URL"
    else:
        name_score = _name_match_score(row.name, verification_text, url)
        address_match = _address_matches(row.address, verification_text)
        phone_match = _phone_matches(row.phone_number, verification_text)
        domain_match = _domain_matches_name(row.name, url)
        city_match = _city_matches(row.address, verification_text)

        if name_score >= 0.8:
            confidence += 0.4
            reasons.append("strong restaurant-name match")
        elif name_score >= 0.55:
            confidence += 0.28
            reasons.append("partial restaurant-name match")

        if address_match:
            confidence += 0.38
            reasons.append("address match")
        if phone_match:
            confidence += 0.42
            reasons.append("phone match")
        if domain_match:
            confidence += 0.15
            reasons.append("domain resembles restaurant name")
        if city_match:
            confidence += 0.08
            reasons.append("city/location match")
        if "official" in verification_text:
            confidence += 0.04
            reasons.append("official-site wording")

        if name_score < 0.55:
            rejected_reason = "restaurant name did not match enough"
        elif not domain_match:
            rejected_reason = "domain did not resemble restaurant name"
        elif not (address_match or phone_match or city_match):
            rejected_reason = "no address/phone evidence for this location"

    confidence = round(min(confidence, 1.0), 3)
    accepted = not rejected_reason and confidence >= 0.62
    if not reasons and rejected_reason:
        reasons.append(rejected_reason)
    if fetch_status:
        reasons.append(fetch_status)

    return {
        "url": url,
        "title": result.title,
        "description": result.description,
        "query": query,
        "host": host,
        "accepted": accepted,
        "confidence": confidence,
        "reason": "; ".join(reasons) or "candidate evaluated",
        "rejection_reason": "" if accepted else rejected_reason,
    }


def _menu_source_record_from_result(
    *,
    result: BraveSearchResult,
    query: str,
    website_url: str,
    restaurant_name: str,
    restaurant_source_id: str,
    address: str,
    fetched_at: str,
) -> MenuSourceRecord | None:
    url = result.url
    if not _is_http_url(url):
        return None

    candidate_text = _result_text(result)
    host = _host_without_www(url)
    if _is_known_non_official_host(host) and not _is_allowed_menu_platform(host):
        return None

    score, reasons = _score_candidate(
        url,
        candidate_text,
        location_hint=address or restaurant_name,
    )
    if _domain_matches_name(restaurant_name, url):
        score += 0.1
        reasons.append("domain resembles restaurant name")
    if _name_match_score(restaurant_name, _normalize_text(candidate_text), url) >= 0.55:
        score += 0.1
        reasons.append("search result names restaurant")

    source_type = _source_type(url, candidate_text)
    if source_type == "ordering_page" and not _is_allowed_menu_platform(host):
        return None
    if score < 0.25 and source_type not in ["pdf", "image"]:
        return None

    confidence = round(min(score + 0.12, 1.0), 3)
    return MenuSourceRecord(
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
        website_url=website_url,
        candidate_url=url,
        source_type=source_type,
        link_text=result.title or "Brave Search result",
        confidence=confidence,
        evidence_grade=_evidence_grade(confidence, "unvalidated", source_type),
        reason="Brave Search candidate; " + "; ".join(reasons),
        is_primary_menu_candidate=source_type in ["pdf", "image"]
        or "menu" in _normalize_text(f"{url} {candidate_text}"),
        validation_status="unvalidated",
        validation_reason="candidate found by Brave Search",
        fetched_at=fetched_at,
        raw_payload={
            "discovered_by": "brave_search",
            "query": query,
            "result": {
                "title": result.title,
                "url": result.url,
                "description": result.description,
                "extra_snippets": result.extra_snippets,
            },
        },
    )


def _verification_text(url: str, *, user_agent: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.path.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return "", "binary candidate not fetched for website verification"

    try:
        page = fetch_html_page(url, user_agent=user_agent, fetch_mode="static")
    except PageFetchError as exc:
        return "", f"verification fetch skipped/failed: {exc}"

    soup = make_soup(page.html)
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)[:12000], "verification page fetched"


def _result_text(result: BraveSearchResult) -> str:
    snippets = " ".join(result.extra_snippets)
    return f"{result.title} {result.description} {snippets} {result.url}"


def _quoted(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"\s+", " ", value.replace('"', " ")).strip()
    return f'"{cleaned}"' if cleaned else ""


def _street_line(address: str | None) -> str:
    if not address:
        return ""
    return address.split(",", 1)[0].strip()


def _city_region(address: str | None) -> str:
    if not address:
        return ""
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) >= 3:
        return ", ".join(parts[1:3])
    if len(parts) >= 2:
        return parts[1]
    return ""


def _city_name(address: str | None) -> str:
    if not address:
        return ""
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[1]
    return ""


def _category_query_terms(categories: list[str]) -> list[str]:
    terms: list[str] = []
    for category in categories or []:
        cleaned = category.split(":", 1)[-1]
        tokens = [
            token
            for token in _tokens(cleaned.replace("_", " ").replace("-", " "))
            if len(token) >= 4 and token not in GENERIC_CATEGORY_TOKENS
        ]
        if tokens:
            term = " ".join(tokens[:2])
            if term not in terms:
                terms.append(term)
    return terms[:2]


def _address_matches(address: str | None, text: str) -> bool:
    if not address:
        return False
    street = _street_line(address)
    number_match = re.search(r"\b\d{2,6}\b", street)
    if not number_match:
        return False
    number = number_match.group(0)
    street_tokens = [
        token
        for token in _tokens(street)
        if token != number and token not in STREET_SUFFIXES and len(token) >= 4
    ]
    return number in text and any(token in text for token in street_tokens)


def _city_matches(address: str | None, text: str) -> bool:
    city_region = _city_region(address)
    if not city_region:
        return False
    city_tokens = [
        token
        for token in _tokens(city_region)
        if len(token) >= 4 and token not in {"usa", "united", "states"}
    ]
    return bool(city_tokens) and any(token in text for token in city_tokens)


def _phone_matches(phone_number: str | None, text: str) -> bool:
    if not phone_number:
        return False
    phone_digits = _digits(phone_number)
    text_digits = _digits(text)
    return len(phone_digits) >= 7 and phone_digits[-7:] in text_digits


def _name_match_score(name: str | None, text: str, url: str) -> float:
    tokens = _name_tokens(name)
    if not tokens:
        return 0.0
    host = _host_without_www(url).replace("-", "").replace(".", "")
    compact_name = "".join(tokens)
    if compact_name and compact_name in host:
        return 1.0
    matched = sum(1 for token in tokens if token in text or token in host)
    return matched / len(tokens)


def _domain_matches_name(name: str | None, url: str) -> bool:
    tokens = _name_tokens(name)
    if not tokens:
        return False
    host = _registrable_domain(_host_without_www(url)).replace("-", "").replace(".", "")
    return any(len(token) >= 5 and token in host for token in tokens) or "".join(tokens) in host


def _name_tokens(name: str | None) -> list[str]:
    return [
        token
        for token in _tokens(name or "")
        if len(token) >= 3 and token not in NAME_STOPWORDS
    ] or [token for token in _tokens(name or "") if len(token) >= 3]


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^a-z0-9]+", value.lower())
        if token
    ]


def _normalize_text(value: str) -> str:
    return " ".join(_tokens(value))


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value)


def _host_without_www(url_or_host: str) -> str:
    parsed = urlparse(url_or_host)
    host = parsed.netloc or url_or_host
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host




def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ["http", "https"] and bool(parsed.netloc)


def _is_known_non_official_host(host: str) -> bool:
    if any(hint in host for hint in KNOWN_NON_OFFICIAL_HOSTS):
        return True
    compact_host = host.replace("-", "").replace(".", "")
    return any(hint in compact_host for hint in DIRECTORY_HOST_HINTS)


def _is_allowed_menu_platform(host: str) -> bool:
    return any(hint in host for hint in MENU_ALLOWED_PLATFORM_HINTS)
