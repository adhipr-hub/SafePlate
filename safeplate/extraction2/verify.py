from __future__ import annotations

from safeplate.extraction2.schema import Payload
from safeplate.menu_text import MenuItemRecord


def _normalize(text: str) -> str:
    """Collapse whitespace and case so grounding survives PDF letter-spacing and
    quote reflow (the same defeat-"f a c i l i t y" trick v1's guardrail uses)."""
    return "".join(ch for ch in text.lower() if not ch.isspace())


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
    for item in items:
        if not require_grounding or not source_norm:
            kept.append(item)
            continue
        if _normalize(item.item_name) in source_norm:
            kept.append(item)
        else:
            dropped.append((item, "item name not traceable to source text"))
    return kept, dropped


def mean_confidence(items: list[MenuItemRecord]) -> float:
    if not items:
        return 0.0
    return round(sum(i.confidence for i in items) / len(items), 3)
