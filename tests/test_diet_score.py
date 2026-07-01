from safeplate.diet_score import assess_diet
from safeplate.menu_text import MenuItemRecord


def _item(item_name, *, allergen_terms=(), extraction_method="listed"):
    return MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="",
        category="", item_name=item_name, description="", price="",
        dietary_terms=[], allergen_terms=list(allergen_terms),
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
    items = [_item("Margherita Pizza", allergen_terms=["milk", "gluten"], extraction_method="allergen_matrix")]
    a = assess_diet("vegetarian", menu_items=items)
    assert a.verdict in ("good_options", "limited")  # dairy is fine for lacto-veg
    assert "Margherita Pizza" in a.compatible_items


def test_empty_menu_is_unknown_not_good():
    a = assess_diet("vegan", menu_items=[])
    assert a.verdict == "unknown"  # never assume compatible with no evidence


def test_all_unknown_menu_is_unknown():
    items = [_item("Mystery Dish A"), _item("Mystery Dish B"), _item("House Special")]
    a = assess_diet("vegan", menu_items=items)
    assert a.verdict == "unknown"      # non-empty but zero informative items
    assert a.support == 0.0
