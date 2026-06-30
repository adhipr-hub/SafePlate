"""The extraction pipeline enriches non-matrix items with inferred allergens.

Text/LLM-extracted dishes name ingredients, not allergens; the inference layer
(ingredient_allergens) folds the implied allergens in. Authoritative allergen
charts are trusted as-is and never overridden.
"""

from __future__ import annotations

from safeplate.extraction2.pipeline import _enrich_inferred_allergens
from safeplate.menu_text import MenuItemRecord


def _item(name, desc="", allergens=(), method="gemini_text", raw=""):
    return MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="",
        category="", item_name=name, description=desc, price="",
        dietary_terms=[], allergen_terms=list(allergens), source_type="",
        extraction_method=method, confidence=0.6, raw_text=raw or f"{name} {desc}",
        fetched_at="",
    )


def test_tahini_in_description_grounds_sesame():
    [out] = _enrich_inferred_allergens([
        _item("Babaganoush", "fire-roasted eggplant, tahini, garlic")
    ])
    assert "sesame" in out.allergen_terms


def test_definite_dairy_and_maybe_treenut_split():
    [out] = _enrich_inferred_allergens([
        _item("Pesto Pasta", "basil, parmesan, olive oil")
    ])
    assert "milk" in out.allergen_terms            # parmesan -> definite
    assert "tree nut" in out.cross_contact_terms   # pesto -> may-contain
    assert "tree nut" not in out.allergen_terms


def test_existing_allergens_preserved_and_deduped():
    [out] = _enrich_inferred_allergens([
        _item("Hummus", "chickpeas, tahini", allergens=["sesame"])
    ])
    assert out.allergen_terms.count("sesame") == 1


def test_allergen_matrix_items_are_not_overridden():
    # An authoritative chart that lists ONLY milk for a tahini dish is trusted:
    # inference must not silently add sesame on top of the chart's verdict.
    [out] = _enrich_inferred_allergens([
        _item("Sesame Noodles", "noodles, tahini", allergens=["milk"],
              method="allergen_matrix")
    ])
    assert out.allergen_terms == ["milk"]
    assert "sesame" not in out.allergen_terms


def test_plain_dish_unchanged():
    [out] = _enrich_inferred_allergens([_item("Grilled Chicken", "with rice")])
    assert out.allergen_terms == []
    assert out.cross_contact_terms == []
