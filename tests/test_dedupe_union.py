"""R5: merging duplicate dish records must UNION allergen evidence, never keep one and
drop the other. In a safety-asymmetric app a confirmed dish->allergen mapping must
survive even when a lower-evidence copy of the same dish 'wins' the dedupe rank."""
import dataclasses

from safeplate.menu_text import MenuItemRecord
from safeplate.extraction2.pipeline import _dedupe_across_sources, _union, _fold_allergen_evidence


def _item(name, *, allergen=(), method="gemini_text", cols=(), cc=(), diet=(), conf=0.7):
    f = {x.name: "" for x in dataclasses.fields(MenuItemRecord)}
    f.update(item_name=name, allergen_terms=list(allergen), dietary_terms=list(diet),
             cross_contact_terms=list(cc), matrix_allergen_columns=tuple(cols),
             extraction_method=method, confidence=conf)
    return MenuItemRecord(**f)


def test_dedupe_unions_allergens_when_lower_evidence_record_wins_rank():
    # Same dish: a STRUCTURED html tag (wins the is_structured tiebreak) lists only milk;
    # the gemini vision matrix lists peanut+milk. The kept record must carry BOTH.
    html = _item("Pad Thai", allergen=["milk"], method="schema_org")
    matrix = _item("Pad Thai", allergen=["peanut", "milk"], method="gemini_allergen_matrix",
                   cols=("peanut", "milk"))
    out = _dedupe_across_sources([html, matrix])
    assert len(out) == 1
    assert set(out[0].allergen_terms) == {"peanut", "milk"}
    assert "peanut" in out[0].matrix_allergen_columns


def test_dedupe_unions_when_allergen_record_arrives_second():
    # Completion-order: a plain (no-allergen) copy lands first, the matrix second.
    plain = _item("Satay", allergen=[], method="gemini_text")
    matrix = _item("Satay", allergen=["peanut"], method="gemini_allergen_matrix",
                   cols=("peanut",))
    out = _dedupe_across_sources([plain, matrix])
    assert len(out) == 1
    assert "peanut" in out[0].allergen_terms


def test_union_folds_secondary_allergens_into_kept_primary():
    primary = [_item("Brownie", allergen=["milk"], method="gemini_pdf_matrix")]
    secondary = [_item("Brownie", allergen=["tree nut"], method="gemini_text")]
    out = _union(primary, secondary)
    assert len(out) == 1
    assert set(out[0].allergen_terms) == {"milk", "tree nut"}


def test_fold_unions_cross_contact_and_keeps_base_identity():
    base = _item("Cake", allergen=["egg"], method="schema_org", cc=["peanut"])
    other = _item("Cake", allergen=["tree nut"], method="gemini_text", cc=["sesame"])
    folded = _fold_allergen_evidence(base, other)
    assert folded.extraction_method == "schema_org"            # base identity kept
    assert set(folded.allergen_terms) == {"egg", "tree nut"}
    assert set(folded.cross_contact_terms) == {"peanut", "sesame"}
