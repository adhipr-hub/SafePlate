from __future__ import annotations

import re
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
from safeplate.textutil import norm_ws


def extract_menu(
    payloads: list[Payload],
    *,
    policy: Policy = Policy.HYBRID,
    llm_enabled: bool = False,
    gemini_api_key: str | None = None,
    gemini_model: str | None = None,
    use_cache: bool = True,
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
            use_cache=use_cache,
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
    use_cache: bool = True,
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

    def run_llm() -> tuple[bool, list[MenuItemRecord], bool, int]:
        if not llm_enabled:
            return False, [], False, 0
        try:
            items, incomplete, calls = interpret_llm.interpret_text(
                payload, api_key=api_key, model=model, use_cache=use_cache
            )
        except interpret_llm.LLMNotEnabled:
            return False, [], False, 0
        kept, _dropped = verify(items, payload, require_grounding=True)
        return True, kept, incomplete, calls

    # Allergen-matrix PDFs: ALWAYS recover the dish x allergen grid with Gemini vision.
    # The deterministic text-grid parser UNDER-reads graphical / rotated / icon-header
    # grids (the norm for big chains -- e.g. Shake Shack's chart yields 14 mangled rows
    # vs ~100 by vision), and a partial text-grid parse used to short-circuit the far
    # better vision read. For a safety app that's the wrong trade: vision is ~half a cent
    # per chart, cached for weeks and shared across a chain's locations, so we run it on
    # every allergen PDF and UNION three reads -- vision wins name conflicts
    # (authoritative for graphical grids), the text LLM fills the catalog (vision
    # under-extracts TEXT-based PDFs, e.g. Pressed 22/117), and the text-grid rows are
    # unioned in last so no allergen-bearing dish is ever lost. If vision returns nothing
    # (no key / failure) we fall through to the text-grid/text-LLM logic below.
    if (
        payload.source_type == "pdf"
        and llm_enabled
        and _looks_allergen(payload.text)
    ):
        try:
            matrix = interpret_llm.interpret_pdf_matrix(
                payload, api_key=api_key, model=model, use_cache=use_cache
            )
        except interpret_llm.LLMNotEnabled:
            matrix = []
        if matrix:
            ran, llm_items, incomplete, calls = run_llm()
            merged = _collapse_components(
                _union(_union(matrix, llm_items, _dish_key), structured, _dish_key)
            )
            label = "gemini_pdf_matrix+text" if llm_items else "gemini_pdf_matrix"
            return merged, label, "", (1 + calls), incomplete

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


_DISH_KEY_RE = re.compile(r"[^a-z0-9]+")


def _dish_key(name: str) -> str:
    """Match key for folding components into dishes: alphanumerics only, lower-cased.
    So a component's parent 'ShackBurger' folds into the dish 'ShackBurger®' (symbol /
    punctuation / spacing differences don't block the match). Deliberately NOT used to
    merge near-duplicate top-level dishes -- those are kept separate."""
    return _DISH_KEY_RE.sub("", (name or "").lower())


def _collapse_components(items: list[MenuItemRecord]) -> list[MenuItemRecord]:
    """Fold ingredient component sub-rows (marked by the matrix vision read) up into
    their parent dish and drop them, so the menu lists only ORDERABLE dishes -- without
    losing allergen data: each parent inherits the UNION of its components' allergens
    (and chart columns). A component is folded into EVERY top-level dish whose key
    matches its parent (so near-duplicate dishes both stay allergen-complete). A
    component whose parent can't be matched is KEPT as its own item (never silently drop
    allergen evidence). Records are frozen, so parents are rebuilt with replace()."""
    top_by_key: dict[str, list[int]] = {}
    for idx, it in enumerate(items):
        if not it.is_component:
            key = _dish_key(it.item_name)
            if key:
                top_by_key.setdefault(key, []).append(idx)
    add_terms: dict[int, list[str]] = {}
    add_cols: dict[int, set[str]] = {}
    add_cc: dict[int, list[str]] = {}
    folded: set[int] = set()
    for idx, it in enumerate(items):
        if not it.is_component:
            continue
        targets = top_by_key.get(_dish_key(it.parent_item))
        if not targets:
            continue  # unattributable -> keep it (emitted as-is below)
        for ti in targets:
            add_terms.setdefault(ti, []).extend(it.allergen_terms)
            add_cols.setdefault(ti, set()).update(it.matrix_allergen_columns)
            add_cc.setdefault(ti, []).extend(it.cross_contact_terms)
        folded.add(idx)
    out: list[MenuItemRecord] = []
    for idx, it in enumerate(items):
        if idx in folded:
            continue  # folded into its parent dish -> drop the component row
        if idx in add_terms:
            terms = list(dict.fromkeys(list(it.allergen_terms) + add_terms[idx]))
            cols = tuple(sorted(set(it.matrix_allergen_columns) | add_cols.get(idx, set())))
            cc = list(dict.fromkeys(list(it.cross_contact_terms) + add_cc.get(idx, [])))
            out.append(replace(it, allergen_terms=terms, matrix_allergen_columns=cols,
                               cross_contact_terms=cc))
        else:
            out.append(it)
    return out


_norm = norm_ws


def _rank(item: MenuItemRecord) -> tuple[int, int, float, int]:
    """Dedupe priority for the same dish across sources. Allergen evidence wins FIRST:
    a record carrying dish->allergen data (e.g. an allergen-matrix row) must not be
    dropped in favour of a richer-looking schema.org/price record that lacks it -- that
    silently discarded a confirmed nut mapping (a false negative). Then prefer
    schema-parsed over LLM reads, higher confidence, and a present price."""
    has_allergen = 1 if item.allergen_terms else 0
    is_structured = 0 if item.extraction_method.startswith("gemini") else 1
    has_price = 1 if (item.price or "").strip() else 0
    return (has_allergen, is_structured, item.confidence, has_price)


def _union(primary, secondary, keyfn=_norm):
    """Keep all of ``primary``; append only ``secondary`` items whose key is new.
    ``keyfn`` defaults to the whitespace-normalized name; the allergen-PDF path passes
    ``_dish_key`` so a vision row ('Creamsicle® Float') and a text-LLM row ('Creamsicle
    Float') of the SAME dish collapse to one (vision wins, keeping its allergen data)."""
    seen = {keyfn(i.item_name) for i in primary}
    out = list(primary)
    for item in secondary:
        key = keyfn(item.item_name)
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
