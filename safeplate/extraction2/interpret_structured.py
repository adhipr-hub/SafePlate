from __future__ import annotations

from safeplate.allergen_matrix import items_from_allergen_matrix_soup
from safeplate.embedded_json import extract_items_from_embedded_json
from safeplate.extraction2.schema import Payload
from safeplate.menu_text import (
    MenuItemRecord,
    _extract_schema_org_menu_items_from_soup,
)
from safeplate.soup import make_soup


def interpret_structured(payload: Payload) -> list[MenuItemRecord]:
    """Deterministic extraction from machine-readable schemas ONLY.

    Each parser trusts an explicit structure -- a dish x allergen table, a
    schema.org `Menu`, or an app-embedded JSON blob -- so none of them need the
    prose heuristics (`_looks_like_item_name`, the `_NON_DISH_*` blocklists) that
    made v1 brittle. On a page with no such schema they all return [], and the
    pipeline falls through to the LLM interpreter (or honestly reports no menu).

    Order = richest schema first: allergen matrix (dish->allergens) beats a plain
    schema.org item list beats a raw JSON name/price blob.
    """
    # PDF allergen matrices: parse the dish x allergen table grid from the bytes
    # (the HTML table parser can't read a PDF's text layer). Skip the slow pdfplumber
    # extract_tables() pass on PDFs whose text can't back an allergen grid -- but run
    # it when the text layer is empty (scanned PDF: we can't rule a grid out, and the
    # call is cheap with no text), keeping output identical.
    if payload.source_type == "pdf" and payload.content:
        from safeplate.allergen_matrix import (
            _pdf_text_could_have_allergen_grid,
            extract_items_from_allergen_pdf,
        )

        text = payload.text or ""
        if (not text.strip()) or _pdf_text_could_have_allergen_grid(text):
            pdf_items = extract_items_from_allergen_pdf(payload.content)
            if pdf_items:
                return pdf_items

    html = payload.text or ""
    if not html.strip():
        return []
    # Parse the HTML into a soup ONCE and reuse it across all structured passes below.
    # Each pass is read-only (none mutate the tree), so a shared soup is output-identical
    # while removing the ~3x redundant lxml parse this function used to pay per page.
    soup = make_soup(html)
    # Tier 1: structured allergen data embedded in hydration JSON (price-optional).
    # Highest value for a safety app, and a no-op on pages without such data.
    from safeplate.extraction2.embedded_allergens import (
        extract_allergen_items_from_embedded_json,
    )

    items = extract_allergen_items_from_embedded_json(html, soup=soup)
    if not items:
        items = items_from_allergen_matrix_soup(soup)
    if not items:
        items = _extract_schema_org_menu_items_from_soup(soup)
    if not items:
        items = extract_items_from_embedded_json(html, soup=soup)
    return items
