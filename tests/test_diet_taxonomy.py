import json
from pathlib import Path

from safeplate.allergens import DIETS


def test_vegan_excludes_dairy_and_egg_categories():
    assert "dairy" in DIETS["vegan"].excluded_categories
    assert "egg" in DIETS["vegan"].excluded_categories
    # Vegetarians keep dairy/egg:
    assert "dairy" not in DIETS["vegetarian"].excluded_categories
    assert "egg" not in DIETS["vegetarian"].excluded_categories


def test_meat_kb_has_dairy_and_egg_terms():
    kb = json.loads((Path(__file__).resolve().parents[1] /
                     "data" / "allergen_kb" / "meat_animal.json").read_text(encoding="utf-8"))
    for term in ("cheese", "paneer", "butter", "cream"):
        assert term in kb["dairy"], term
    for term in ("omelette", "omelet"):
        assert term in kb["egg"], term
