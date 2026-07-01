import json
from pathlib import Path

from safeplate.allergens import DIETS
from safeplate.diet_score import _item_conflicts


def test_vegan_excludes_dairy_and_egg_categories():
    assert "dairy" in DIETS["vegan"].excluded_categories
    assert "egg" in DIETS["vegan"].excluded_categories
    # Vegetarians keep dairy/egg:
    assert "dairy" not in DIETS["vegetarian"].excluded_categories
    assert "egg" not in DIETS["vegetarian"].excluded_categories


def test_meat_kb_has_dairy_and_egg_terms():
    kb = json.loads((Path(__file__).resolve().parents[1] /
                     "data" / "allergen_kb" / "meat_animal.json").read_text(encoding="utf-8"))
    for term in ("cheese", "paneer", "ghee", "yogurt"):
        assert term in kb["dairy"], term
    for term in ("omelette", "omelet"):
        assert term in kb["egg"], term


def test_vegan_false_friends_not_flagged():
    """Regression test: vegan dishes with false-friend dairy/egg substrings should not be flagged."""
    vegan = DIETS["vegan"]
    for name in ["Peanut Butter Noodles", "Almond Butter Smoothie",
                 "Coconut Cream Curry", "Cashew Cream Pasta",
                 "Vegan Mayonnaise Sandwich"]:
        assert not _item_conflicts(vegan, name.lower(), []), f"False positive for {name}"


def test_vegan_still_flags_real_dairy():
    """Regression test: vegan should still flag real dairy/egg dishes."""
    vegan = DIETS["vegan"]
    for name in ["Cheese Quesadilla", "Paneer Tikka", "Chicken Alfredo", "Cheese Omelette"]:
        assert _item_conflicts(vegan, name.lower(), []), f"False negative for {name}"
