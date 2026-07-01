# tests/test_diet_score_floor.py
from types import SimpleNamespace

from safeplate.diet_score import assess_diet


def _item(name, dietary=(), allergens=()):
    return SimpleNamespace(item_name=name, dietary_terms=list(dietary),
                           allergen_terms=list(allergens))


def test_vegetarian_assumed_from_name_reaches_good_options():
    items = [_item("Garden Salad"), _item("Margherita Pizza"), _item("Veg Spring Rolls")]
    a = assess_diet("vegetarian", menu_items=items)
    assert a.verdict == "good_options"
    assert a.basis == "estimated"
    assert "estimat" in " ".join(a.rationale).lower()


def test_vegetarian_conflict_from_meat_name():
    items = [_item("Grilled Chicken Caesar"), _item("Pepperoni Pizza")]
    a = assess_diet("vegetarian", menu_items=items)
    assert a.verdict == "not_compatible"


def test_vegan_cheese_name_is_conflict_not_assumed():
    # Cheese/paneer are vegan conflicts (Task 1). All items conflict -> not_compatible.
    # NOTE: "Butter Naan" is deliberately NOT used here -- a later Task 1 follow-up
    # (commit 2d67be1) dropped bare "butter" from the dairy KB as a false-friend term
    # (it was flagging "Peanut Butter Noodles" etc.), so it no longer conflicts by name.
    items = [_item("Cheese Quesadilla"), _item("Paneer Tikka")]
    a = assess_diet("vegan", menu_items=items)
    assert a.verdict == "not_compatible"


def test_vegan_estimated_is_capped_at_limited():
    # No labels, no conflicts -> assumed vegan, but deterministic estimate caps at limited.
    items = [_item("Garden Salad"), _item("Steamed Rice"), _item("Fruit Bowl")]
    a = assess_diet("vegan", menu_items=items)
    assert a.verdict == "limited"          # NOT good_options
    assert a.basis == "estimated"


def test_labeled_vegan_reaches_good_options():
    items = [_item("House Bowl", dietary=["vegan"]),
             _item("Green Curry", dietary=["vegan"]),
             _item("Grilled Chicken")]
    a = assess_diet("vegan", menu_items=items)
    assert a.verdict == "good_options"
    assert a.basis in ("labeled", "mixed")


def test_empty_menu_is_unknown():
    a = assess_diet("vegan", menu_items=[])
    assert a.verdict == "unknown"
