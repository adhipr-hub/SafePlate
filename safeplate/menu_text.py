from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import re

from bs4 import BeautifulSoup

from safeplate.concurrency import map_concurrent
from safeplate.config import get_fetch_concurrency
from safeplate.fetching import fetch_url_bytes
from safeplate.io import timestamped_output_paths
from safeplate.io import write_dataclass_csv
from safeplate.io import write_dataclass_json
from safeplate.page_fetch import PageFetchError, fetch_html_page
from safeplate.schema_org import json_ld_items_from_soup as _json_ld_items_from_soup
from safeplate.soup import make_soup
from safeplate.soup import remove_non_content_tags as _remove_non_content_tags
from safeplate.textutil import PRICE_PATTERN
from safeplate.textutil import classlist_text as _classlist_text
from safeplate.textutil import clean_text as _clean_text


MENU_TEXT_CSV_FIELDS = [
    "restaurant_name",
    "restaurant_source_id",
    "menu_source_url",
    "source_type",
    "extraction_method",
    "char_count",
    "price_count",
    "dietary_terms",
    "allergen_terms",
    "fetched_at",
    "extracted_text",
]

MENU_ITEM_CSV_FIELDS = [
    "restaurant_name",
    "restaurant_source_id",
    "menu_source_url",
    "category",
    "item_name",
    "description",
    "price",
    "dietary_terms",
    "allergen_terms",
    "source_type",
    "extraction_method",
    "confidence",
    "raw_text",
    "fetched_at",
]

HTML_MENU_SOURCE_TYPES = [
    "website_link",
    "nutrition_or_allergen_page",
    "schema_org_menu",
    "ordering_page",
]

PDF_MENU_SOURCE_TYPES = ["pdf"]
IMAGE_MENU_SOURCE_TYPES = ["image"]
TEXT_EXTRACTABLE_SOURCE_TYPES = (
    HTML_MENU_SOURCE_TYPES + PDF_MENU_SOURCE_TYPES + IMAGE_MENU_SOURCE_TYPES
)

# The HTML path has a single fixed extraction method (visible text after soup cleaning);
# it does not vary by fetch mode. A single constant makes that explicit.
_HTML_EXTRACTION_METHOD = "html_visible_text"

DIETARY_TERMS = [
    "vegan",
    "vegetarian",
    "gluten-free",
    "gluten free",
    "dairy-free",
    "dairy free",
    "halal",
    "kosher",
]

ALLERGEN_TERMS = [
    "peanut",
    "peanuts",
    "tree nut",
    "nuts",
    "almond",
    "cashew",
    "walnut",
    "pecan",
    "hazelnut",
    "pistachio",
    "macadamia",
    "chestnut",
    "pine nut",
    "brazil nut",
    "filbert",
    # Definitional nut-derived ingredients: the named thing IS (mostly) a nut, so a
    # literal listing is GROUNDED tree-nut evidence, not merely a dish prior. Kept to
    # the >=0.95-certain set; merely-'usually'-nut names (pesto, nougat, romesco) stay
    # priors only. All are mirrored in allergen_score._TREE_NUT_TERMS.
    "marzipan",
    "frangipane",
    "gianduja",
    "nutella",
    "pignoli",
    "sesame",
    "soy",
    "shellfish",
    "shrimp",
    "crab",
    "egg",
    "milk",
    "dairy",
    "wheat",
    "gluten",
    "allergen",
    "allergy",
    # Multilingual NUT ingredient words so a foreign-language menu that literally
    # names a nut still produces a grounded allergen hit. Ingredient words only
    # (not dish names); ambiguous coconut/nutmeg collisions deliberately excluded.
    "cacahuete", "cacahuate", "cacahuète", "arachide", "arachidi", "erdnuss",
    "amendoim", "落花生", "ピーナッツ", "花生", "땅콩", "ถั่วลิสง", "मूंगफली",
    "арахис", "almendra", "amande", "mandel", "mandorla", "amêndoa", "badem",
    "アーモンド", "杏仁", "아몬드", "बादाम", "миндаль", "لوز", "anacardo", "cajou",
    "カシューナッツ", "腰果", "काजू", "كاجو", "avellana", "noisette", "haselnuss",
    "nocciola", "fındık", "ヘーゼルナッツ", "بندق", "фундук", "pistacho", "pistache",
    "pistazie", "pistacchio", "ピスタチオ", "开心果", "فستق", "walnuss", "くるみ",
    "核桃", "호두", "अखरोट", "ceviz", "pinoli", "松子", "잣",
    # Reverse-gap nut INGREDIENT words the prior knows but free-text extraction did
    # not (ingredient words only, not dish names). Distinctive (accented/non-Latin),
    # so substring matching won't collide with common menu words.
    "đậu phộng", "فول سوداني", "fıstığı",            # peanut (vi/ar/tr)
    "hạnh nhân",                                      # almond (vi)
    "anacardi", "cashewkern", "hạt điều", "кешью",   # cashew (it/de/vi/ru)
    "avelã", "фисташки", "クルミ", "piñón",           # hazelnut/pistachio/walnut/pine nut
]

ITEM_NAME_CONNECTORS = {"and", "&", "of", "the", "with", "a", "an", "to", "de", "la"}

CATEGORY_HINTS = [
    "appetizers",
    "starters",
    "small plates",
    "shared plates",
    "large plates",
    "plates",
    "snacks",
    "salads",
    "soups",
    "entrees",
    "mains",
    "sandwiches",
    "bowls",
    "noodles",
    "rice",
    "desserts",
    "sweets",
    "drinks",
    "beverages",
    "cocktails",
    "beer",
    "wine",
    "coffee",
    "tea",
    "lunch",
    "dinner",
    "brunch",
    "breakfast",
    "sides",
    "vegetarian",
    "vegan",
    "water",
    "coffee tea",
    "juice lemonade soda",
    "sparkling wine rose",
    "white wine",
    "red wine",
    "breakfast plates",
    "bread and pastries",
    "bread pastries",
    "salads soup",
    "salads soups",
    "burgers",
    "pancakes",
    "kids menu",
    "homemade desserts",
    "to share to add",
    "to share",
    "to add",
]

# Precomputed once: CATEGORY_HINTS reduced to their alnum-collapsed match keys. Built
# at import (CATEGORY_HINTS is a constant) so _looks_like_category doesn't rebuild this
# set on every menu line. Identical membership semantics, just hoisted out of the hot loop.
_CATEGORY_KEYS = frozenset(
    re.sub(r"[^a-z0-9]+", " ", hint).strip() for hint in CATEGORY_HINTS
)

ITEM_NEGATIVE_SIGNALS = [
    "free shipping",
    "terms apply",
    "gifts under",
    "gift boxes",
    "wishlist",
    "choose price range",
    "gratuity",
    "take out/delivery",
    "delivery orders",
    "your cart",
    "shopping cart",
    "checkout",
    "subtotal",
    "service fee",
]

BARE_PRICE_PATTERN = re.compile(
    r"(?<![\w.$])(?:[2-9]|[1-9]\d|1\d{2})(?:\.\d{1,2})?(?![\w.])",
    flags=re.IGNORECASE,
)
NON_PRICE_FOLLOWERS = [
    "%",
    "am",
    "pm",
    "cal",
    "g",
    "gram",
    "grams",
    "kg",
    "kcal",
    "lb",
    "lbs",
    "ml",
    "oz",
    "year",
    "years",
]
NON_PRICE_PRECEDERS = [
    "abv",
    "cal",
    "kcal",
    "form",
    "section",
]

NON_MENU_BARE_PRICE_PHRASES = (
    "modern slavery act",
    "pursuant to",
    "requirements of section",
    "conservation international",
    "retail industry leaders association",
    "including our most",
    "periodic reports",
    "central tea buying",
    "central cocoa purchasing",
)


@dataclass(frozen=True)
class MenuTextRecord:
    restaurant_name: str
    restaurant_source_id: str
    menu_source_url: str
    source_type: str
    extraction_method: str
    char_count: int
    price_count: int
    dietary_terms: list[str]
    allergen_terms: list[str]
    fetched_at: str
    extracted_text: str


@dataclass(frozen=True)
class MenuItemRecord:
    restaurant_name: str
    restaurant_source_id: str
    menu_source_url: str
    category: str
    item_name: str
    description: str
    price: str
    dietary_terms: list[str]
    allergen_terms: list[str]
    source_type: str
    extraction_method: str
    confidence: float
    raw_text: str
    fetched_at: str
    # Allergen-matrix metadata: the canonical allergen tokens that had a COLUMN in the
    # source chart (e.g. ("peanut", "tree nut", "milk")). Lets the scorer tell "the chart
    # has a nut column and this dish wasn't marked" from "the chart never covered nuts" --
    # only the former is evidence of nut-absence. Empty for non-matrix records.
    matrix_allergen_columns: tuple[str, ...] = ()
    # Allergen charts list a dish followed by its component INGREDIENT sub-rows (Burger
    # Bun, American Cheese, ShackSauce). ``is_component`` marks such a sub-row and
    # ``parent_item`` names the dish it belongs to, so the pipeline can fold a
    # component's allergens up into its parent and show only orderable dishes -- without
    # losing any allergen data. False/"" for ordinary top-level items.
    is_component: bool = False
    parent_item: str = ""
    # Allergens an allergen chart marks as CROSS-CONTACT / "may contain" / shared-facility
    # for this dish (a separate symbol from "contains"). Kept apart from ``allergen_terms``
    # (which is presence) so the scorer can treat a nut here as a trace-risk floor for
    # cross-contact-sensitive users WITHOUT calling the dish a confirmed nut dish.
    cross_contact_terms: list[str] = field(default_factory=list)


class MenuTextError(RuntimeError):
    """Raised when menu text extraction fails."""


def extract_menu_text_from_sources(
    *,
    menu_source_rows: list[dict[str, str]],
    user_agent: str,
    include_unvalidated: bool = False,
    max_chars: int = 12000,
    fetch_mode: str = "static",
    max_workers: int | None = None,
) -> list[MenuTextRecord]:
    """Back-compat wrapper. Returns only the text records; see
    `extract_menu_from_sources` for the combined (text + items) single-pass API."""
    text_records, _items = extract_menu_from_sources(
        menu_source_rows=menu_source_rows,
        user_agent=user_agent,
        include_unvalidated=include_unvalidated,
        max_chars=max_chars,
        fetch_mode=fetch_mode,
        max_workers=max_workers,
    )
    return text_records

def extract_menu_items_from_sources(
    *,
    menu_source_rows: list[dict[str, str]],
    user_agent: str,
    include_unvalidated: bool = False,
    max_items_per_source: int = 250,
    fetch_mode: str = "static",
    max_workers: int | None = None,
    use_llm_fallback: bool = False,
    gemini_api_key: str | None = None,
    gemini_model: str | None = None,
) -> list[MenuItemRecord]:
    """Back-compat wrapper. Returns only the item records; see
    `extract_menu_from_sources` for the combined (text + items) single-pass API."""
    _texts, item_records = extract_menu_from_sources(
        menu_source_rows=menu_source_rows,
        user_agent=user_agent,
        include_unvalidated=include_unvalidated,
        max_items_per_source=max_items_per_source,
        fetch_mode=fetch_mode,
        max_workers=max_workers,
        use_llm_fallback=use_llm_fallback,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
    )
    return item_records


def _eligible_source_rows(menu_source_rows, include_unvalidated):
    return [
        row
        for row in menu_source_rows
        if _should_extract_row(row, include_unvalidated=include_unvalidated)
        and row.get("source_type") in TEXT_EXTRACTABLE_SOURCE_TYPES
    ]


def _stage_workers(max_workers, fetch_mode):
    workers = max_workers if max_workers is not None else get_fetch_concurrency()
    # Playwright sync = single thread.
    return 1 if fetch_mode != "static" else workers


def _build_text_record(row, raw_text, extraction_method, max_chars):
    source_type = row.get("source_type", "")
    text = _clean_text(raw_text)[:max_chars]
    dietary_terms, allergen_terms = _dietary_and_allergen_terms(text)
    return MenuTextRecord(
        restaurant_name=row.get("restaurant_name", ""),
        restaurant_source_id=row.get("restaurant_source_id", ""),
        menu_source_url=row.get("candidate_url", ""),
        source_type=source_type,
        extraction_method=extraction_method,
        char_count=len(text),
        price_count=_price_count(
            text,
            allow_bare_prices=source_type in PDF_MENU_SOURCE_TYPES + IMAGE_MENU_SOURCE_TYPES,
        ),
        dietary_terms=dietary_terms,
        allergen_terms=allergen_terms,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        extracted_text=text,
    )


def _extract_source_once(
    row,
    *,
    user_agent,
    fetch_mode,
    use_llm_fallback,
    gemini_api_key,
    gemini_model,
):
    """Fetch + parse one source ONCE. Returns
    (source_type, items_method, candidates, raw_text, text_method).
    raw_text is "" when the source produced no usable text (e.g. image menus)."""
    source_type = row.get("source_type", "")
    url = row.get("candidate_url", "")
    raw_text = ""
    text_method = ""

    if source_type in HTML_MENU_SOURCE_TYPES:
        html = _fetch_html(url, user_agent=user_agent, fetch_mode=fetch_mode)
        items_method = _HTML_EXTRACTION_METHOD
        soup = make_soup(html)
        from safeplate.allergen_matrix import items_from_allergen_matrix_soup

        candidates = (
            items_from_allergen_matrix_soup(soup)
            + _extract_schema_org_menu_items_from_soup(soup)
            + _extract_menu_items_from_soup(soup, extraction_method=items_method)
        )
        # _extract_menu_items_from_soup runs _remove_non_content_tags, so this is
        # the same cleaned visible text the old text stage produced.
        raw_text = soup.get_text(" ", strip=True)
        text_method = _HTML_EXTRACTION_METHOD
        if not candidates:
            candidates = _recover_html_items(
                html=html,
                url=url,
                restaurant_name=row.get("restaurant_name", ""),
                restaurant_source_id=row.get("restaurant_source_id", ""),
                use_llm_fallback=use_llm_fallback,
                gemini_api_key=gemini_api_key,
                gemini_model=gemini_model,
            )
    elif source_type == "image" and use_llm_fallback and gemini_api_key:
        from safeplate.menu_fetch_llm import DEFAULT_MODEL, extract_items_via_gemini_image

        raw, content_type = _fetch_bytes(url, user_agent=user_agent)
        candidates = extract_items_via_gemini_image(
            raw,
            content_type=content_type,
            restaurant_name=row.get("restaurant_name", ""),
            restaurant_source_id=row.get("restaurant_source_id", ""),
            api_key=gemini_api_key,
            model=gemini_model or DEFAULT_MODEL,
            cache_key=url,
        )
        items_method = "gemini_image"
    elif source_type == "pdf":
        raw, _content_type = _fetch_bytes(url, user_agent=user_agent)
        candidates, items_method, raw_text = _extract_pdf_items_from_bytes(
            raw,
            restaurant_name=row.get("restaurant_name", ""),
            restaurant_source_id=row.get("restaurant_source_id", ""),
            use_llm_fallback=use_llm_fallback,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
        )
        text_method = "pdf_text"
    else:
        text, items_method = extract_text_for_menu_source(
            row, user_agent=user_agent, fetch_mode=fetch_mode
        )
        candidates = _extract_menu_items_from_text(text)
        raw_text = text
        text_method = items_method

    return source_type, items_method, candidates, raw_text, text_method


def _items_from_candidates(fetched, max_items_per_source):
    records = []
    seen = set()
    for result in fetched:
        if result is None:
            continue
        row, source_type, extraction_method, candidates = result
        for item in candidates[:max_items_per_source]:
            key = _dedupe_item_key(row, item)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                MenuItemRecord(
                    restaurant_name=row.get("restaurant_name", ""),
                    restaurant_source_id=row.get("restaurant_source_id", ""),
                    menu_source_url=row.get("candidate_url", ""),
                    category=item.category,
                    item_name=item.item_name,
                    description=item.description,
                    price=item.price,
                    dietary_terms=item.dietary_terms,
                    allergen_terms=item.allergen_terms,
                    source_type=source_type,
                    extraction_method=item.extraction_method or extraction_method,
                    confidence=item.confidence,
                    raw_text=item.raw_text,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                )
            )
    return records


def extract_menu_from_sources(
    *,
    menu_source_rows: list[dict[str, str]],
    user_agent: str,
    include_unvalidated: bool = False,
    max_chars: int = 12000,
    max_items_per_source: int = 250,
    fetch_mode: str = "static",
    max_workers: int | None = None,
    use_llm_fallback: bool = False,
    gemini_api_key: str | None = None,
    gemini_model: str | None = None,
) -> tuple[list[MenuTextRecord], list[MenuItemRecord]]:
    """Fetch + parse each source ONCE, returning (text_records, item_records).

    Replaces calling extract_menu_text_from_sources and
    extract_menu_items_from_sources separately (which each fetched and parsed
    every source). Same outputs, one pass per source. Callers that only want
    one side use the back-compat wrappers above."""
    eligible_rows = _eligible_source_rows(menu_source_rows, include_unvalidated)

    def work(row):
        try:
            source_type, items_method, candidates, raw_text, text_method = (
                _extract_source_once(
                    row,
                    user_agent=user_agent,
                    fetch_mode=fetch_mode,
                    use_llm_fallback=use_llm_fallback,
                    gemini_api_key=gemini_api_key,
                    gemini_model=gemini_model,
                )
            )
        except MenuTextError:
            return None
        text_record = (
            _build_text_record(row, raw_text, text_method, max_chars)
            if raw_text.strip()
            else None
        )
        return text_record, (row, source_type, items_method, candidates)

    workers = _stage_workers(max_workers, fetch_mode)
    results = map_concurrent(work, eligible_rows, max_workers=workers)
    text_records = [r[0] for r in results if r is not None and r[0] is not None]
    item_records = _items_from_candidates(
        [r[1] for r in results if r is not None], max_items_per_source
    )
    return text_records, item_records

def extract_text_for_menu_source(
    row: dict[str, str],
    *,
    user_agent: str,
    fetch_mode: str = "static",
) -> tuple[str, str]:
    source_type = row.get("source_type", "")
    url = row.get("candidate_url", "")
    if source_type in HTML_MENU_SOURCE_TYPES:
        return (
            extract_visible_text(
                url,
                user_agent=user_agent,
                fetch_mode=fetch_mode,
            ),
            _HTML_EXTRACTION_METHOD,
        )
    if source_type == "pdf":
        return extract_pdf_text(url, user_agent=user_agent), "pdf_text"
    # Image menus are read by Gemini vision in the item-extraction stage
    # (extract_menu_items_from_sources), not here.
    raise MenuTextError(f"Unsupported menu source type for extraction: {source_type}")


def extract_visible_text(
    url: str,
    user_agent: str,
    fetch_mode: str = "static",
) -> str:
    html = _fetch_html(url, user_agent=user_agent, fetch_mode=fetch_mode)
    soup = make_soup(html)
    _remove_non_content_tags(soup)
    return soup.get_text(" ", strip=True)


def extract_pdf_text(url: str, user_agent: str) -> str:
    raw, _content_type = _fetch_bytes(url, user_agent=user_agent)
    text = _pdf_text_from_bytes(raw).strip()
    if not text:
        raise MenuTextError(f"No extractable PDF text found in {url}")
    return text


def _extract_pdf_items_from_bytes(
    raw: bytes,
    *,
    restaurant_name: str,
    restaurant_source_id: str,
    use_llm_fallback: bool,
    gemini_api_key: str | None,
    gemini_model: str | None,
):
    """Extract items from PDF bytes; returns (candidates, method, flat_text) so the
    flat text can build a MenuTextRecord without re-parsing the PDF."""
    from safeplate.allergen_matrix import extract_items_from_allergen_pdf

    text = _pdf_text_from_bytes(raw)
    matrix_items = extract_items_from_allergen_pdf(raw)
    if any(item.allergen_terms for item in matrix_items):
        return matrix_items, "allergen_matrix_pdf", text

    looks_allergen = "allergen" in text.lower() or "allergy" in text.lower()
    if looks_allergen and use_llm_fallback and gemini_api_key:
        from safeplate.menu_fetch_llm import (
            DEFAULT_MODEL,
            extract_allergen_matrix_via_gemini_pdf,
        )

        vision_items = extract_allergen_matrix_via_gemini_pdf(
            raw,
            restaurant_name=restaurant_name,
            restaurant_source_id=restaurant_source_id,
            api_key=gemini_api_key,
            model=gemini_model or DEFAULT_MODEL,
        )
        if any(item.allergen_terms for item in vision_items):
            return vision_items, "gemini_allergen_matrix", text

    return _extract_menu_items_from_text(text), "pdf_text", text

# Cap pages parsed from any single PDF. Allergen charts/menus are a handful of pages;
# a 100+ page nutrition PDF (e.g. a big chain's) would otherwise stall a worker for
# minutes. Truncating keeps extraction bounded so we can front-load all restaurants.
_PDF_MAX_PAGES = 40


def _pdf_text_from_bytes(raw: bytes) -> str:
    """Extract text from PDF bytes (first ``_PDF_MAX_PAGES`` pages). Prefers PyMuPDF
    (C-based, ~10-50x faster than pure-Python parsers); falls back to pypdf if it's
    not installed so the path degrades rather than breaks."""
    from safeplate.timing import span

    with span("pdf_parse"):
        return _pdf_text_from_bytes_inner(raw)


def _pdf_text_from_bytes_inner(raw: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        fitz = None
    if fitz is not None:
        try:
            # Skip image decoding: we only want the text layer here (the vision path
            # renders pages separately). Dropping image work is up to ~2x faster on
            # icon-heavy chain PDFs and leaves the extracted text identical.
            text_flags = fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_IMAGES
            with fitz.open(stream=raw, filetype="pdf") as doc:
                pages = [
                    doc[i].get_text("text", flags=text_flags)
                    for i in range(min(doc.page_count, _PDF_MAX_PAGES))
                ]
            return "\n".join(pages)
        except Exception:
            pass  # fitz failed on this PDF (encrypted/malformed) -> try pypdf below
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(BytesIO(raw))
        return "\n".join(
            (page.extract_text() or "") for page in reader.pages[:_PDF_MAX_PAGES]
        )
    except Exception:
        return ""


def _recover_html_items(
    *,
    html: str,
    url: str,
    restaurant_name: str,
    restaurant_source_id: str,
    use_llm_fallback: bool,
    gemini_api_key: str | None,
    gemini_model: str | None,
) -> list[MenuItemRecord]:
    """Fallbacks for HTML pages the deterministic parser could not read.

    Tier C (free): scan JSON embedded in the page. Tier A (paid, opt-in): let
    Gemini fetch and read the URL. Imported lazily to avoid an import cycle.
    """
    from safeplate.embedded_json import extract_items_from_embedded_json

    items = extract_items_from_embedded_json(html)
    if items:
        return items

    if use_llm_fallback and gemini_api_key:
        from safeplate.menu_fetch_llm import (
            DEFAULT_MODEL,
            extract_items_via_gemini_url,
        )

        return extract_items_via_gemini_url(
            url,
            restaurant_name=restaurant_name,
            restaurant_source_id=restaurant_source_id,
            api_key=gemini_api_key,
            model=gemini_model or DEFAULT_MODEL,
        )
    return []


def _fetch_html(
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
        raise MenuTextError(str(exc)) from exc


def _fetch_bytes(url: str, user_agent: str) -> tuple[bytes, str]:
    return fetch_url_bytes(url, user_agent=user_agent, error_cls=MenuTextError)


def _extract_menu_items_from_html(
    html: str,
    extraction_method: str = "html_visible_text",
) -> list[MenuItemRecord]:
    return _extract_menu_items_from_soup(make_soup(html), extraction_method=extraction_method)


_MENU_CONTAINER_HINTS = (
    "menu", "dish", "product", "food", "plat", "plato", "piatto",
    "gericht", "comida", "speise",
)


def _has_menu_ancestor(node: object) -> bool:
    for _ in range(5):
        if node is None or not getattr(node, "get", None):
            break
        token = " ".join(
            [str(node.get("id") or ""), _classlist_text(node.get("class"))]
        ).lower()
        if any(hint in token for hint in _MENU_CONTAINER_HINTS):
            return True
        node = node.parent
    return False




_NAV_TAGS = {"nav", "header", "footer"}
_NAV_CLASS_RE = re.compile(
    r"nav(bar|igation|-|_|$)|breadcrumb|menu-toggle|site-?menu|main-?menu|"
    r"top-?menu|location|locator|filter|dropdown|lang(uage)?|country|region|"
    r"site-?header|site-?footer|page-?header|page-?footer|masthead|topbar|cookie",
    re.I,
)


def _in_navigation(node: object) -> bool:
    """True if the element sits inside site navigation / header / footer / a
    location or language picker — where a 'menu' class is UI, not a food menu."""
    for _ in range(7):
        if node is None or not getattr(node, "name", None):
            break
        if node.name in _NAV_TAGS:
            return True
        role = str(node.get("role") or "").lower()
        if role in ("navigation", "menubar", "banner", "contentinfo"):
            return True
        token = (str(node.get("id") or "") + " " + _classlist_text(node.get("class"))).lower()
        if _NAV_CLASS_RE.search(token):
            return True
        node = node.parent
    return False


def _container_item_name(el: object) -> str:
    for selector in ("[class*=name i]", "[class*=title i]", "h3", "h4", "h5", "strong", "b"):
        try:
            child = el.select_one(selector)
        except Exception:
            child = None
        if child:
            text = _clean_text(child.get_text(" ", strip=True))
            if text and len(text) <= 80:
                return text
    return _clean_text(el.get_text(" ", strip=True))


def _listed_items_from_soup(soup: BeautifulSoup) -> list[MenuItemRecord]:
    """Collect price-LESS menu items from real menu markup (li / menu containers).

    Many menus — especially outside the US — list dishes with no prices. Price is
    secondary for allergen work, so we still collect the item NAME, but only from
    elements inside menu-hinted markup to keep noise low.
    """
    records = []
    seen = set()
    candidates = list(soup.find_all("li"))
    candidates += soup.find_all(
        attrs={"class": lambda c: c and any(
            h in _classlist_text(c).lower() for h in _MENU_CONTAINER_HINTS)}
    )
    # A menu-classed <li> matches both selectors; de-dup by identity (Tag equality is
    # structural, so dict.fromkeys could wrongly merge distinct same-markup elements)
    # to avoid re-walking ancestors and re-running get_text on the same element.
    seen_ids: set[int] = set()
    candidates = [el for el in candidates if not (id(el) in seen_ids or seen_ids.add(id(el)))]
    for el in candidates:
        if not _has_menu_ancestor(el) or _in_navigation(el):
            continue
        text = _clean_text(el.get_text(" ", strip=True))
        if not text or len(text) > 200 or PRICE_PATTERN.search(text):
            continue
        name = _container_item_name(el)
        if not _looks_like_item_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        records.append(
            _build_html_item_record(
                name=name, description="", price="", category="",
                extraction_method="html_listed_item",
            )
        )
    return records


def _extract_menu_items_from_soup(
    soup: BeautifulSoup,
    extraction_method: str = "html_visible_text",
) -> list[MenuItemRecord]:
    listed = _listed_items_from_soup(soup)
    _remove_non_content_tags(soup)
    records = []
    seen: set = set()

    # Each line-source starts a fresh category context but shares the dedupe set so a
    # dish seen in visible text isn't re-emitted from the price-block pass.
    for lines in [_visible_lines_from_soup(soup), _price_text_blocks_from_soup(soup)]:
        records.extend(_records_from_price_lines(
            lines, extraction_method=extraction_method, allow_bare_prices=False, seen=seen,
        ))

    # Add price-less listed items whose name wasn't already captured with a price.
    priced_names = {record.item_name.lower() for record in records}
    for record in listed:
        if record.item_name.lower() not in priced_names:
            records.append(record)
    return records


def _extract_schema_org_menu_items_from_html(html: str) -> list[MenuItemRecord]:
    return _extract_schema_org_menu_items_from_soup(make_soup(html))


def _extract_schema_org_menu_items_from_soup(soup: BeautifulSoup) -> list[MenuItemRecord]:
    records = []
    seen = set()
    for item in _json_ld_items_from_soup(soup):
        if _schema_type_matches(item, "menu"):
            records.extend(
                _schema_menu_section_records(
                    item.get("hasMenuSection"),
                    source_url=_schema_text(item.get("@id")),
                    seen=seen,
                )
            )
        elif _schema_type_matches(item, "menuitem"):
            record = _schema_menu_item_record(
                item,
                category="",
                source_url=_schema_text(item.get("@id")),
            )
            if record:
                key = _schema_record_key(record)
                if key not in seen:
                    seen.add(key)
                    records.append(record)

    # Also read Schema.org expressed as HTML microdata (itemprop attributes),
    # not just JSON-LD. Many CMS/theme menus use microdata instead.
    for record in _microdata_menu_items_from_soup(soup):
        key = _schema_record_key(record)
        if key not in seen:
            seen.add(key)
            records.append(record)
    return records


def _microdata_menu_items_from_soup(soup: BeautifulSoup) -> list[MenuItemRecord]:
    records = []
    for node in soup.find_all(attrs={"itemtype": re.compile(r"menuitem", re.I)}):
        name = _microdata_value(node, "name")
        if not name or not _looks_like_item_name(name):
            continue
        record = _build_html_item_record(
            name=name,
            description=_microdata_value(node, "description"),
            price=_microdata_price(node),
            category=_microdata_section_name(node),
            extraction_method="schema_org_microdata",
        )
        records.append(record)
    return records


def _microdata_value(node: object, prop: str) -> str:
    target = node.find(attrs={"itemprop": prop})
    if not target:
        return ""
    content = target.get("content")
    if content and content.strip():
        return _clean_text(content)
    return _clean_text(target.get_text(" ", strip=True))


def _microdata_price(node: object) -> str:
    for prop in ("price", "lowPrice"):
        value = _microdata_value(node, prop)
        if value:
            return value if any(c in value for c in "$€£¥₹฿") else _format_schema_price(value)
    offers = node.find(attrs={"itemprop": "offers"})
    if offers:
        return _microdata_value(offers, "price")
    return ""


def _microdata_section_name(node: object) -> str:
    parent = node.parent
    for _ in range(6):
        if parent is None or not getattr(parent, "get", None):
            break
        itemtype = parent.get("itemtype") or ""
        if "menusection" in str(itemtype).lower():
            return _microdata_value(parent, "name")[:80]
        parent = parent.parent
    return ""


def _build_html_item_record(
    *, name: str, description: str, price: str, category: str, extraction_method: str
) -> MenuItemRecord:
    raw_text = f"{name} {description} {price}".strip()
    dietary_terms, allergen_terms = _dietary_and_allergen_terms(raw_text)
    return MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="",
        category=category, item_name=name.strip(), description=description.strip(),
        price=price.strip(), dietary_terms=dietary_terms, allergen_terms=allergen_terms,
        source_type="", extraction_method=extraction_method,
        confidence=_item_confidence(
            category=category, description=description,
            dietary_terms=dietary_terms, allergen_terms=allergen_terms,
        ),
        raw_text=raw_text, fetched_at="",
    )


def _schema_menu_section_records(
    section_payload: object,
    *,
    source_url: str,
    seen: set[tuple[str, str, str]],
    category: str = "",
) -> list[MenuItemRecord]:
    records = []
    if isinstance(section_payload, list):
        for section in section_payload:
            records.extend(
                _schema_menu_section_records(
                    section,
                    source_url=source_url,
                    seen=seen,
                    category=category,
                )
            )
        return records
    if not isinstance(section_payload, dict):
        return records

    section_category = _schema_text(section_payload.get("name")) or category
    menu_items = section_payload.get("hasMenuItem") or section_payload.get("menuItem")
    if isinstance(menu_items, list):
        item_values = menu_items
    elif menu_items:
        item_values = [menu_items]
    else:
        item_values = []

    for menu_item in item_values:
        if not isinstance(menu_item, dict):
            continue
        record = _schema_menu_item_record(
            menu_item,
            category=section_category,
            source_url=source_url,
        )
        if not record:
            continue
        key = _schema_record_key(record)
        if key in seen:
            continue
        seen.add(key)
        records.append(record)

    child_sections = (
        section_payload.get("hasMenuSection")
        or section_payload.get("menuSection")
        or []
    )
    records.extend(
        _schema_menu_section_records(
            child_sections,
            source_url=source_url,
            seen=seen,
            category=section_category,
        )
    )
    return records


def _schema_menu_item_record(
    item: dict[str, object],
    *,
    category: str,
    source_url: str,
) -> MenuItemRecord | None:
    item_name = _schema_text(item.get("name"))
    if not item_name or not _looks_like_item_name(item_name):
        return None

    description = _schema_text(item.get("description"))
    price = _schema_menu_item_price(item)
    raw_text = _clean_text(
        " ".join(
            value
            for value in [
                category,
                item_name,
                description,
                price,
                _schema_menu_add_on_text(item.get("menuAddOn")),
            ]
            if value
        )
    )
    if not raw_text:
        return None

    dietary_terms, allergen_terms = _dietary_and_allergen_terms(raw_text)
    confidence = 0.9
    if price:
        confidence += 0.05
    if category:
        confidence += 0.03
    if description:
        confidence += 0.02

    return MenuItemRecord(
        restaurant_name="",
        restaurant_source_id="",
        menu_source_url=source_url,
        category=category,
        item_name=item_name,
        description=description,
        price=price,
        dietary_terms=dietary_terms,
        allergen_terms=allergen_terms,
        source_type="",
        extraction_method="schema_org_menu_item",
        confidence=round(min(confidence, 0.99), 2),
        raw_text=raw_text,
        fetched_at="",
    )


def _schema_menu_item_price(item: dict[str, object]) -> str:
    for value in _schema_offer_values(item.get("offers")):
        price = _schema_text(value.get("price"))
        if price:
            return _format_schema_price(price)
        price_specification = value.get("priceSpecification")
        if isinstance(price_specification, dict):
            price = _schema_text(price_specification.get("price"))
            if price:
                return _format_schema_price(price)

    for field_name in ["price", "priceRange"]:
        price = _schema_text(item.get(field_name))
        if price:
            return _format_schema_price(price)

    add_on_price = _schema_price_from_menu_add_on(item.get("menuAddOn"))
    if add_on_price:
        return _format_schema_price(add_on_price)

    return ""


def _schema_offer_values(value: object) -> list[dict[str, object]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _schema_price_from_menu_add_on(value: object) -> str:
    if isinstance(value, list):
        for item in value:
            price = _schema_price_from_menu_add_on(item)
            if price:
                return price
        return ""
    if not isinstance(value, dict):
        return ""

    name = _schema_text(value.get("name"))
    if _looks_like_schema_price(name):
        return name

    menu_items = value.get("hasMenuItem") or value.get("menuItem")
    if isinstance(menu_items, list):
        for item in menu_items:
            price = _schema_price_from_menu_add_on(item)
            if price:
                return price
    elif isinstance(menu_items, dict):
        price = _schema_price_from_menu_add_on(menu_items)
        if price:
            return price

    child_sections = value.get("hasMenuSection") or value.get("menuSection")
    return _schema_price_from_menu_add_on(child_sections)


def _schema_menu_add_on_text(value: object) -> str:
    if isinstance(value, list):
        return " ".join(
            text for text in [_schema_menu_add_on_text(item) for item in value] if text
        )
    if not isinstance(value, dict):
        return ""
    pieces = [_schema_text(value.get("name"))]
    menu_items = value.get("hasMenuItem") or value.get("menuItem")
    if isinstance(menu_items, list):
        pieces.extend(_schema_menu_add_on_text(item) for item in menu_items)
    elif isinstance(menu_items, dict):
        pieces.append(_schema_menu_add_on_text(menu_items))
    child_sections = value.get("hasMenuSection") or value.get("menuSection")
    pieces.append(_schema_menu_add_on_text(child_sections))
    return _clean_text(" ".join(piece for piece in pieces if piece))


def _schema_type_matches(item: dict[str, object], expected_type: str) -> bool:
    schema_type = item.get("@type")
    if isinstance(schema_type, list):
        return any(str(value).lower() == expected_type for value in schema_type)
    return str(schema_type or "").lower() == expected_type


def _schema_text(value: object) -> str:
    if isinstance(value, str):
        if value.strip().lower() in ["none", "null"]:
            return ""
        return _clean_text(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for field_name in ["@value", "name", "text", "@id", "url"]:
            text = _schema_text(value.get(field_name))
            if text:
                return text
    return ""


def _format_schema_price(value: str) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    if "$" in cleaned or "/" in cleaned:
        return cleaned
    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        number = float(cleaned)
        if number.is_integer():
            return str(int(number))
    return cleaned


def _looks_like_schema_price(value: str) -> bool:
    if not value:
        return False
    return bool(
        re.fullmatch(
            r"\$?\s?\d{1,3}(?:\.\d{2})?(?:\s*/\s*\$?\s?\d{1,3}(?:\.\d{2})?)*",
            value.strip(),
        )
    )


def _schema_record_key(record: MenuItemRecord) -> tuple[str, str, str]:
    return (
        _dedupe_text(record.category),
        _dedupe_text(record.item_name),
        _dedupe_price(record.price),
    )


def _records_from_price_lines(
    lines: list[str],
    *,
    extraction_method: str,
    allow_bare_prices: bool,
    seen: set,
) -> list[MenuItemRecord]:
    """Build priced MenuItemRecords from a sequence of text lines, tracking the running
    category and skipping dishes already in `seen`. Shared by the soup and plain-text
    extractors, which differ only in `extraction_method` and `allow_bare_prices`."""
    records: list[MenuItemRecord] = []
    current_category = ""
    for line in lines:
        if _looks_like_category(line):
            current_category = line[:80]
            continue
        for raw_text, before_price, price, after_price in _price_segments(
            line,
            allow_bare_prices=allow_bare_prices,
        ):
            if not before_price or _looks_like_category(before_price):
                continue
            item_name, description = _split_item_name_and_description(before_price)
            if after_price and not description:
                description = after_price
            if not _looks_like_item_name(item_name):
                continue
            key = (item_name.lower(), price.lower(), raw_text.lower())
            if key in seen:
                continue
            seen.add(key)
            dietary_terms, allergen_terms = _dietary_and_allergen_terms(raw_text)
            records.append(
                MenuItemRecord(
                    restaurant_name="",
                    restaurant_source_id="",
                    menu_source_url="",
                    category=current_category,
                    item_name=item_name,
                    description=description,
                    price=price,
                    dietary_terms=dietary_terms,
                    allergen_terms=allergen_terms,
                    source_type="",
                    extraction_method=extraction_method,
                    confidence=_item_confidence(
                        category=current_category,
                        description=description,
                        dietary_terms=dietary_terms,
                        allergen_terms=allergen_terms,
                    ),
                    raw_text=raw_text,
                    fetched_at="",
                )
            )
    return records


def _extract_menu_items_from_text(text: str) -> list[MenuItemRecord]:
    lines = [_clean_text(line) for line in text.splitlines() if _clean_text(line)]
    return _records_from_price_lines(
        lines, extraction_method="text_price_lines", allow_bare_prices=True, seen=set(),
    )


def _price_segments(
    line: str,
    *,
    allow_bare_prices: bool = False,
) -> list[tuple[str, str, str, str]]:
    if _is_negative_item_text(line):
        return []

    matches = _price_matches(line, allow_bare_prices=allow_bare_prices)
    if not matches:
        return []

    segments = []
    previous_end = 0
    for match in matches:
        segment = line[previous_end:match.end()].strip(" -:|")
        previous_end = match.end()
        if len(segment) < 5 or len(segment) > 260:
            continue
        before_price = segment[: segment.rfind(match.group(0))].strip(" -:|")
        # Description after the price, stopping at the next price (any currency).
        after_price = PRICE_PATTERN.split(line[match.end():], maxsplit=1)[0].strip(" -:|")
        segments.append(
            (
                segment[:240],
                before_price,
                match.group(0).strip(),
                after_price[:120],
            )
        )
    return segments


def _price_matches(line: str, *, allow_bare_prices: bool) -> list[re.Match[str]]:
    matches = list(PRICE_PATTERN.finditer(line))
    # Only fall back to bare numbers when the line has no explicit currency price.
    # On a priced line a stray number is almost always a quantity/calorie/size,
    # not a second price — so this kills a major false-positive source.
    if allow_bare_prices and not matches:
        matches.extend(
            match
            for match in BARE_PRICE_PATTERN.finditer(line)
            if _is_plausible_bare_price(line, match)
        )
    return sorted(matches, key=lambda match: match.start())


def _is_plausible_bare_price(line: str, match: re.Match[str]) -> bool:
    normalized_line = line.lower()
    if any(phrase in normalized_line for phrase in NON_MENU_BARE_PRICE_PHRASES):
        return False
    if re.search(r"\b(?:fy|form|section)\s*\d", normalized_line):
        return False

    value_text = match.group(0)
    try:
        value = float(value_text)
    except ValueError:
        return False

    if value < 2 or value > 175:
        return False

    after = line[match.end(): match.end() + 8].lower().lstrip()
    if after and after[0].isalpha():
        return False
    if any(after.startswith(token) for token in NON_PRICE_FOLLOWERS):
        return False
    if after.startswith("-") or after.startswith("‑"):
        return False

    before = line[max(0, match.start() - 8): match.start()].lower().rstrip()
    if any(before.endswith(token) for token in NON_PRICE_PRECEDERS):
        return False

    return True


def _price_count(text: str, *, allow_bare_prices: bool) -> int:
    return sum(
        len(_price_matches(line, allow_bare_prices=allow_bare_prices))
        for line in text.splitlines()
    )


def _dedupe_item_key(
    row: dict[str, str],
    item: MenuItemRecord,
) -> tuple[str, str, str, str]:
    # Intentionally excludes candidate_url: the same dish on /menu, /menu#lunch,
    # /catering, and crawled/sitemap variants is one item, not several. Keying on
    # restaurant + name + price collapses those cross-page duplicates.
    return (
        row.get("restaurant_source_id", ""),
        row.get("restaurant_name", ""),
        _dedupe_text(item.item_name),
        _dedupe_price(item.price),
    )


def _dedupe_text(value: str) -> str:
    normalized = value.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _dedupe_price(value: str) -> str:
    match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
    if not match:
        return ""
    number = float(match.group(0))
    if number.is_integer():
        return str(int(number))
    return str(number)


def build_menu_text_output_paths(label: str, out_dir: Path) -> tuple[Path, Path]:
    json_path, csv_path = timestamped_output_paths(
        label,
        out_dir,
        "menu_text",
        (".json", ".csv"),
    )
    return json_path, csv_path


def write_menu_text_json(path: Path, rows: list[MenuTextRecord]) -> None:
    write_dataclass_json(path, rows)


def _join_terms_transform(record: dict[str, object], row: object) -> None:
    """Serialize the term lists to '; '-joined strings for CSV. Shared by both the
    text and item writers (their MenuTextRecord/MenuItemRecord both expose the fields)."""
    record["dietary_terms"] = "; ".join(row.dietary_terms)
    record["allergen_terms"] = "; ".join(row.allergen_terms)


def write_menu_text_csv(path: Path, rows: list[MenuTextRecord]) -> None:
    write_dataclass_csv(
        path,
        rows,
        fieldnames=MENU_TEXT_CSV_FIELDS,
        transform=_join_terms_transform,
    )


def build_menu_item_output_paths(label: str, out_dir: Path) -> tuple[Path, Path]:
    json_path, csv_path = timestamped_output_paths(
        label,
        out_dir,
        "menu_items",
        (".json", ".csv"),
    )
    return json_path, csv_path


def write_menu_items_json(path: Path, rows: list[MenuItemRecord]) -> None:
    write_dataclass_json(path, rows)


def write_menu_items_csv(path: Path, rows: list[MenuItemRecord]) -> None:
    write_dataclass_csv(
        path,
        rows,
        fieldnames=MENU_ITEM_CSV_FIELDS,
        transform=_join_terms_transform,
    )


def _should_extract_row(
    row: dict[str, str],
    *,
    include_unvalidated: bool,
) -> bool:
    if include_unvalidated:
        return True
    if row.get("validation_status") == "validated":
        return True
    if row.get("source_type") == "ordering_page":
        return True
    return row.get("source_type") == "image" and row.get("is_primary_menu_candidate") == "True"


def _visible_lines_from_soup(soup: BeautifulSoup) -> list[str]:
    text = soup.get_text("\n", strip=True)
    return [
        _clean_text(line)
        for line in text.splitlines()
        if _clean_text(line)
    ]


def _price_text_blocks_from_soup(soup: BeautifulSoup) -> list[str]:
    blocks = []
    for tag in soup.find_all(["li", "p", "h3", "h4", "h5", "div", "span"]):
        text = _clean_text(tag.get_text(" ", strip=True))
        if not text or not PRICE_PATTERN.search(text):
            continue
        if len(text) > 260:
            continue
        blocks.append(text)
    return list(dict.fromkeys(blocks))


def _looks_like_category(line: str) -> bool:
    normalized = line.lower().strip()
    if PRICE_PATTERN.search(normalized):
        return False
    if len(normalized) > 45:
        return False
    normalized_key = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    return normalized_key in _CATEGORY_KEYS


def _split_item_name_and_description(value: str) -> tuple[str, str]:
    for separator in [" - ", " – ", " — ", ": "]:
        if separator in value:
            left, right = value.split(separator, 1)
            return left.strip(), right.strip()
    words = value.split()
    title_prefix_count = _title_prefix_word_count(words)
    if title_prefix_count >= 2 and title_prefix_count < len(words):
        return (
            " ".join(words[:title_prefix_count]).strip(),
            " ".join(words[title_prefix_count:]).strip(),
        )
    if len(words) >= 7:
        return " ".join(words[:4]).strip(), " ".join(words[4:]).strip()
    return value.strip(), ""


def _title_prefix_word_count(words: list[str]) -> int:
    last_title_index = -1
    for index, word in enumerate(words[:8]):
        cleaned = word.strip("()[]{}.,;:!?'\"")
        if _looks_like_title_word(cleaned):
            last_title_index = index
            continue
        if (
            cleaned.lower() in ITEM_NAME_CONNECTORS
            and index + 1 < len(words)
            and _looks_like_title_word(words[index + 1].strip("()[]{}.,;:!?'\""))
        ):
            last_title_index = index
            continue
        break
    return last_title_index + 1


def _looks_like_title_word(value: str) -> bool:
    if not value:
        return False
    if not any(char.isalpha() for char in value):
        return False
    return value[0].isupper() or value.isupper()


_NAME_CONNECTOR_END = (
    " with", " and", " of", " the", " in", " on", " to", " or", " a", " for",
    " de", " la", " du", " &", ",", "-", "+", "/", ":",
)

# Universal non-dish strings (site nav, merch, UI, language switchers). General,
# not per-site. Exact-match set for ambiguous short words; phrase set for clear
# substrings. Keeps price-less list extraction from collecting navigation/merch.
_NON_DISH_EXACT = {
    "about", "about us", "contact", "contact us", "home", "login", "log in",
    "logout", "register", "gallery", "careers", "jobs", "press", "blog", "news",
    "faq", "faqs", "locations", "location", "shop", "store", "stores", "hours",
    "directions", "subscribe", "menu", "menus", "order", "order online", "cart",
    "reservations", "reservation", "events", "gift cards", "gift card", "merch",
    "newsletter", "privacy", "terms", "search", "more", "all", "français",
    "english", "español", "fr en", "en fr", "tote bags", "tote bag", "our story",
    "our team", "the team", "book", "book now", "book a table", "find us",
    # Location-picker / nav text that leaks on chain & JS sites
    "all locations", "all cities", "select filter option", "select location",
    "canada", "united states", "usa", "discover", "open menu", "view menu",
    "delivery", "pickup", "takeout", "catering", "private events", "event venues",
    "getting here", "where to stay", "things to do", "our partners", "eat & drink",
}
_NON_DISH_PHRASES = (
    "follow us", "sign in", "sign up", "add to cart", "view menu", "read more",
    "learn more", "download", "app store", "google play", "all rights",
    "opening hours", "view cart", "checkout", "book a table", "delivery in",
    "order now", "see menu", "our menu", "© ", "www.",
    # Corporate/legal document fragments that leak from non-menu PDFs
    "modern slavery", "slavery act", "pursuant to", "conservation international",
    "this statement is made", "fiscal year", " fy2", "operated by", "sourced by",
)


def _looks_like_item_name(value: str) -> bool:
    if len(value) < 2 or len(value) > 80:
        return False
    if value.lower() in ["subtotal", "total", "delivery", "service fee"]:
        return False
    if _is_negative_item_text(value):
        return False
    if not any(char.isalpha() for char in value):
        return False
    low_stripped = value.lower().strip(" .!*#|")
    if low_stripped in _NON_DISH_EXACT or any(p in value.lower() for p in _NON_DISH_PHRASES):
        return False
    words = value.split()
    if len(words) >= 4 and len(set(w.lower() for w in words)) <= len(words) // 2:
        return False  # repeated-word run-on (nav/section concatenation)
    # General grammatical gates (not per-site): a real menu-item name is a
    # Title/UPPER-case noun phrase, not a sentence fragment or prose run-on.
    first = value.lstrip("([\"'¡¿")[:1]
    if first and first.isalpha() and first.islower():
        return False  # lowercase start = description fragment or bare variant
    low = value.lower().rstrip()
    if low.endswith(_NAME_CONNECTOR_END):
        return False  # trailing connector = cut-off fragment
    if len(value.split()) > 10:
        return False  # sentence-like run-on, not a name
    # Reject prose run-ons (CJK sentence punctuation) and mostly-numeric strings.
    if any(p in value for p in "、。，％〜「」『』…"):
        return False
    digits = sum(ch.isdigit() for ch in value)
    return digits / len(value) <= 0.5


def _is_negative_item_text(value: str) -> bool:
    normalized = value.lower()
    return any(signal in normalized for signal in ITEM_NEGATIVE_SIGNALS)


def _item_confidence(
    *,
    category: str,
    description: str,
    dietary_terms: list[str],
    allergen_terms: list[str],
) -> float:
    score = 0.55
    if category:
        score += 0.15
    if description:
        score += 0.15
    if dietary_terms or allergen_terms:
        score += 0.1
    return round(min(score, 0.95), 2)


# Plain substring matching over-reports when an allergen term is a literal
# substring of an unrelated word: "egg" in "eggplant", "nuts" in
# "doughnuts"/"coconuts", "wheat" in "buckwheat", "crab" in "crabapple". We keep
# substring matching on purpose — for a safety tool, over-reporting an allergen is
# the conservative direction, and substring still catches the dangerous compounds
# a word boundary would silently drop ("eggs", "milkshake", "eggnog",
# "buttermilk", "hazelnuts", German "erdnussbutter"). We only suppress an
# occurrence that sits entirely inside a known false-friend word.
_ALLERGEN_FALSE_FRIENDS = {
    "egg": {"eggplant", "eggplants", "veggie", "veggies"},
    "nuts": {"coconut", "coconuts", "doughnut", "doughnuts", "donut", "donuts"},
    "wheat": {"buckwheat", "buckwheats"},
    "crab": {"crabapple", "crabapples", "crabgrass"},
}


def _matched_terms(text: str, terms: list[str]) -> list[str]:
    return _matched_terms_in(text.lower(), terms)


def _matched_terms_in(normalized_text: str, terms: list[str]) -> list[str]:
    """Match ``terms`` against text that is ALREADY lower-cased -- lets callers
    that match several vocabularies over one source lower-case it just once."""
    return sorted({term for term in terms if _term_present(term, normalized_text)})


def _dietary_and_allergen_terms(text: str) -> tuple[list[str], list[str]]:
    """Dietary + allergen hits are always needed together on the same source
    text; lower-case once and match both, instead of lowering twice."""
    normalized = text.lower()
    return (
        _matched_terms_in(normalized, DIETARY_TERMS),
        _matched_terms_in(normalized, ALLERGEN_TERMS),
    )


def _term_present(term: str, normalized_text: str) -> bool:
    false_friends = _ALLERGEN_FALSE_FRIENDS.get(term)
    if not false_friends:
        return term in normalized_text
    # The term is a substring of at least one unrelated word; count it only if it
    # appears as (part of) a word that is not one of those false friends.
    index = normalized_text.find(term)
    while index != -1:
        if _enclosing_word(normalized_text, index, len(term)) not in false_friends:
            return True
        index = normalized_text.find(term, index + 1)
    return False


def _enclosing_word(text: str, start: int, length: int) -> str:
    begin = start
    while begin > 0 and text[begin - 1].isalpha():
        begin -= 1
    end = start + length
    while end < len(text) and text[end].isalpha():
        end += 1
    return text[begin:end]






