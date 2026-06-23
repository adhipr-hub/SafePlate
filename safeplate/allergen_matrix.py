"""Parse restaurant allergen/nutrition *matrix* tables into per-dish allergen data.

Most chains — and many independents, including the ones whose ordering menus hide
prices behind a location picker — publish a dish x allergen grid (an "allergen
matrix"): rows are dishes, columns are allergens (Peanut, Milk, Egg, Soy,
Gluten, Fish, Sesame, ...), and a cell is marked when the dish contains that
allergen. This is *more* valuable than a priced menu for SafePlate, because it
maps dish -> allergen directly and authoritatively instead of inferring it.

This module reads that grid out of HTML ``<table>`` markup (the dominant, most
reliable form) and emits one :class:`MenuItemRecord` per dish with the marked
allergens already populated. Prices are intentionally absent — matrices don't
carry them, and price is secondary to the allergen mapping.

It is deliberately conservative about what counts as a matrix (>= 3 recognized
allergen columns and >= 2 dish rows) so it never mistakes a nutrition or layout
table for an allergen grid.
"""

from __future__ import annotations

from typing import Any

from safeplate.menu_text import (
    MenuItemRecord,
    _classlist_text,
    _clean_text,
    _looks_like_item_name,
)
from safeplate.soup import make_soup


# Header text -> canonical allergen token. Substring match on the lowercased
# header. Order matters: more specific entries first so "peanut" wins over the
# generic "nut" column and "shellfish" wins over "fish".
_ALLERGEN_COLUMN_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("peanut", "groundnut"), "peanut"),
    (("tree nut", "treenut", "tree-nut"), "tree nut"),
    (("crustacean", "shellfish"), "shellfish"),
    (("mollusc", "mollusk"), "mollusc"),
    (("fish",), "fish"),
    (("milk", "dairy", "lactose"), "milk"),
    (("egg",), "egg"),
    (("soya", "soybean", "soy"), "soy"),
    (("gluten",), "gluten"),
    (("wheat",), "wheat"),
    (("sesame",), "sesame"),
    (("mustard",), "mustard"),
    (("celery",), "celery"),
    (("sulphite", "sulfite", "sulphur", "sulfur"), "sulphites"),
    (("lupin", "lupine"), "lupin"),
    # Generic "nut(s)" column, checked last so the specific nut types win.
    (("nut",), "tree nut"),
]

_POSITIVE_SYMBOLS = "✓✔✅☑●•◆■▪♦"
_POSITIVE_WORDS = {"x", "yes", "y", "contains", "1", "true"}
_NEGATIVE_WORDS = {
    "", "-", "–", "—", "0", "no", "n", "none", "free", "n/a", "na",
    "✗", "✘", "✕", "×", "○", "◦",
}

_MAX_TABLES = 40


def extract_items_from_allergen_matrix(html: str) -> list[MenuItemRecord]:
    """Parse dish x allergen matrix tables out of an HTML page."""
    return items_from_allergen_matrix_soup(make_soup(html))


def items_from_allergen_matrix_soup(soup: Any) -> list[MenuItemRecord]:
    records: list[MenuItemRecord] = []
    seen: set[str] = set()
    for table in soup.find_all("table")[:_MAX_TABLES]:
        records.extend(_records_from_table(table, seen))
    return records


# Allergen matrices are at most a few pages; cap so a giant nutrition PDF can't make
# pdfplumber's slow table-detection stall a worker (see Yard House).
_MATRIX_PDF_MAX_PAGES = 25


_ALLERGEN_GRID_KEYWORDS = ("allergen", "allergy", "allergies", "nutrition", "intoleran")


def _pdf_text_could_have_allergen_grid(text: str) -> bool:
    """True iff the extracted PDF text could plausibly back a pdfplumber allergen grid.

    Safe SUPERSET of what ``extract_items_from_allergen_pdf`` can emit: that parser
    requires >=3 distinct recognized allergen column headers (see
    ``_records_from_text_grid``), and those header words are part of the text layer, so
    any PDF it could grid contains >=3 distinct allergen alias terms here. We also pass
    on an explicit allergen/nutrition keyword. Plain menu/policy PDFs match neither, so
    we skip the expensive ``extract_tables()`` pass on them with identical output."""
    low = (text or "").lower()
    if not low.strip():
        return False
    if any(keyword in low for keyword in _ALLERGEN_GRID_KEYWORDS):
        return True
    distinct = {
        token
        for aliases, token in _ALLERGEN_COLUMN_ALIASES
        if any(alias in low for alias in aliases)
    }
    return len(distinct) >= 3


def extract_items_from_allergen_pdf(pdf_bytes: bytes) -> list[MenuItemRecord]:
    """Parse dish x allergen matrix tables out of a (text-based) PDF.

    Uses pdfplumber to recover table structure, then reuses the same grid
    interpretation as the HTML path. Returns [] for image/scanned PDFs or PDFs
    whose allergen columns are rotated/icon headers pdfplumber can't read — those
    are handled by the Gemini-vision fallback instead.
    """
    try:
        import pdfplumber
    except ImportError:
        return []
    import logging
    from io import BytesIO

    # pdfplumber (pdfminer) floods stderr with per-glyph "Cannot set non-stroke
    # color" warnings on some PDFs and parses table structure slowly; cap the page
    # count so a huge nutrition PDF can't stall a worker, and quiet the noise.
    logging.getLogger("pdfminer").setLevel(logging.ERROR)

    records: list[MenuItemRecord] = []
    seen: set[str] = set()
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:_MATRIX_PDF_MAX_PAGES]:
                for table in page.extract_tables() or []:
                    records.extend(_records_from_text_grid(table, seen))
    except Exception:
        return records
    return records


def _records_from_text_grid(
    grid: list[list[Any]], seen: set[str]
) -> list[MenuItemRecord]:
    rows = [[_clean_text(str(c)) if c else "" for c in row] for row in grid if row]
    if len(rows) < 2:
        return []
    header = rows[0]
    allergen_cols = {
        idx: allergen
        for idx, cell in enumerate(header)
        if (allergen := _header_allergen(cell))
    }
    if len(set(allergen_cols.values())) < 3:
        return []
    name_col = next(
        (idx for idx in range(len(header)) if idx not in allergen_cols), 0
    )
    needed = max([name_col, *allergen_cols.keys()])

    records: list[MenuItemRecord] = []
    for row in rows[1:]:
        if len(row) <= needed:
            continue
        name = row[name_col].strip()
        if not _looks_like_item_name(name) or name.lower() in seen:
            continue
        present = sorted(
            {
                allergen
                for col, allergen in allergen_cols.items()
                if _cell_text_is_marked(row[col])
            }
        )
        seen.add(name.lower())
        records.append(_matrix_item_record(name, present, ""))
    return records


def _cell_text_is_marked(text: str) -> bool:
    norm = (text or "").strip().lower().strip(" .•")
    if norm in _NEGATIVE_WORDS:
        return False
    if norm in _POSITIVE_WORDS:
        return True
    if any(symbol in (text or "") for symbol in _POSITIVE_SYMBOLS):
        return True
    if "contain" in norm and not any(neg in norm for neg in ("not", "no ", "free")):
        return True
    return False


def looks_like_allergen_matrix(soup: Any) -> bool:
    """True if the page contains at least one parseable dish x allergen grid.

    Used during menu-source validation: matrix pages often have few prices and
    few menu-item words, so the normal "looks like a priced menu" check would
    drop them despite being the most valuable allergen source we can find.
    """
    seen: set[str] = set()
    for table in soup.find_all("table")[:_MAX_TABLES]:
        if len(_records_from_table(table, seen)) >= 2:
            return True
    return False


def _records_from_table(table: Any, seen: set[str]) -> list[MenuItemRecord]:
    parsed = _parse_matrix_header(table)
    if parsed is None:
        return []
    name_col, allergen_cols, category = parsed

    records: list[MenuItemRecord] = []
    needed = max([name_col, *allergen_cols.keys()])
    for row in _data_rows(table):
        cells = row.find_all(["td", "th"])
        if len(cells) <= needed:
            continue
        name = _clean_text(cells[name_col].get_text(" ", strip=True))
        if not _looks_like_item_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        present = sorted(
            {
                allergen
                for col, allergen in allergen_cols.items()
                if _cell_is_marked(cells[col])
            }
        )
        seen.add(key)
        records.append(_matrix_item_record(name, present, category))
    return records


def _parse_matrix_header(
    table: Any,
) -> tuple[int, dict[int, str], str] | None:
    header_cells = _header_cells(table)
    if not header_cells:
        return None

    allergen_cols: dict[int, str] = {}
    for idx, cell in enumerate(header_cells):
        allergen = _header_allergen(cell.get_text(" ", strip=True))
        if allergen:
            allergen_cols[idx] = allergen

    # A real matrix needs several distinct allergen columns; this is what keeps
    # ordinary nutrition tables (Calories/Fat/Sodium) and layout tables out.
    if len(set(allergen_cols.values())) < 3:
        return None

    name_col = next(
        (idx for idx in range(len(header_cells)) if idx not in allergen_cols),
        0,
    )
    return name_col, allergen_cols, _table_caption(table)


def _header_cells(table: Any) -> list[Any]:
    thead = table.find("thead")
    if thead is not None:
        header_row = thead.find("tr")
        if header_row is not None:
            cells = header_row.find_all(["th", "td"])
            if cells:
                return cells
    first_row = table.find("tr")
    if first_row is None:
        return []
    return first_row.find_all(["th", "td"])


def _data_rows(table: Any) -> list[Any]:
    tbody = table.find("tbody")
    if tbody is not None:
        return tbody.find_all("tr")
    rows = table.find_all("tr")
    # No <tbody>: the first row is the header, so skip it.
    return rows[1:]


def _header_allergen(header_text: str) -> str | None:
    text = header_text.strip().lower()
    if not text:
        return None
    # "Coconut" contains "nut" but is not treated as a tree nut here (matches the
    # rest of the pipeline, which deliberately excludes coconut collisions).
    if "coconut" in text and not any(
        token in text for token in ("tree nut", "peanut", "walnut", "hazelnut")
    ):
        return None
    for aliases, canonical in _ALLERGEN_COLUMN_ALIASES:
        if any(alias in text for alias in aliases):
            return canonical
    return None


def _cell_is_marked(cell: Any) -> bool:
    text = cell.get_text(" ", strip=True).lower()
    norm = text.strip(" .•")
    if norm in _NEGATIVE_WORDS:
        return False
    if norm in _POSITIVE_WORDS:
        return True
    if any(symbol in text for symbol in _POSITIVE_SYMBOLS):
        return True
    if "contain" in norm and not any(neg in norm for neg in ("not", "no ", "free")):
        return True
    for img in cell.find_all("img"):
        alt = (img.get("alt") or "").lower()
        if any(token in alt for token in ("yes", "contain", "tick", "check", "present")):
            return True
    cls = _classlist_text(cell.get("class")).lower()
    if any(token in cls for token in ("contains", "present", "allergen-yes")):
        return True
    return False


def _table_caption(table: Any) -> str:
    caption = table.find("caption")
    if caption is not None:
        text = _clean_text(caption.get_text(" ", strip=True))
        if text:
            return text[:80]
    return ""


def _matrix_item_record(
    name: str, allergens: list[str], category: str
) -> MenuItemRecord:
    raw_text = (
        f"{name} contains {', '.join(allergens)}" if allergens else name
    )
    return MenuItemRecord(
        restaurant_name="",
        restaurant_source_id="",
        menu_source_url="",
        category=category,
        item_name=name,
        description="",
        price="",
        dietary_terms=[],
        allergen_terms=allergens,
        source_type="",
        extraction_method="allergen_matrix",
        # Matrices are authoritative dish->allergen statements, not inferred.
        confidence=0.9,
        raw_text=raw_text,
        fetched_at="",
    )
