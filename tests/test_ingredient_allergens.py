"""Spec for ingredient -> allergen inference (the implicit-ingredient gap).

A literal allergen WORD (peanut, almond, sesame) is already caught by
menu_text._dietary_and_allergen_terms. This layer covers ingredient/dish names
that IMPLY an allergen without naming it (tahini -> sesame, paneer -> milk),
across cuisines. `infer_allergens(text)` returns (definite, may_contain):

  * definite      -> folds into a record's allergen_terms (confirmed presence)
  * may_contain   -> folds into cross_contact_terms (the softer "often has" channel)

Accuracy bar: NO false positives on near-miss words (eggplant, coconut milk,
butternut, gluten-free pita) -- an over-warn is cheap but the user asked for
"accurate and reasonable", and the safety eval's 0/30 over-warn bar must hold.
"""

from __future__ import annotations

import pytest

from safeplate.ingredient_allergens import infer_allergens


def definite(text):
    return set(infer_allergens(text)[0])


def maybe(text):
    return set(infer_allergens(text)[1])


# --------------------------------------------------------------------------- #
# DEFINITE: the named ingredient near-certainly contains the allergen.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    # Sesame -- the motivating case (Oren's tahini)
    ("Babaganoush: eggplant, tahini, garlic", "sesame"),
    ("Halva for dessert", "sesame"),
    ("Everything bagel with za'atar", "sesame"),
    # Milk / dairy across cuisines
    ("Paneer Tikka Masala", "milk"),
    ("Chicken Alfredo", "milk"),
    ("Saag with ghee", "milk"),
    ("Queso fundido", "milk"),
    ("Burrata and tomato", "milk"),
    ("Labneh with olive oil", "milk"),
    # Egg
    ("Spaghetti Carbonara", "egg"),
    ("Lemon meringue pie", "egg"),
    ("Club sandwich with mayonnaise", "egg"),
    # Soy
    ("Miso soup", "soy"),
    ("Agedashi tofu", "soy"),
    ("Steamed edamame", "soy"),
    ("Tempeh stir fry", "soy"),
    # Wheat / gluten
    ("Crispy seitan", "wheat"),
    ("Pork katsu with panko", "wheat"),
    ("Beef udon", "wheat"),
    # Fish
    ("Caesar made with anchovy", "fish"),
    ("Pad see ew with fish sauce", "fish"),
    ("Spicy tuna roll", "fish"),
    ("Bonito flakes", "fish"),
    # Crustacean shellfish
    ("Garlic prawns", "shellfish"),
    ("Lobster bisque", "shellfish"),
    ("Shrimp scampi", "shellfish"),
    # Mollusc
    ("Fried calamari", "mollusc"),
    ("Steamed mussels", "mollusc"),
    ("Beef with oyster sauce", "mollusc"),
    # Mustard / celery
    ("Pork with dijon", "mustard"),
    ("Mirepoix of celery, onion, carrot", "celery"),
    # Peanut (definite dishes)
    ("Gado-gado salad", "peanut"),
    ("Groundnut stew", "peanut"),
    # Tree nut (definite dishes / ingredients)
    ("Baklava, two pieces", "tree nut"),
    ("Marzipan stollen", "almond"),
    ("Gianduja gelato", "hazelnut"),
])
def test_definite_ingredient_implies_allergen(text, expected):
    assert expected in definite(text), (text, infer_allergens(text))


# --------------------------------------------------------------------------- #
# MAY-CONTAIN: often-but-not-always; routes to the softer cross-contact channel.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("Chicken satay skewers", "peanut"),
    ("Pad thai", "peanut"),
    ("Kung pao chicken", "peanut"),
    ("Classic basil pesto", "tree nut"),   # pine nut usually; walnut/cashew sometimes
    ("Chicken korma", "tree nut"),
    ("Pistachio kulfi", "tree nut"),       # kulfi often, not always, nutty
])
def test_may_contain_routes_to_soft_channel(text, expected):
    d, m = infer_allergens(text)
    assert expected in set(m), (text, d, m)
    assert expected not in set(d), f"{text}: should be may-contain, not definite"


def test_pesto_flags_walnut_possibility_not_just_pine_nut():
    # The user's note: pesto can contain walnuts (or cashew), not only pine nuts,
    # so the family token (tree nut) is the safe signal, as a may-contain.
    _d, m = infer_allergens("house-made classic pesto")
    assert "tree nut" in set(m)


def test_peanut_free_does_not_suppress_tree_nut():
    # SAFETY: "peanut-free" contains the substring "nut-free" but must only suppress
    # PEANUT -- suppressing tree nut here would hide a real tree-nut allergen.
    d, m = infer_allergens("peanut-free baklava")  # baklava -> tree nut (definite)
    assert "tree nut" in (set(d) | set(m))
    assert "peanut" not in (set(d) | set(m))


# --------------------------------------------------------------------------- #
# FALSE FRIENDS: must NOT flag.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,not_expected", [
    ("Grilled eggplant parm", "egg"),          # eggplant != egg
    ("Coconut milk curry", "milk"),            # plant milk != dairy
    ("Almond milk latte", "milk"),             # plant milk != dairy
    ("Oat milk flat white", "milk"),
    ("Butternut squash soup", "milk"),         # butternut != butter
    ("Butternut squash soup", "tree nut"),     # butternut != nut
    ("Peanut butter cookie", "milk"),          # nut butter != dairy
    ("Cocoa butter dessert", "milk"),
    ("Water chestnut stir fry", "tree nut"),   # water chestnut != chestnut nut
    ("Buckwheat soba", "wheat"),               # buckwheat != wheat
    ("Sauteed oyster mushroom", "mollusc"),    # oyster mushroom != mollusc
    ("Nutmeg dusted latte", "tree nut"),       # nutmeg != nut
    ("Cream of tartar meringue", "milk"),      # cream of tartar != dairy cream
])
def test_false_friends_not_flagged(text, not_expected):
    d, m = infer_allergens(text)
    assert not_expected not in (set(d) | set(m)), (text, d, m)


# --------------------------------------------------------------------------- #
# NEGATION: an explicit free-from qualifier suppresses that allergen.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,suppressed", [
    ("Gluten-free pita", "wheat"),
    ("Dairy-free alfredo (cashew cream)", "milk"),
    ("Vegan carbonara", "egg"),
    ("Vegan queso", "milk"),
    ("Sesame-free hummus", "sesame"),
])
def test_free_from_qualifier_suppresses(text, suppressed):
    d, m = infer_allergens(text)
    assert suppressed not in (set(d) | set(m)), (text, d, m)


# --------------------------------------------------------------------------- #
# Canonical tokens must be ones the scorer understands (nut families).
# --------------------------------------------------------------------------- #
def test_tree_nut_tokens_are_scorer_canonical():
    from safeplate.allergen_score import _PEANUT_TERMS, _TREE_NUT_TERMS
    d, m = infer_allergens("baklava, satay, pesto, marzipan")
    nut_tokens = {t for t in (set(d) | set(m))
                  if t in {"peanut", "tree nut", "almond", "walnut", "cashew",
                           "hazelnut", "pistachio", "pecan", "pine nut",
                           "macadamia", "brazil nut"}}
    for tok in nut_tokens:
        assert tok in (_PEANUT_TERMS | _TREE_NUT_TERMS | {"tree nut"}), tok


def test_empty_and_plain_text_infers_nothing():
    assert infer_allergens("") == ([], [])
    assert infer_allergens("grilled chicken breast with rice") == ([], [])
