from safeplate.diet_score import assess_diet
from safeplate.menu_text import MenuItemRecord


def _item(item_name, *, allergen_terms=(), extraction_method="listed", dietary_terms=()):
    return MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="",
        category="", item_name=item_name, description="", price="",
        dietary_terms=list(dietary_terms), allergen_terms=list(allergen_terms),
        source_type="", extraction_method=extraction_method, confidence=0.9,
        raw_text="", fetched_at="",
    )


def test_vegan_flags_dairy_chart_hit():
    items = [_item("Cheese Pizza", allergen_terms=["milk"], extraction_method="allergen_matrix")]
    a = assess_diet("vegan", menu_items=items)
    assert a.verdict == "not_compatible"
    assert "Cheese Pizza" in a.offending_items


def test_vegan_flags_meat_by_name():
    items = [_item("Beef Burger")]
    a = assess_diet("vegan", menu_items=items)
    assert a.verdict == "not_compatible"


def test_vegetarian_allows_dairy():
    items = [_item("Margherita Pizza", allergen_terms=["milk", "gluten"],
                    extraction_method="allergen_matrix", dietary_terms=["vegetarian"])]
    a = assess_diet("vegetarian", menu_items=items)
    assert a.verdict in ("good_options", "limited")  # dairy is fine for lacto-veg
    assert "Margherita Pizza" in a.compatible_items


def test_empty_menu_is_unknown_not_good():
    a = assess_diet("vegan", menu_items=[])
    assert a.verdict == "unknown"  # never assume compatible with no evidence


def test_all_unlabeled_vegan_menu_is_estimated_and_capped():
    # Non-conflict, unlabeled dishes are now ASSUMED compatible from the name,
    # but for vegan the name-only 'estimated' basis is capped at 'limited' and
    # never presented as confirmed/good_options.
    items = [_item("Mystery Dish A"), _item("Mystery Dish B"), _item("House Special")]
    a = assess_diet("vegan", menu_items=items)
    assert a.verdict == "limited"
    assert a.basis == "estimated"


def test_unlabeled_item_is_estimated_not_labeled():
    # A non-excluded allergen (gluten) + no positive dietary label: vegan
    # compatibility is ESTIMATED from the dish name, never 'labeled', and the
    # vegan estimated cap keeps it at 'limited' (not good_options). Allergen data
    # itself is still NOT treated as positive diet evidence.
    items = [_item("House Special", allergen_terms=["gluten"])]
    a = assess_diet("vegan", menu_items=items)
    assert a.basis == "estimated"
    assert a.verdict == "limited"
    assert "House Special" in a.compatible_items


def test_diet_summary_payload_shape():
    from safeplate.menu_service import _diet_summary_payload

    items = [_item("Beef Burger")]
    out = _diet_summary_payload(frozenset({"vegan"}), items, cuisines=["american"])
    assert out[0]["diet"] == "vegan"
    assert out[0]["verdict"] == "not_compatible"
    assert "offendingItems" in out[0]
    assert "compatibleItems" in out[0]
