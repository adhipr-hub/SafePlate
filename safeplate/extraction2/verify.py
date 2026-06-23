from __future__ import annotations

import re

from safeplate.extraction2.schema import Payload
from safeplate.menu_text import MenuItemRecord

# Below this length, a whitespace-stripped substring match is too collision-prone to
# trust on its own ("rice" inside "price", "ice" inside "service"), so a short name
# must match on a word boundary. Longer names keep the letter-spacing-proof fallback.
_MIN_STRIPPED_GROUND_LEN = 8


def _normalize(text: str) -> str:
    """Collapse whitespace and case so grounding survives PDF letter-spacing and
    quote reflow (the same defeat-"f a c i l i t y" trick v1's guardrail uses)."""
    return "".join(ch for ch in text.lower() if not ch.isspace())


def _collapse(text: str) -> str:
    """Whitespace-collapsed (not stripped) lowercase, so word boundaries survive."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def _is_grounded(name: str, *, source_norm: str, source_collapsed: str) -> bool:
    """Is ``name`` traceable to the source? Primary check is a word-boundary match in
    the readable text; the whitespace-stripped fallback (for letter-spaced PDFs) is
    allowed only for names long enough to make a coincidental collision implausible."""
    name_collapsed = _collapse(name)
    if not name_collapsed:
        return False
    if re.search(rf"(?<!\w){re.escape(name_collapsed)}(?!\w)", source_collapsed):
        return True
    name_norm = _normalize(name)
    return len(name_norm) >= _MIN_STRIPPED_GROUND_LEN and name_norm in source_norm


def verify(
    items: list[MenuItemRecord],
    payload: Payload,
    *,
    require_grounding: bool,
) -> tuple[list[MenuItemRecord], list[tuple[MenuItemRecord, str]]]:
    """Precision guardrail -- the GOOD kind of rule.

    It checks a FIXED contract (every emitted item must be traceable to the
    source) rather than trying to enumerate every shape of junk, so it never
    grows per-site. Structured items skip grounding (they were parsed straight
    out of a schema); LLM items must have their name appear in the source text,
    which is what stops a model from inventing dishes.
    """
    kept: list[MenuItemRecord] = []
    dropped: list[tuple[MenuItemRecord, str]] = []
    source_norm = _normalize(payload.text or "")
    source_collapsed = _collapse(payload.text or "")
    for item in items:
        if not require_grounding or not source_norm:
            kept.append(item)
            continue
        if _is_grounded(
            item.item_name, source_norm=source_norm, source_collapsed=source_collapsed
        ):
            kept.append(item)
        else:
            dropped.append((item, "item name not traceable to source text"))
    return kept, dropped


def mean_confidence(items: list[MenuItemRecord]) -> float:
    if not items:
        return 0.0
    return round(sum(i.confidence for i in items) / len(items), 3)
