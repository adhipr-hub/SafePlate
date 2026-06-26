"""Regression tests for the robustness improvements (per-nut matrix columns,
definitional grounded nut terms, added nut desserts with collision guards)."""
from __future__ import annotations

from safeplate.allergen_matrix import extract_items_from_allergen_matrix
from safeplate.allergen_score import _nut_terms_present
from safeplate.allergen_prior import PEANUTS, TREE_NUTS, score_menu_item_prior
from safeplate.menu_text import _dietary_and_allergen_terms

NUTS = {PEANUTS, TREE_NUTS}


# --- R1: per-nut allergen-matrix columns (Almond / Cashew / ...) -------------------
_PER_NUT_MATRIX = """
<table><thead><tr>
  <th>Dish</th><th>Milk</th><th>Egg</th><th>Almond</th><th>Cashew</th>
</tr></thead><tbody>
  <tr><td>Almond Croissant</td><td></td><td>✓</td><td>✓</td><td></td></tr>
  <tr><td>Cashew Cluster</td><td></td><td></td><td></td><td>✓</td></tr>
  <tr><td>Plain Roll</td><td>✓</td><td></td><td></td><td></td></tr>
</tbody></table>
"""


def test_per_nut_matrix_columns_are_read_as_tree_nut():
    by_name = {r.item_name: r for r in extract_items_from_allergen_matrix(_PER_NUT_MATRIX)}
    # A real matrix was recognized (3 distinct allergens: milk, egg, tree nut).
    assert {"Almond Croissant", "Cashew Cluster", "Plain Roll"} <= set(by_name)
    # Dishes ticked under Almond / Cashew now record a tree-nut hit...
    assert "tree nut" in by_name["Almond Croissant"].allergen_terms
    assert "tree nut" in by_name["Cashew Cluster"].allergen_terms
    # ...and a nut-free dish does not.
    assert "tree nut" not in by_name["Plain Roll"].allergen_terms


# --- R2: definitional nut-derived ingredients become GROUNDED evidence -------------
def test_definitional_nut_terms_are_grounded():
    for name in ("Marzipan Stollen", "Nutella Crepe", "Gianduja Tart", "Pignoli Cookies"):
        _diet, allergens = _dietary_and_allergen_terms(name)
        assert _nut_terms_present(allergens, NUTS), f"{name} not grounded as a nut"


# --- R3: added nut desserts + the macaron/macaroon collision guard -----------------
def test_added_nut_desserts_match():
    for name in ("Pistachio Macaron", "Turron", "Linzer Torte", "Marcona Almonds"):
        prior = score_menu_item_prior(item_name=name)
        assert prior.basis in ("dish_knowledge", "suspected_nuts")
        assert prior.risk >= 0.5, f"{name} risk too low: {prior.risk}"


def test_coconut_macaroon_does_not_read_as_almond_macaron():
    # 'macaron' is a substring of 'macaroon'; a coconut macaroon must not become a nut dish.
    prior = score_menu_item_prior(item_name="Coconut Macaroon")
    assert prior.basis != "dish_knowledge"
