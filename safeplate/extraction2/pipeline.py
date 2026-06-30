from __future__ import annotations

import re
from dataclasses import replace

from safeplate.extraction2 import interpret_llm
from safeplate.extraction2.interpret_structured import interpret_structured
from safeplate.extraction2.region import detect_source_region
from safeplate.extraction2.schema import (
    CoverageReport,
    MenuExtractionResult,
    Payload,
    PayloadKind,
    Policy,
)
from safeplate.extraction2.verify import mean_confidence, verify
from safeplate.ingredient_allergens import infer_allergens
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
                # Content-locale: which region does this source's text/URL belong to?
                # Stamped per source so the orchestrator can compare it to the diner's
                # region and surface a from-another-region notice. Only sources that
                # yielded items can ever surface a banner, so skip the scan otherwise.
                region=(detect_source_region(payload.text or "", payload.url) or "")
                if items else "",
            )
        )
        items_all.extend(items)

    return MenuExtractionResult(
        items=_dedupe_across_sources(_enrich_inferred_allergens(items_all)),
        coverage=coverage,
        llm_calls=llm_calls,
        incomplete=incomplete,
    )


def _enrich_inferred_allergens(items: list[MenuItemRecord]) -> list[MenuItemRecord]:
    """Fold ingredient-implied allergens into each item: ``tahini`` -> sesame,
    ``paneer`` -> milk, ``pesto`` -> may-contain tree nut. Definite inferences join
    ``allergen_terms`` (treated as confirmed presence); "often" ones join
    ``cross_contact_terms``. Items from an authoritative allergen chart are trusted
    exactly as parsed and left untouched -- a chart's verdict outranks a guess from
    the dish name."""
    out: list[MenuItemRecord] = []
    for it in items:
        if "matrix" in (it.extraction_method or "").lower():
            out.append(it)
            continue
        definite, maybe = infer_allergens(f"{it.item_name} {it.description} {it.raw_text}")
        if not definite and not maybe:
            out.append(it)
            continue
        allergens = list(dict.fromkeys(list(it.allergen_terms) + definite))
        cc = list(dict.fromkeys(
            list(it.cross_contact_terms) + [t for t in maybe if t not in allergens]
        ))
        out.append(replace(it, allergen_terms=allergens, cross_contact_terms=cc))
    return out


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
    # better vision read. So we run vision on every allergen PDF: vision wins name
    # conflicts (authoritative for graphical grids) and the text-grid rows are unioned in
    # so no allergen-bearing dish is lost. The text LLM is kept ONLY when vision likely
    # UNDER-read -- it found materially more dishes than vision (the text-based-PDF case,
    # e.g. Pressed: vision 22 vs ~117). When vision already has a full chart, the text
    # LLM mostly adds noise (component / near-duplicate names) that pads the list and
    # dilutes the chart's coverage, so we drop it and trust vision. Vision empty
    # (no key / failure) -> fall through to the text-grid/text-LLM logic below.
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
            # Keep the text LLM ONLY if it found materially more NET-NEW dishes than
            # vision -- i.e. vision under-read (text-based PDF). Compare NET-NEW (names
            # vision doesn't already have), not raw counts: a verbose chart's text layer
            # lists every component, so raw text count can exceed vision while adding only
            # noise. Pressed: ~95 net-new vs vision 22 -> keep. Shake Shack: net-new is a
            # minority of vision's items -> drop (don't pad a chart vision already read).
            vision_keys = {_dish_key(i.item_name) for i in matrix}
            net_new = sum(1 for i in llm_items if _dish_key(i.item_name) not in vision_keys)
            if net_new > len(matrix) * _TEXT_OVER_VISION_FACTOR:
                merged = _collapse_components(
                    _union(_union(matrix, llm_items, _dish_key), structured, _dish_key)
                )
                return merged, "gemini_pdf_matrix+text", "", (1 + calls), incomplete
            # Vision has the full chart -> trust it; don't let the text LLM pad the list.
            merged = _collapse_components(_union(matrix, structured, _dish_key))
            return merged, "gemini_pdf_matrix", "", (1 + calls), False

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


# Keep the text-LLM's items alongside a vision chart only when its NET-NEW dish count
# (names vision doesn't already have) exceeds this multiple of vision's item count --
# i.e. vision clearly under-read (text-based PDF). Otherwise the text LLM is just padding
# a chart vision already read in full.
_TEXT_OVER_VISION_FACTOR = 1.0


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


def _fold_allergen_evidence(
    base: MenuItemRecord, other: MenuItemRecord
) -> MenuItemRecord:
    """Union the allergen-bearing fields of ``other`` into ``base`` (which keeps all its
    own identity/price/text fields). SAFETY (R5): when two records of the SAME dish
    merge, never keep one view and discard the other's allergen mapping -- a confirmed
    dish->nut fact from a slower/lower-ranked source must survive. Returns ``base``
    unchanged when there is nothing new to add (cheap no-op)."""
    terms = list(dict.fromkeys(list(base.allergen_terms) + list(other.allergen_terms)))
    cols = tuple(dict.fromkeys(list(base.matrix_allergen_columns) + list(other.matrix_allergen_columns)))
    cc = list(dict.fromkeys(list(base.cross_contact_terms) + list(other.cross_contact_terms)))
    diet = list(dict.fromkeys(list(base.dietary_terms) + list(other.dietary_terms)))
    if (terms == list(base.allergen_terms)
            and cols == tuple(base.matrix_allergen_columns)
            and cc == list(base.cross_contact_terms)
            and diet == list(base.dietary_terms)):
        return base
    return replace(base, allergen_terms=terms, matrix_allergen_columns=cols,
                   cross_contact_terms=cc, dietary_terms=diet)


def _union(primary, secondary, keyfn=_norm):
    """Keep all of ``primary``; append ``secondary`` items whose key is new. When a
    ``secondary`` item is the SAME dish as a ``primary`` one, fold its allergen evidence
    INTO the primary (primary keeps identity) instead of dropping it -- so a vision row
    ('Creamsicle® Float') and a text-LLM row ('Creamsicle Float') of one dish collapse to
    a single record carrying the UNION of their allergens (R5)."""
    out = list(primary)
    index: dict[str, int] = {}
    for i, item in enumerate(out):
        key = keyfn(item.item_name)
        if key:
            index.setdefault(key, i)
    for item in secondary:
        key = keyfn(item.item_name)
        if not key:
            continue
        if key in index:
            i = index[key]
            out[i] = _fold_allergen_evidence(out[i], item)
        else:
            index[key] = len(out)
            out.append(item)
    return out


def _dedupe_across_sources(items: list[MenuItemRecord]) -> list[MenuItemRecord]:
    best: dict[str, MenuItemRecord] = {}
    for item in items:
        key = _norm(item.item_name)
        if not key:
            continue
        current = best.get(key)
        if current is None:
            best[key] = item
            continue
        # Keep the higher-ranked record's identity, but UNION the loser's allergen
        # evidence into it so a duplicate never silently drops a dish->allergen mapping.
        winner, loser = (item, current) if _rank(item) > _rank(current) else (current, item)
        best[key] = _fold_allergen_evidence(winner, loser)
    return list(best.values())
