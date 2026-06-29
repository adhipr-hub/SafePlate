from __future__ import annotations

import re

from safeplate.extraction2.schema import Payload
from safeplate.menu_text import MenuItemRecord
from safeplate.textutil import norm_ws, strip_ws

# Below this length, a whitespace-stripped substring match is too collision-prone to
# trust on its own ("rice" inside "price", "ice" inside "service"), so a short name
# must match on a word boundary. Longer names keep the letter-spacing-proof fallback.
_MIN_STRIPPED_GROUND_LEN = 8


# Grounding keys (shared via textutil): _normalize strips ALL whitespace so grounding
# survives PDF letter-spacing / quote reflow ("f a c i l i t y"); _collapse keeps word
# boundaries (single-space) for the boundary-anchored primary match.
_normalize = strip_ws
_collapse = norm_ws


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


# Trailing size / variant decoration the text interpreter APPENDS to a base dish
# name ("Cicero's Special (Small)", "Margherita - Large"), which is therefore not
# verbatim in the source. Stripped so the base name can ground directly.
_SIZE_SUFFIX_RE = re.compile(
    r"(?:\s*\([^)]*\)|\s*[-–—,]?\s*"
    r"(?:small|medium|large|regular|reg|kids?|mini|x-?large|x-?l|sm|md|lg|"
    r"\d+\s*(?:oz|inch|in|cm|\")))\s*$",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _strip_size_suffix(name: str) -> str:
    """Remove trailing size/variant decoration so a composed label like
    "Cicero's Special (Small)" reduces to the base name that IS in the source.
    Applied repeatedly to peel e.g. "... (Small) - Large"."""
    prev = None
    out = name.strip()
    while out and out != prev:
        prev = out
        out = _SIZE_SUFFIX_RE.sub("", out).strip()
    return out


def _quote_supports_name(name: str, quote: str) -> bool:
    """Does the evidence ``quote`` actually correspond to this ``name``? We only let
    a verbatim quote vouch for an item when the name's own significant tokens (>=4
    chars, size decoration removed) appear in the quote -- otherwise a FABRICATED
    name could ride a generic quote that happens to be in the source (e.g. a column
    header 'Small Medium Large'). Requires a majority of the name's significant
    tokens, so a real dish whose quote restates the name survives."""
    tokens = [t for t in _TOKEN_RE.findall(_strip_size_suffix(name).lower()) if len(t) >= 4]
    if not tokens:
        return False
    low_quote = quote.lower()
    hits = sum(1 for t in tokens if t in low_quote)
    return hits >= (len(tokens) + 1) // 2


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
    out of a schema); an LLM item is grounded when EITHER its name OR its
    verbatim ``evidence_quote`` (stored as ``raw_text``) appears in the source --
    which is what stops a model from inventing dishes.

    How an item grounds (in order): (1) its name verbatim; (2) its name with
    trailing size/variant decoration stripped -- the text interpreter emits one row
    per size and COMPOSES labels like "Cicero's Special (Small)" that aren't
    verbatim in the source (the PDF has "Cicero's Special" with "Small Medium Large"
    as a separate column header), so grounding the base name recovers every size
    variant WITHOUT trusting a quote; (3) as a last resort, the verbatim
    ``evidence_quote`` (stored as ``raw_text``) -- but ONLY when that quote actually
    names this dish (its significant name tokens appear in the quote). Gating (3)
    stops a FABRICATED name from riding a generic quote that happens to be in the
    source (e.g. the header 'Small Medium Large'); an invented dish whose quote is
    also invented is absent from the source and still dropped.
    """
    kept: list[MenuItemRecord] = []
    dropped: list[tuple[MenuItemRecord, str]] = []
    source_norm = _normalize(payload.text or "")
    source_collapsed = _collapse(payload.text or "")
    for item in items:
        if not require_grounding or not source_norm:
            kept.append(item)
            continue
        grounded = _is_grounded(
            item.item_name, source_norm=source_norm, source_collapsed=source_collapsed
        )
        if not grounded:
            base = _strip_size_suffix(item.item_name)
            if base and base != item.item_name:
                grounded = _is_grounded(
                    base, source_norm=source_norm, source_collapsed=source_collapsed
                )
        if (
            not grounded
            and item.raw_text
            and _quote_supports_name(item.item_name, item.raw_text)
        ):
            grounded = _is_grounded(
                item.raw_text, source_norm=source_norm, source_collapsed=source_collapsed
            )
        if grounded:
            kept.append(item)
        else:
            dropped.append(
                (item, "neither item name nor evidence quote traceable to source text")
            )
    return kept, dropped


def mean_confidence(items: list[MenuItemRecord]) -> float:
    if not items:
        return 0.0
    return round(sum(i.confidence for i in items) / len(items), 3)
