"""The vision allergen-matrix read must report the chart's allergen COLUMNS (so the
scorer recognizes a chart covers nuts even when no dish is marked nut-present) and the
per-dish CROSS-CONTACT marks (so a 'shared-facility: tree nut' chart isn't read as
nut-safe for a trace-sensitive user)."""
from __future__ import annotations

from safeplate.menu_fetch_llm import _absorb_matrix_rows
from safeplate.allergen_score import (
    CrossContactSensitivity, Severity, Tier, UserProfile, score_restaurant_for_user,
)


def test_absorb_sets_canonical_columns_and_cross_contact():
    records: list = []
    _absorb_matrix_rows(
        [{"dish": "Fries", "allergens": ["soy"], "cross_contact": ["Tree Nuts"]}],
        records, set(), "R", "",
        columns=["Peanuts", "Tree Nuts", "Milk/Dairy", "Coconut"],  # coconut dropped
    )
    rec = records[0]
    # Columns canonicalized + sorted; coconut is not a tracked tree nut here.
    assert rec.matrix_allergen_columns == ("milk", "peanut", "tree nut")
    assert rec.cross_contact_terms == ["Tree Nuts"]
    assert rec.allergen_terms == ["soy"]  # presence kept separate from cross-contact


def _matrix_item(name, *, allergen_terms=None, cross_contact=None, columns=("peanut", "tree nut", "milk")):
    return {
        "item_name": name, "description": "", "allergen_terms": allergen_terms or [],
        "extraction_method": "gemini_allergen_matrix", "menu_source_url": "https://r/chart",
        "matrix_allergen_columns": columns, "cross_contact_terms": cross_contact or [],
    }


def _score(profile, items):
    return score_restaurant_for_user(profile, cuisines=["american"], region="US", menu_items=items)


def test_chart_nut_cross_contact_floors_risk_for_trace_sensitive():
    """A chart that marks tree-nut CROSS-CONTACT (not present) on a dish must raise the
    floor for a trace-sensitive user -- not be read as nut-safe -- while NOT being
    counted as a confirmed nut dish."""
    items = [_matrix_item("Fries", cross_contact=["tree nut"]),
             _matrix_item("Soda"), _matrix_item("Burger"), _matrix_item("Salad")]
    strict = UserProfile.for_nuts(Severity.ALLERGY, cross_contact=CrossContactSensitivity.STRICT)
    relaxed = UserProfile.for_nuts(Severity.ALLERGY, cross_contact=CrossContactSensitivity.NOT_CONCERNED)
    a_strict = _score(strict, items)
    a_relaxed = _score(relaxed, items)
    # Shared-facility nut mark lifts the trace-sensitive user's risk above the
    # not-concerned user's (who tolerates traces).
    assert a_strict.overall_risk > a_relaxed.overall_risk
    assert any("cross-contact" in r.lower() for r in a_strict.rationale)
    # Cross-contact is NOT presence -> no dish is flagged as containing the nut.
    assert a_strict.per_allergen[0].menu_flagged == 0


def test_chart_with_nut_column_is_recognized_as_matrix_even_with_no_nut_marks():
    """A clean chart that HAS peanut/tree-nut columns but marks no dish nut-present is
    recognized as an authoritative allergen chart (basis allergen_matrix), not a guess."""
    items = [_matrix_item("Burger", allergen_terms=["milk", "wheat"]),
             _matrix_item("Fries", allergen_terms=["soy"]),
             _matrix_item("Salad")]
    a = _score(UserProfile.for_nuts(Severity.ALLERGY), items)
    assert a.evidence_basis == "allergen_matrix"
    assert Tier(a.tier).rank <= Tier.CAUTION.rank  # clean chart -> not 'avoid'
