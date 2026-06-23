from __future__ import annotations

from dataclasses import replace

from safeplate.extraction2 import interpret_llm
from safeplate.extraction2.interpret_structured import interpret_structured
from safeplate.extraction2.schema import (
    CoverageReport,
    MenuExtractionResult,
    Payload,
    PayloadKind,
    Policy,
)
from safeplate.extraction2.verify import mean_confidence, verify
from safeplate.menu_text import MenuItemRecord


def extract_menu(
    payloads: list[Payload],
    *,
    policy: Policy = Policy.HYBRID,
    llm_enabled: bool = False,
    gemini_api_key: str | None = None,
    gemini_model: str | None = None,
) -> MenuExtractionResult:
    """Interpret a restaurant's acquired menu payloads into grounded item records.

    Validation is by extraction: a source is "valid" iff it yields >=1 grounded
    item (no separate rule-based page-scoring gate -- that v1 gate is what let a
    Modern Slavery Act PDF through). Items are de-duplicated ACROSS the
    restaurant's sources so the same dish on two pages is reported once, keeping
    the most authoritative record.
    """
    items_all: list[MenuItemRecord] = []
    coverage: list[CoverageReport] = []
    llm_calls = 0
    incomplete = False

    for payload in payloads:
        items, interpreter, reason, llm_used, payload_incomplete = _interpret_one(
            payload,
            policy=policy,
            llm_enabled=llm_enabled,
            api_key=gemini_api_key,
            model=gemini_model,
        )
        llm_calls += llm_used
        incomplete = incomplete or payload_incomplete
        # Stamp each item with its source URL (the matrix/vision paths leave it
        # blank) so the scorer's provenance weighting can judge official-vs-off-site.
        items = [
            it if it.menu_source_url else replace(it, menu_source_url=payload.url)
            for it in items
        ]
        coverage.append(
            CoverageReport(
                url=payload.url,
                found=bool(items),
                payload_kind=payload.kind.value,
                item_count=len(items),
                interpreter=interpreter if items else "none",
                confidence=mean_confidence(items),
                reason=(f"{len(items)} items via {interpreter}" if items else reason),
            )
        )
        items_all.extend(items)

    return MenuExtractionResult(
        items=_dedupe_across_sources(items_all),
        coverage=coverage,
        llm_calls=llm_calls,
        incomplete=incomplete,
    )


def _interpret_one(
    payload: Payload,
    *,
    policy: Policy,
    llm_enabled: bool,
    api_key: str | None,
    model: str | None,
) -> tuple[list[MenuItemRecord], str, str, int, bool]:
    """Returns (verified_items, interpreter_name, reason_if_empty, llm_calls_used,
    incomplete). ``incomplete`` is True only when an LLM text chunk failed, leaving a
    partial menu the caller must not cache as complete.

    Verification happens here, per provenance: structured items came from a parsed
    schema and are trusted as-is; LLM items must be grounded in the source text.
    """
    if payload.kind == PayloadKind.VISUAL:
        if not llm_enabled:
            return [], "none", "visual source needs LLM vision (disabled)", 0, False
        try:
            items = interpret_llm.interpret_visual(payload, api_key=api_key, model=model)
        except interpret_llm.LLMNotEnabled as exc:
            return [], "none", str(exc), 0, False
        return items, "llm_visual", "", 1, False

    structured = interpret_structured(payload)

    # Allergen-matrix PDFs whose grid pdfplumber can't read (rotated/icon headers,
    # the norm for big chains): recover the dish x allergen grid with Gemini vision.
    # High value for a safety app, so it runs before the plain text interpreter.
    if (
        not structured
        and payload.source_type == "pdf"
        and llm_enabled
        and _looks_allergen(payload.text)
    ):
        try:
            matrix = interpret_llm.interpret_pdf_matrix(payload, api_key=api_key, model=model)
        except interpret_llm.LLMNotEnabled:
            matrix = []
        if matrix:
            return matrix, "gemini_pdf_matrix", "", 1, False

    def run_llm() -> tuple[bool, list[MenuItemRecord], bool, int]:
        if not llm_enabled:
            return False, [], False, 0
        try:
            items, incomplete, calls = interpret_llm.interpret_text(
                payload, api_key=api_key, model=model
            )
        except interpret_llm.LLMNotEnabled:
            return False, [], False, 0
        kept, _dropped = verify(items, payload, require_grounding=True)
        return True, kept, incomplete, calls

    if policy == Policy.HYBRID:
        if structured:
            return structured, "structured", "", 0, False
        ran, llm_items, incomplete, calls = run_llm()
        if not ran:
            return [], "none", "no machine-readable schema; LLM text disabled", 0, False
        return (
            llm_items,
            "llm_text",
            ("" if llm_items else "no schema; LLM found nothing"),
            calls,
            incomplete,
        )

    if policy == Policy.LLM_FIRST:
        ran, llm_items, incomplete, calls = run_llm()
        if llm_items:
            return llm_items, "llm_text", "", calls, incomplete
        if structured:
            # Fell back to the complete schema items; the partial LLM read is discarded.
            return structured, "structured", "LLM empty; used structured", calls, False
        return [], "none", "no items from LLM or schema", calls, incomplete

    # MERGE: union structured + grounded LLM -> a dish found by EITHER is kept.
    ran, llm_items, incomplete, calls = run_llm()
    merged = _union(structured, llm_items)
    used = calls
    if not merged:
        return [], "none", "no items from LLM or schema", used, incomplete
    label = "merge" if (structured and llm_items) else ("structured" if structured else "llm_text")
    return merged, label, "", used, incomplete


def _looks_allergen(text: str) -> bool:
    low = (text or "").lower()
    return "allergen" in low or "allergy" in low


def _norm(name: str) -> str:
    return " ".join((name or "").split()).lower()


def _rank(item: MenuItemRecord) -> tuple[int, float, int]:
    """Prefer schema-parsed items over LLM reads, then higher confidence, then
    items that carry a price (more complete)."""
    is_structured = 0 if item.extraction_method.startswith("gemini") else 1
    has_price = 1 if (item.price or "").strip() else 0
    return (is_structured, item.confidence, has_price)


def _union(primary: list[MenuItemRecord], secondary: list[MenuItemRecord]) -> list[MenuItemRecord]:
    seen = {_norm(i.item_name) for i in primary}
    out = list(primary)
    for item in secondary:
        key = _norm(item.item_name)
        if key and key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _dedupe_across_sources(items: list[MenuItemRecord]) -> list[MenuItemRecord]:
    best: dict[str, MenuItemRecord] = {}
    for item in items:
        key = _norm(item.item_name)
        if not key:
            continue
        current = best.get(key)
        if current is None or _rank(item) > _rank(current):
            best[key] = item
    return list(best.values())
