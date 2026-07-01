from safeplate import allergen_prior as ap


def test_cuisine_baseline_reads_generic_table():
    prior = ap.allergen_cuisine_baseline("gluten", ["italian"], "US")
    assert prior.risk >= 0.7  # italian gluten baseline is high
    assert prior.allergen == "gluten"


def test_unknown_allergen_falls_back_to_default_baseline():
    prior = ap.allergen_cuisine_baseline("celery", ["thai"], "US")
    assert 0.0 < prior.risk < 0.3  # no table yet -> low default, never zero/"safe"


def test_restaurant_allergen_risk_flags_known_dish():
    risk = ap.restaurant_allergen_risk(
        allergen="milk",
        cuisines=["italian"],
        region="US",
        menu_items=[{"name": "Fettuccine Alfredo"}, {"name": "Garden Salad"}],
    )
    assert risk.risk >= 0.8
    names = [d["name"] for d in risk.item_details if d["risk"] >= 0.8]
    assert any("alfredo" in n.lower() for n in names)


def test_restaurant_allergen_risk_floor_never_zero():
    risk = ap.restaurant_allergen_risk(
        allergen="mustard", cuisines=["thai"], region="US", menu_items=[{"name": "Plain Rice"}]
    )
    assert risk.risk > 0.0  # absence != safe


# --------------------------------------------------------------------------- #
# Task 3: generic scorer dispatch (+ nut byte-identical regression guard)
# --------------------------------------------------------------------------- #
from safeplate.allergen_score import (  # noqa: E402
    AllergenPref, Severity, UserProfile, score_restaurant_for_user, matrix_covers,
)
from safeplate.menu_text import MenuItemRecord  # noqa: E402


def _item(item_name, *, allergen_terms=(), extraction_method="listed",
          matrix_allergen_columns=(), cross_contact_terms=()):
    """Build a real MenuItemRecord (all required fields present). The dish-name
    field is ``item_name``, NOT ``name``."""
    return MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="",
        category="", item_name=item_name, description="", price="",
        dietary_terms=[], allergen_terms=list(allergen_terms),
        source_type="", extraction_method=extraction_method, confidence=0.9,
        raw_text="", fetched_at="",
        matrix_allergen_columns=tuple(matrix_allergen_columns),
        cross_contact_terms=list(cross_contact_terms),
    )


def test_matrix_covers_canonicalizes():
    assert matrix_covers("milk", ["Milk", "Egg"]) is True
    assert matrix_covers("tree_nut", ["tree nut"]) is True
    assert matrix_covers("milk", ["Gluten"]) is False


def test_generic_allergen_chart_hit_avoids():
    profile = UserProfile(allergens=(AllergenPref(allergen="milk", severity=Severity.ALLERGY),))
    items = [_item("Cheese Pizza", allergen_terms=["milk", "gluten"],
                   extraction_method="allergen_matrix",
                   matrix_allergen_columns=("milk", "gluten"))]
    a = score_restaurant_for_user(profile, cuisines=["italian"], region="US", menu_items=items)
    assert a.tier == "avoid"
    assert a.per_allergen[0].allergen == "milk"


def test_generic_allergen_no_evidence_caps_at_caution():
    profile = UserProfile(allergens=(AllergenPref(allergen="milk", severity=Severity.ANAPHYLAXIS),))
    a = score_restaurant_for_user(profile, cuisines=["italian"], region="US", menu_items=[])
    assert a.tier in ("caution", "likely_ok")  # prior alone never grounds AVOID
    assert a.tier != "avoid"


def test_nut_profile_byte_identical():
    # A nuts-only profile must route to the untouched nut path and score as before.
    profile = UserProfile.for_nuts(Severity.ANAPHYLAXIS)
    items = [_item("Pad Thai", allergen_terms=["peanut"],
                   extraction_method="allergen_matrix",
                   matrix_allergen_columns=("peanut",))]
    a = score_restaurant_for_user(profile, cuisines=["thai"], region="US", menu_items=items)
    assert a.tier == "avoid"
    assert a.per_allergen[0].allergen == "nuts"
