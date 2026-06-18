"""Low-level text helpers shared across the pipeline.

Single source of truth for slug/clean/class-list helpers (previously duplicated
in several modules) and the multi-currency price pattern (so menu discovery's
validation gate and menu extraction agree on what a price looks like). This
module depends on nothing else in ``safeplate`` so any module can import it
without risking an import cycle.
"""

from __future__ import annotations

import re

# --- Multi-currency price detection (shared by discovery validation + extraction) ---
_CUR_SYM = r"[$€£¥₹฿₩₫₪₴₦]"
_CUR_CODE = (
    r"(?:usd|eur|gbp|jpy|inr|thb|krw|cny|rmb|sgd|hkd|aud|cad|chf|brl|mxn|php|idr|vnd|rs\.?|r\$)"
)
_AMT = r"\d[\d.,  ]*\d|\d"  # 350 / 12,50 / 1,234.56 / 1.234,56 / 1 200
PRICE_PATTERN = re.compile(
    rf"(?:{_CUR_SYM}\s?(?:{_AMT}))"                       # €12,50  ¥1,200  ₹350
    rf"|(?:(?:{_AMT})\s?{_CUR_SYM})"                      # 12,50€  120฿
    rf"|(?:{_CUR_CODE}\s?(?:{_AMT}))"                     # Rs 350  USD 12
    rf"|(?:(?:{_AMT})\s?(?:円|元|圆|บาท|/-|{_CUR_CODE}))"  # 1200円  350/-  12 EUR
    rf"|\b\d{{1,3}}(?:,\d{{3}})*\.\d{{2}}\b",             # 1,234.56 plain decimal
    flags=re.IGNORECASE,
)


def clean_text(text: str) -> str:
    """Collapse whitespace and trim."""
    return re.sub(r"\s+", " ", text).strip()


def classlist_text(value: object) -> str:
    """Join a BeautifulSoup class attribute (list/tuple or str) into one string."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value or "")


def slugify(value: str, default: str = "item") -> str:
    """Filesystem/id-safe slug from arbitrary text."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    return cleaned.strip("_") or default
