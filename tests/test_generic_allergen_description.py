"""Regression: allergen evidence that appears only in a dish DESCRIPTION must be
caught on the generic (non-nut) path, exactly as it is on the nut path.

Bug this locks: the nut path feeds both item_name and description into the dish
prior, but `_score_generic_allergen` passed only the name into
`restaurant_allergen_risk`, which in turn matched KB patterns against the name
alone. "Chicken Pasta" / "in a creamy alfredo sauce" therefore never matched the
milk KB's "alfredo" pattern -- a silent false negative on the multi-allergen path,
the asymmetric failure direction this product must never take.
"""

from __future__ import annotations

from safeplate.allergen_prior import restaurant_allergen_risk
from safeplate.allergen_score import (
    AllergenPref,
    RestaurantSignals,
    Severity,
    UserProfile,
    score_restaurant_for_user,
)

MILK_ALLERGY = UserProfile(
    allergens=(AllergenPref(allergen="milk", severity=Severity.ALLERGY),)
)


def _menu_item(name: str, description: str = "") -> dict:
    return {
        "item_name": name,
        "description": description,
        "allergen_terms": [],
        "extraction_method": "gemini_text",
        "source_type": "website_link",
        "menu_source_url": "http://example.test/menu",
    }


def test_description_only_kb_match_is_flagged():
    res = restaurant_allergen_risk(
        allergen="milk",
        cuisines=["italian"],
        region="US",
        menu_items=[
            {"name": "Chicken Pasta", "description": "in a creamy alfredo sauce"}
        ],
    )
    assert [name for name, _risk in res.riskiest_items] == ["Chicken Pasta"]
    assert res.risk >= 0.9  # alfredo is a 0.95 milk pattern in the KB


def test_name_only_kb_match_still_works():
    res = restaurant_allergen_risk(
        allergen="milk",
        cuisines=["italian"],
        region="US",
        menu_items=[{"name": "Cheese Board"}],  # no description key at all
    )
    assert [name for name, _risk in res.riskiest_items] == ["Cheese Board"]


def test_description_only_match_reaches_dish_prior_end_to_end():
    result = score_restaurant_for_user(
        MILK_ALLERGY,
        cuisines=["italian"],
        region="US",
        menu_items=[_menu_item("Chicken Pasta", "in a creamy alfredo sauce")],
        signals=RestaurantSignals(),
    )
    assert result.evidence_basis == "dish_prior"
