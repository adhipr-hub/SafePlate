from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from safeplate.concurrency import map_concurrent
from safeplate.config import get_fetch_concurrency
from safeplate.fetching import fetch_url_bytes
from safeplate.io import read_csv_rows as _read_csv_rows
from safeplate.io import timestamped_output_paths
from safeplate.io import write_dataclass_csv
from safeplate.io import write_dataclass_json
from safeplate.page_fetch import PageFetchError, fetch_html_page
from safeplate.schema_org import json_ld_items_from_html as _extract_json_ld_items
from safeplate.schema_org import json_ld_items_from_soup as _json_ld_items_from_soup
from safeplate.soup import make_soup
from safeplate.soup import remove_non_content_tags as _remove_non_content_tags
from safeplate.allergen_matrix import looks_like_allergen_matrix
from safeplate.textutil import PRICE_PATTERN
from safeplate.textutil import classlist_text as _class_text
from safeplate.schemas import MenuSourceRecord


MENU_SOURCE_CSV_FIELDS = [
    "restaurant_name",
    "restaurant_source_id",
    "website_url",
    "candidate_url",
    "source_type",
    "link_text",
    "confidence",
    "evidence_grade",
    "reason",
    "is_primary_menu_candidate",
    "validation_status",
    "validation_reason",
    "fetched_at",
    "raw_payload",
]

# Used deliberately in TWO places: link/URL scoring during discovery, and page
# scoring during validation (`_score_menu_page_text`). Allergen/diet words are
# included on purpose — so a dedicated allergen page (which has few prices/dishes)
# still validates and gets extracted, not just priced menus.
STRICT_MENU_KEYWORDS = [
    "menu",
    "menus",
    "lunch",
    "dinner",
    "brunch",
    "breakfast",
    "dining",
    "nutrition",
    "allergen",
    "allergens",
    "allergy",
    "dietary",
    "gluten",
    "vegan",
    "vegetarian",
    # Multilingual "menu" / food-list synonyms so non-English sites are not a
    # discovery blind spot (a Tokyo site links "メニュー", a Paris site "carte").
    "carta", "menú", "carte", "speisekarte", "karte", "menü", "cardápio",
    "cardapio", "ementa", "menukaart", "メニュー", "お品書き", "品書き",
    "菜单", "菜單", "餐牌", "菜谱", "메뉴", "เมนู", "मेन्यू", "मेनू",
    "قائمة", "منيو", "меню", "thực đơn", "thuc don", "jadalnia", "jelovnik",
]

SECONDARY_MENU_KEYWORDS = [
    "order",
    "ordering",
    "takeout",
    "take-out",
    "delivery",
    "catering",
    # Broader food-list signals (weaker than a direct menu synonym).
    "food", "drinks", "beverages", "wine", "cocktails", "desserts",
    "prix fixe", "a la carte", "tasting", "carte des vins",
]

MENU_ITEM_HINTS = [
    "appetizer",
    "appetizers",
    "entree",
    "entrees",
    "salad",
    "salads",
    "sandwich",
    "sandwiches",
    "burger",
    "pizza",
    "pasta",
    "dessert",
    "desserts",
    "beverage",
    "beverages",
    "soup",
    "noodles",
    "rice",
    "chicken",
    "beef",
    "pork",
    "tofu",
]

ORDERING_HOST_HINTS = [
    "beyondmenu",
    "toasttab",
    "square.site",
    "chownow",
    "doordash",
    "ubereats",
    "grubhub",
    "olo.com",
    "order.online",
    "bento-order",
]

IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".gif"]


def _keyword_regex(keywords: list[str]) -> re.Pattern[str]:
    # Single alternation so a page/URL is scanned once instead of once per keyword.
    # NOTE: only safe for "is ANY keyword present" checks. It is NOT used for
    # counting, because alternation consumes non-overlapping matches and would
    # under-count keywords that are substrings of others (e.g. menu vs menus).
    return re.compile("|".join(re.escape(keyword) for keyword in keywords))


_STRICT_KEYWORD_RE = _keyword_regex(STRICT_MENU_KEYWORDS)


class MenuSourceError(RuntimeError):
    """Raised when a website cannot be fetched or parsed."""


def discover_menu_sources_for_url(
    *,
    website_url: str,
    restaurant_name: str | None = None,
    restaurant_source_id: str | None = None,
    user_agent: str,
    limit: int = 25,
    validate: bool = True,
    include_ordering_pages: bool = False,
    include_images: bool = False,
    crawl_depth: int = 1,
    use_sitemap: bool = True,
    location_hint: str | None = None,
    fetch_mode: str = "static",
    max_workers: int | None = None,
    seek_allergen_pages: bool = True,
    brave_api_key: str | None = None,
) -> list[MenuSourceRecord]:
    workers = max_workers if max_workers is not None else get_fetch_concurrency()
    if fetch_mode != "static":
        workers = 1  # Playwright sync API must run on a single thread
    normalized_url = _ensure_url_scheme(website_url)
    html = _fetch_text(normalized_url, user_agent=user_agent, fetch_mode=fetch_mode)

    fetched_at = datetime.now(timezone.utc).isoformat()
    candidates = []
    pages = [(normalized_url, html)]
    if use_sitemap:
        pages.extend(
            _fetch_sitemap_pages(
                normalized_url,
                user_agent=user_agent,
                fetch_mode=fetch_mode,
                max_workers=workers,
            )
        )
    if crawl_depth > 1:
        pages.extend(
            _crawl_likely_pages(
                base_url=normalized_url,
                html=html,
                user_agent=user_agent,
                fetch_mode=fetch_mode,
                max_workers=workers,
            )
        )

    seen_page_urls: set[str] = set()
    for page_url, page_html in pages:
        if page_url in seen_page_urls:
            continue
        seen_page_urls.add(page_url)
        candidates.extend(
            _candidates_for_page(
                soup=make_soup(page_html),
                page_url=page_url,
                website_url=normalized_url,
                restaurant_name=restaurant_name,
                restaurant_source_id=restaurant_source_id,
                fetched_at=fetched_at,
                location_hint=location_hint,
            )
        )

    # Active allergen-menu seeker: if normal discovery did not already surface an
    # allergen/nutrition page, probe common allergen URLs directly. A dish x
    # allergen matrix is the most valuable source for SafePlate, so it is worth
    # an extra look rather than only catching the ones that happen to be linked.
    if seek_allergen_pages and not _has_allergen_candidate(candidates):
        for url, page_html in _seek_allergen_pages(
            normalized_url, user_agent=user_agent, fetch_mode=fetch_mode, max_workers=workers
        ):
            if url in seen_page_urls:
                continue
            seen_page_urls.add(url)
            candidates.extend(
                _candidates_for_page(
                    soup=make_soup(page_html),
                    page_url=url,
                    website_url=normalized_url,
                    restaurant_name=restaurant_name,
                    restaurant_source_id=restaurant_source_id,
                    fetched_at=fetched_at,
                    location_hint=location_hint,
                )
            )
            direct = _record_from_link(
                href=url,
                text="allergen menu",
                base_url=url,
                website_url=normalized_url,
                restaurant_name=restaurant_name,
                restaurant_source_id=restaurant_source_id,
                fetched_at=fetched_at,
                location_hint=location_hint,
            )
            if direct is not None:
                candidates.append(direct)

        # Allergen matrices are very often published as a PDF, so probe those too.
        for pdf_url in _seek_allergen_pdfs(
            normalized_url, user_agent=user_agent, max_workers=workers
        ):
            if pdf_url in seen_page_urls:
                continue
            seen_page_urls.add(pdf_url)
            pdf_record = _record_from_link(
                href=pdf_url,
                text="allergen menu",
                base_url=pdf_url,
                website_url=normalized_url,
                restaurant_name=restaurant_name,
                restaurant_source_id=restaurant_source_id,
                fetched_at=fetched_at,
                location_hint=location_hint,
            )
            if pdf_record is not None:
                candidates.append(pdf_record)

        # Still no allergen source on the site itself: search the web for one
        # (CDN / upload-folder / non-standard-path allergen PDFs) via Brave.
        if brave_api_key and not _has_allergen_candidate(candidates):
            candidates.extend(
                _seek_allergen_with_brave(
                    website_url=normalized_url,
                    restaurant_name=restaurant_name,
                    restaurant_source_id=restaurant_source_id,
                    address=location_hint,
                    api_key=brave_api_key,
                    user_agent=user_agent,
                )
            )

    records = [candidate for candidate in candidates if candidate is not None]
    records = _dedupe_records(records)
    if validate:
        records = map_concurrent(
            lambda record: _validate_record(
                record, user_agent=user_agent, fetch_mode=fetch_mode
            ),
            records,
            max_workers=workers,
        )
        records = _filter_validated_records(
            records,
            include_ordering_pages=include_ordering_pages,
            include_images=include_images,
        )
    records.sort(key=lambda record: record.confidence, reverse=True)
    return records[:limit]


# Common URL paths where restaurants publish a dedicated allergen/nutrition page.
_ALLERGEN_PROBE_PATHS = [
    "/allergens", "/allergen", "/allergy", "/allergen-menu", "/allergens-menu",
    "/allergen-information", "/allergen-info", "/allergy-information",
    "/food-allergens", "/allergen-guide", "/allergen-chart", "/allergen-matrix",
    "/nutrition", "/nutritional-information", "/dietary", "/dietary-information",
]


def _candidates_for_page(
    *,
    soup: BeautifulSoup,
    page_url: str,
    website_url: str,
    restaurant_name: str | None,
    restaurant_source_id: str | None,
    fetched_at: str,
    location_hint: str | None,
) -> list[MenuSourceRecord | None]:
    """Extract all menu-source candidates from a single parsed page."""
    out: list[MenuSourceRecord | None] = []
    out.extend(
        _records_from_schema_org_items(
            items=_json_ld_items_from_soup(soup),
            page_url=page_url,
            website_url=website_url,
            restaurant_name=restaurant_name,
            restaurant_source_id=restaurant_source_id,
            fetched_at=fetched_at,
            location_hint=location_hint,
        )
    )
    links, images = _links_and_images_from_soup(soup)
    out.extend(
        _record_from_link(
            href=href, text=text, base_url=page_url, website_url=website_url,
            restaurant_name=restaurant_name, restaurant_source_id=restaurant_source_id,
            fetched_at=fetched_at, location_hint=location_hint,
        )
        for href, text in links
    )
    out.extend(
        _record_from_image(
            src=src, alt=alt, base_url=page_url, website_url=website_url,
            restaurant_name=restaurant_name, restaurant_source_id=restaurant_source_id,
            fetched_at=fetched_at, location_hint=location_hint,
        )
        for src, alt in images
    )
    return out


def _has_allergen_candidate(candidates: list[MenuSourceRecord | None]) -> bool:
    for candidate in candidates:
        if candidate is None:
            continue
        if candidate.source_type == "nutrition_or_allergen_page":
            return True
        # An allergen PDF is typed "pdf" but is still an allergen source.
        if candidate.source_type == "pdf" and "allergen" in candidate.candidate_url.lower():
            return True
    return False


def _seek_allergen_pages(
    base_url: str,
    *,
    user_agent: str,
    fetch_mode: str,
    max_workers: int,
) -> list[tuple[str, str]]:
    """Probe common allergen-page paths; return (url, html) for real ones."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    urls = [root + path for path in _ALLERGEN_PROBE_PATHS]

    def probe(url: str) -> tuple[str, str] | None:
        try:
            html = _fetch_text(url, user_agent=user_agent, fetch_mode=fetch_mode)
        except MenuSourceError:
            return None
        # Confirm it is genuinely allergen content, not a soft-404 homepage.
        lowered = html.lower()
        if "allergen" in lowered or "allergy" in lowered:
            return (url, html)
        return None

    results = map_concurrent(probe, urls, max_workers=max_workers)
    return [page for page in results if page is not None]


# Common URL paths where restaurants publish the allergen matrix as a PDF.
_ALLERGEN_PDF_PATHS = [
    "/allergens.pdf", "/allergen.pdf", "/allergen-menu.pdf", "/allergens-menu.pdf",
    "/allergen-guide.pdf", "/allergen-chart.pdf", "/allergen-matrix.pdf",
    "/allergen-information.pdf", "/menu-allergens.pdf", "/allergy.pdf",
    "/nutrition.pdf", "/dietary.pdf", "/allergens-and-nutrition.pdf",
]


def _seek_allergen_pdfs(
    base_url: str,
    *,
    user_agent: str,
    max_workers: int,
) -> list[str]:
    """Probe common allergen-PDF paths; return URLs that return a real PDF."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    urls = [root + path for path in _ALLERGEN_PDF_PATHS]

    def probe(url: str) -> str | None:
        try:
            raw, content_type = _fetch_bytes(url, user_agent=user_agent)
        except MenuSourceError:
            return None
        # Confirm it is actually a PDF (a soft-404 returns HTML, not %PDF).
        if raw[:5] == b"%PDF-" or "application/pdf" in content_type.lower():
            return url
        return None

    results = map_concurrent(probe, urls, max_workers=max_workers)
    return [url for url in results if url is not None]


def _seek_allergen_with_brave(
    *,
    website_url: str,
    restaurant_name: str | None,
    restaurant_source_id: str | None,
    address: str | None,
    api_key: str,
    user_agent: str,
) -> list[MenuSourceRecord]:
    # Lazy import: brave_search imports from this module, so a top-level import
    # would be circular. Never let an allergen web search break discovery.
    try:
        from safeplate.brave_search import discover_allergen_pdfs_with_brave

        return discover_allergen_pdfs_with_brave(
            restaurant_name=restaurant_name or "",
            restaurant_source_id=restaurant_source_id or "",
            website_url=website_url,
            address=address or "",
            api_key=api_key,
            user_agent=user_agent,
        )
    except Exception:
        return []


def read_restaurant_csv(path: Path) -> list[dict[str, str]]:
    return _read_csv_rows(path)


def build_menu_output_paths(label: str, out_dir: Path) -> tuple[Path, Path]:
    json_path, csv_path = timestamped_output_paths(
        label,
        out_dir,
        "menu_sources",
        (".json", ".csv"),
    )
    return json_path, csv_path


def write_menu_sources_json(path: Path, rows: list[MenuSourceRecord]) -> None:
    write_dataclass_json(path, rows)


def write_menu_sources_csv(path: Path, rows: list[MenuSourceRecord]) -> None:
    def transform(record: dict[str, Any], row: MenuSourceRecord) -> None:
        record["raw_payload"] = json.dumps(row.raw_payload, sort_keys=True)

    write_dataclass_csv(
        path,
        rows,
        fieldnames=MENU_SOURCE_CSV_FIELDS,
        transform=transform,
    )


def _fetch_text(
    url: str,
    user_agent: str,
    fetch_mode: str = "static",
) -> str:
    try:
        return fetch_html_page(
            url,
            user_agent=user_agent,
            fetch_mode=fetch_mode,
        ).html
    except PageFetchError as exc:
        raise MenuSourceError(str(exc)) from exc


def _fetch_raw_text(url: str, user_agent: str) -> str:
    raw, _content_type = _fetch_bytes(url, user_agent=user_agent)
    return raw.decode("utf-8", errors="replace")


def _fetch_bytes(url: str, user_agent: str) -> tuple[bytes, str]:
    return fetch_url_bytes(url, user_agent=user_agent, error_cls=MenuSourceError)


def _record_from_link(
    *,
    href: str,
    text: str,
    base_url: str,
    website_url: str,
    restaurant_name: str | None,
    restaurant_source_id: str | None,
    fetched_at: str,
    location_hint: str | None,
) -> MenuSourceRecord | None:
    candidate_url = urljoin(base_url, href)
    if not _is_http_url(candidate_url):
        return None

    score, reasons = _score_candidate(
        candidate_url,
        text,
        location_hint=location_hint,
    )
    if score <= 0:
        return None

    return MenuSourceRecord(
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
        website_url=website_url,
        candidate_url=candidate_url,
        source_type=_source_type(candidate_url, text),
        link_text=text or None,
        confidence=min(score, 1.0),
        evidence_grade=_evidence_grade(
            min(score, 1.0),
            "unvalidated",
            _source_type(candidate_url, text),
        ),
        reason="; ".join(reasons),
        is_primary_menu_candidate=_is_primary_menu_candidate(candidate_url, text),
        validation_status="unvalidated",
        validation_reason="candidate not fetched yet",
        fetched_at=fetched_at,
        raw_payload={"href": href, "text": text},
    )


def _record_from_image(
    *,
    src: str,
    alt: str,
    base_url: str,
    website_url: str,
    restaurant_name: str | None,
    restaurant_source_id: str | None,
    fetched_at: str,
    location_hint: str | None,
) -> MenuSourceRecord | None:
    candidate_url = urljoin(base_url, src)
    if not _is_http_url(candidate_url):
        return None

    path = urlparse(candidate_url).path.lower()
    text = alt or path
    if not any(path.endswith(extension) for extension in IMAGE_EXTENSIONS):
        return None

    if not _has_strict_menu_signal(candidate_url, text):
        return None

    score, reasons = _score_candidate(
        candidate_url,
        text,
        location_hint=location_hint,
    )
    if score <= 0:
        return None

    return MenuSourceRecord(
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
        website_url=website_url,
        candidate_url=candidate_url,
        source_type="image",
        link_text=alt or None,
        confidence=min(score, 1.0),
        evidence_grade=_evidence_grade(min(score, 1.0), "unvalidated", "image"),
        reason="; ".join(reasons),
        is_primary_menu_candidate=_is_primary_menu_candidate(candidate_url, text),
        validation_status="unvalidated",
        validation_reason="candidate not fetched yet",
        fetched_at=fetched_at,
        raw_payload={"src": src, "alt": alt},
    )


def _records_from_schema_org(
    *,
    html: str,
    page_url: str,
    website_url: str,
    restaurant_name: str | None,
    restaurant_source_id: str | None,
    fetched_at: str,
    location_hint: str | None,
) -> list[MenuSourceRecord]:
    return _records_from_schema_org_items(
        items=_extract_json_ld_items(html),
        page_url=page_url,
        website_url=website_url,
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
        fetched_at=fetched_at,
        location_hint=location_hint,
    )


def _records_from_schema_org_items(
    *,
    items: list[dict[str, Any]],
    page_url: str,
    website_url: str,
    restaurant_name: str | None,
    restaurant_source_id: str | None,
    fetched_at: str,
    location_hint: str | None,
) -> list[MenuSourceRecord]:
    records = []
    for item in items:
        menu_values = _schema_menu_values(item)
        if not menu_values:
            continue
        schema_type = _schema_type_text(item.get("@type"))
        for value in menu_values:
            menu_url = _schema_value_to_url(value)
            if not menu_url:
                continue
            candidate_url = urljoin(page_url, menu_url)
            if not _is_http_url(candidate_url):
                continue
            records.append(
                _record_from_schema_org_menu(
                    candidate_url=candidate_url,
                    website_url=website_url,
                    restaurant_name=restaurant_name,
                    restaurant_source_id=restaurant_source_id,
                    fetched_at=fetched_at,
                    location_hint=location_hint,
                    schema_type=schema_type,
                    schema_payload=item,
                )
            )
    return records


def _record_from_schema_org_menu(
    *,
    candidate_url: str,
    website_url: str,
    restaurant_name: str | None,
    restaurant_source_id: str | None,
    fetched_at: str,
    location_hint: str | None,
    schema_type: str,
    schema_payload: dict[str, Any],
) -> MenuSourceRecord:
    source_type = _source_type(candidate_url, "schema.org menu")
    score, reasons = _score_candidate(
        candidate_url,
        "schema.org hasMenu menu",
        location_hint=location_hint,
    )
    confidence = min(1.0, max(0.75, score + 0.35))
    reasons.insert(0, "Schema.org menu URL")
    if schema_type:
        reasons.append(f"schema type: {schema_type}")

    return MenuSourceRecord(
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
        website_url=website_url,
        candidate_url=candidate_url,
        source_type="schema_org_menu" if source_type == "website_link" else source_type,
        link_text="Schema.org menu",
        confidence=confidence,
        evidence_grade=_evidence_grade(confidence, "unvalidated", "schema_org_menu"),
        reason="; ".join(reasons),
        is_primary_menu_candidate=True,
        validation_status="unvalidated",
        validation_reason="candidate found in Schema.org JSON-LD",
        fetched_at=fetched_at,
        raw_payload={"schema_type": schema_type, "json_ld": schema_payload},
    )


def _score_candidate(
    url: str,
    text: str,
    *,
    location_hint: str | None,
) -> tuple[float, list[str]]:
    haystack = _candidate_signal_text(url, text)
    reasons = []
    score = 0.0

    strict_keywords = [
        keyword for keyword in STRICT_MENU_KEYWORDS if keyword in haystack
    ]
    if strict_keywords:
        score += min(0.2 * len(strict_keywords), 0.7)
        reasons.append(f"strict keywords: {', '.join(strict_keywords[:5])}")

    secondary_keywords = [
        keyword for keyword in SECONDARY_MENU_KEYWORDS if keyword in haystack
    ]
    if secondary_keywords:
        score += min(0.08 * len(secondary_keywords), 0.25)
        reasons.append(f"secondary keywords: {', '.join(secondary_keywords[:5])}")

    parsed = urlparse(url)
    if parsed.path.lower().endswith(".pdf"):
        score += 0.35
        reasons.append("PDF link")

    if any(hint in parsed.netloc.lower() for hint in ORDERING_HOST_HINTS):
        score += 0.35
        reasons.append("known ordering/menu platform")

    if re.search(r"/menu/?$", parsed.path.lower()):
        score += 0.25
        reasons.append("menu-like path")

    if location_hint:
        location_tokens = _location_tokens(location_hint)
        path = parsed.path.lower()
        if any(token in path for token in location_tokens):
            score += 0.2
            reasons.append(f"matches location hint: {location_hint}")
        elif _looks_location_specific(path):
            score -= 0.15
            reasons.append("possible different-location menu")

    return score, reasons


def _validate_record(
    record: MenuSourceRecord,
    user_agent: str,
    fetch_mode: str,
) -> MenuSourceRecord:
    if record.source_type == "pdf":
        url_lower = record.candidate_url.lower()
        _FOOD_PDF_KEYWORDS = ("allergen", "allerg", "nutrition", "menu", "food", "diet")
        if not any(kw in url_lower for kw in _FOOD_PDF_KEYWORDS):
            return _replace_validation(
                record,
                validation_status="rejected",
                validation_reason="PDF URL contains no food/allergen keywords — likely a corporate or legal document",
                confidence_delta=-0.3,
            )
        return _replace_validation(
            record,
            validation_status="validated",
            validation_reason="PDF menu-like candidate",
            confidence_delta=0.2,
        )

    if record.source_type in ["image", "ordering_page"]:
        return _replace_validation(
            record,
            validation_status="unvalidated",
            validation_reason=f"{record.source_type} not fetched in this step",
            confidence_delta=0.0,
        )

    try:
        html = _fetch_text(
            record.candidate_url,
            user_agent=user_agent,
            fetch_mode=fetch_mode,
        )
    except MenuSourceError as exc:
        return _replace_validation(
            record,
            validation_status="not_fetchable",
            validation_reason=str(exc),
            confidence_delta=-0.15,
        )

    # Parse the candidate page once; reuse the tree for both text and image checks.
    soup = make_soup(html)
    page_text = _visible_text_from_soup(soup)
    score, reasons = _score_menu_page_text(page_text)
    if score >= 0.35:
        return _replace_validation(
            record,
            validation_status="validated",
            validation_reason="; ".join(reasons),
            confidence_delta=score,
        )

    image_menu_score, image_menu_reasons = _score_image_menu_page_from_soup(record, soup)
    if image_menu_score >= 0.3:
        return _replace_validation(
            record,
            validation_status="validated",
            validation_reason="; ".join(image_menu_reasons),
            confidence_delta=image_menu_score,
        )

    # Allergen/nutrition matrix pages often have few prices and few menu-item
    # words, so the checks above would drop them — yet a dish x allergen grid is
    # the most valuable allergen source we can find. Validate it explicitly.
    try:
        if looks_like_allergen_matrix(soup):
            return _replace_validation(
                record,
                validation_status="validated",
                validation_reason="allergen matrix table detected",
                confidence_delta=0.4,
            )
    except Exception:
        pass

    return _replace_validation(
        record,
        validation_status="unvalidated",
        validation_reason="candidate page did not contain enough menu-like text",
        confidence_delta=-0.2,
    )


def _replace_validation(
    record: MenuSourceRecord,
    *,
    validation_status: str,
    validation_reason: str,
    confidence_delta: float,
) -> MenuSourceRecord:
    return MenuSourceRecord(
        **{
            **asdict(record),
            "confidence": max(0.0, min(1.0, record.confidence + confidence_delta)),
            "evidence_grade": _evidence_grade(
                max(0.0, min(1.0, record.confidence + confidence_delta)),
                validation_status,
                record.source_type,
            ),
            "validation_status": validation_status,
            "validation_reason": validation_reason,
        }
    )


def _evidence_grade(confidence: float, validation_status: str, source_type: str) -> str:
    if validation_status == "validated" and source_type in [
        "website_link",
        "pdf",
        "nutrition_or_allergen_page",
        "schema_org_menu",
    ]:
        return "A"
    if source_type == "ordering_page" and confidence >= 0.35:
        return "B"
    if source_type == "image":
        return "C"
    if validation_status == "not_fetchable":
        return "F"
    if confidence >= 0.6:
        return "B"
    if confidence >= 0.3:
        return "C"
    return "D"


def _score_menu_page_text(text: str) -> tuple[float, list[str]]:
    normalized = text.lower()
    reasons = []
    score = 0.0

    strict_count = sum(1 for keyword in STRICT_MENU_KEYWORDS if keyword in normalized)
    item_count = sum(1 for keyword in MENU_ITEM_HINTS if keyword in normalized)
    price_count = len(PRICE_PATTERN.findall(normalized))

    if strict_count:
        score += min(strict_count * 0.12, 0.35)
        reasons.append(f"{strict_count} strict menu terms")
    if item_count >= 3:
        score += min(item_count * 0.04, 0.35)
        reasons.append(f"{item_count} menu item hints")
    if price_count >= 3:
        score += 0.25
        reasons.append(f"{price_count} price-like patterns")

    return score, reasons


def _score_image_menu_page(record: MenuSourceRecord, html: str) -> tuple[float, list[str]]:
    return _score_image_menu_page_from_soup(record, make_soup(html))


def _score_image_menu_page_from_soup(
    record: MenuSourceRecord, soup: BeautifulSoup
) -> tuple[float, list[str]]:
    if not record.is_primary_menu_candidate:
        return 0.0, []

    links, images = _links_and_images_from_soup(soup)
    menu_context_images = [
        (src, text)
        for src, text in images
        if _has_strict_menu_signal(src, text)
    ]
    menu_tab_count = sum(
        1
        for href, text in links
        if href.strip().startswith("#") and _has_strict_menu_signal(href, text)
    )
    menu_section_count = len(
        soup.find_all(
            attrs={
                "class": lambda value: value
                and "menu" in _class_text(value).lower().split()
            }
        )
    )

    score = 0.0
    reasons = []
    if len(menu_context_images) >= 3:
        score += 0.25
        reasons.append(f"{len(menu_context_images)} image candidates in menu context")
    if menu_tab_count >= 2:
        score += 0.12
        reasons.append(f"{menu_tab_count} menu tab anchors")
    if menu_section_count:
        score += 0.08
        reasons.append("menu section contains image content")

    return score, reasons


def _visible_text_from_soup(soup: BeautifulSoup) -> str:
    _remove_non_content_tags(soup)
    return soup.get_text(" ", strip=True)


def _source_type(url: str, text: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    haystack = f"{url} {text}".lower()

    if path.endswith(".pdf"):
        return "pdf"
    if any(path.endswith(extension) for extension in IMAGE_EXTENSIONS):
        return "image"
    if any(hint in host for hint in ORDERING_HOST_HINTS) or "order" in haystack:
        return "ordering_page"
    if "allergen" in haystack or "nutrition" in haystack:
        return "nutrition_or_allergen_page"
    return "website_link"


def _is_primary_menu_candidate(url: str, text: str) -> bool:
    if _source_type(url, text) in ["pdf", "nutrition_or_allergen_page"]:
        return True
    return _has_strict_menu_signal(url, text)


def _has_strict_menu_signal(url: str, text: str) -> bool:
    haystack = _candidate_signal_text(url, text)
    return _STRICT_KEYWORD_RE.search(haystack) is not None


def _candidate_signal_text(url: str, text: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.path} {parsed.query} {text}".lower()


def _location_tokens(location_hint: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^a-z0-9]+", location_hint.lower())
        if len(token) >= 4
    ]


def _looks_location_specific(path: str) -> bool:
    location_tokens = [
        "cupertino",
        "sunnyvale",
        "santa-clara",
        "santaclara",
        "san-jose",
        "sanjose",
        "fremont",
        "dublin",
        "milpitas",
        "mountain-view",
        "mountainview",
        "palo-alto",
        "paloalto",
    ]
    return any(token in path for token in location_tokens)


def _fetch_sitemap_pages(
    website_url: str,
    user_agent: str,
    fetch_mode: str,
    max_workers: int = 1,
) -> list[tuple[str, str]]:
    parsed = urlparse(website_url)
    sitemap_urls = [
        f"{parsed.scheme}://{parsed.netloc}/sitemap.xml",
        f"{parsed.scheme}://{parsed.netloc}/page-sitemap.xml",
    ]

    discovered_urls = []
    for sitemap_url in sitemap_urls:
        try:
            sitemap = _fetch_raw_text(sitemap_url, user_agent=user_agent)
        except MenuSourceError:
            continue
        discovered_urls.extend(
            re.findall(r"<loc>\s*(.*?)\s*</loc>", sitemap, flags=re.IGNORECASE)
        )

    likely_urls = [
        url
        for url in dict.fromkeys(discovered_urls)
        if _has_strict_menu_signal(url, "")
    ][:8]

    return _fetch_pages_concurrent(
        likely_urls,
        user_agent=user_agent,
        fetch_mode=fetch_mode,
        max_workers=max_workers,
    )


def _schema_menu_values(item: dict[str, Any]) -> list[Any]:
    values = []
    for field in ["hasMenu", "menu"]:
        value = item.get(field)
        if value is None:
            continue
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values


def _schema_value_to_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    for field in ["url", "@id", "sameAs"]:
        field_value = value.get(field)
        if isinstance(field_value, str) and field_value.strip():
            return field_value.strip()
    return None


def _schema_type_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _crawl_likely_pages(
    *,
    base_url: str,
    html: str,
    user_agent: str,
    fetch_mode: str,
    max_workers: int = 1,
) -> list[tuple[str, str]]:
    links, _images = _extract_links_and_images(html)

    base_host = urlparse(base_url).netloc
    likely_urls = []
    for href, text in links:
        url = urljoin(base_url, href)
        if not _is_http_url(url):
            continue
        if urlparse(url).netloc != base_host:
            continue
        signal = f"{url} {text}".lower()
        if _has_strict_menu_signal(url, text) or "location" in signal or "dining" in signal:
            likely_urls.append(url)

    return _fetch_pages_concurrent(
        list(dict.fromkeys(likely_urls))[:10],
        user_agent=user_agent,
        fetch_mode=fetch_mode,
        max_workers=max_workers,
    )


def _fetch_pages_concurrent(
    urls: list[str],
    *,
    user_agent: str,
    fetch_mode: str,
    max_workers: int,
) -> list[tuple[str, str]]:
    def fetch(url: str) -> tuple[str, str] | None:
        try:
            return url, _fetch_text(url, user_agent=user_agent, fetch_mode=fetch_mode)
        except MenuSourceError:
            return None

    results = map_concurrent(fetch, urls, max_workers=max_workers)
    return [page for page in results if page is not None]


def _filter_validated_records(
    records: list[MenuSourceRecord],
    *,
    include_ordering_pages: bool,
    include_images: bool,
) -> list[MenuSourceRecord]:
    kept = []
    for record in records:
        if record.source_type == "ordering_page":
            if include_ordering_pages:
                kept.append(record)
            continue

        if record.source_type == "image":
            if include_images:
                kept.append(record)
            continue

        if record.validation_status == "validated" and record.is_primary_menu_candidate:
            kept.append(record)
    return kept


def _dedupe_records(rows: list[MenuSourceRecord]) -> list[MenuSourceRecord]:
    best_by_url: dict[str, MenuSourceRecord] = {}
    for row in rows:
        key = _canonical_url(row.candidate_url)
        existing = best_by_url.get(key)
        if existing is None or row.confidence > existing.confidence:
            best_by_url[key] = row
    return list(best_by_url.values())


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl().rstrip("/")


def _is_http_url(url: str) -> bool:
    return urlparse(url).scheme in ["http", "https"]


def _ensure_url_scheme(url: str) -> str:
    if urlparse(url).scheme:
        return url
    return f"https://{url}"




def _extract_links_and_images(html: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    return _links_and_images_from_soup(make_soup(html))


def _links_and_images_from_soup(
    soup: BeautifulSoup,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    links = []
    for anchor in soup.find_all("a"):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        text = anchor.get_text(" ", strip=True)
        links.append((href, text))

    images = []
    for image in soup.find_all("img"):
        src = (image.get("src") or "").strip()
        if not src:
            continue
        alt = (image.get("alt") or "").strip()
        context_text = _image_context_text(image)
        images.append((src, " ".join(part for part in [alt, context_text] if part)))

    return links, images


def _image_context_text(image: Any) -> str:
    context_parts = []
    for attr in ["title", "aria-label"]:
        value = image.get(attr)
        if value:
            context_parts.append(str(value))

    node = image.parent
    for _ in range(4):
        if not node or not getattr(node, "name", None):
            break
        for attr in ["id", "class", "role", "aria-label"]:
            value = node.get(attr)
            if value:
                context_parts.append(_class_text(value))
        node = node.parent

    return " ".join(part for part in context_parts if part).strip()




