from __future__ import annotations

from safeplate.allergen_matrix import looks_like_allergen_matrix
from safeplate.extraction2.schema import PayloadKind
from safeplate.soup import make_soup

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff")


def classify_html(html: str) -> PayloadKind:
    """STRUCTURED iff the HTML carries a machine-readable menu schema we can
    trust; otherwise TEXT (prose for the LLM). Deliberately NO prose heuristics
    here -- the whole point of v2 is to stop guessing whether free text "looks
    like" a menu. This only detects the presence of an explicit schema.
    """
    if not html or not html.strip():
        return PayloadKind.EMPTY
    low = html.lower()
    # schema.org JSON-LD Menu / MenuItem
    if "schema.org" in low and ("menuitem" in low or '"menu"' in low):
        return PayloadKind.STRUCTURED
    # Embedded application JSON (Toast / Square / Next.js menu blobs)
    if "__next_data__" in low or 'type="application/json"' in low:
        return PayloadKind.STRUCTURED
    # HTML dish x allergen matrix table (conservatively gated inside the parser)
    try:
        if looks_like_allergen_matrix(make_soup(html)):
            return PayloadKind.STRUCTURED
    except Exception:
        pass
    return PayloadKind.TEXT
