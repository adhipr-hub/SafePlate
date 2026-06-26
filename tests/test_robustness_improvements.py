"""Regression tests for the robustness improvements (per-nut matrix columns,
definitional grounded nut terms, added nut desserts with collision guards)."""
from __future__ import annotations

from safeplate.allergen_matrix import extract_items_from_allergen_matrix
from safeplate.allergen_score import (
    Severity,
    UserProfile,
    _nut_terms_present,
    score_restaurant_for_user,
)
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


# --- C3: US locales named like countries / written with full state names ----------
def test_region_disambiguates_us_state_vs_country():
    from safeplate.allergen_prior import region_from_address as region
    assert region("Tbilisi, Georgia") == "GE"  # country preserved
    assert region("123 Peachtree St, Atlanta, Georgia 30301") == "US"  # state + ZIP
    assert region("Savannah, Georgia, USA") == "US"
    assert region("Portland, Oregon") == "US"  # full state name, was 'unknown'
    assert region("Sydney, Australia") == "AU"
    assert region("10115 Berlin, Germany") == "DE"


# --- C0: a chart with NO nut column must not be read as "no nuts here" -------------
_NO_NUT_COLUMN_MATRIX = """
<table><thead><tr><th>Dish</th><th>Milk</th><th>Egg</th><th>Gluten</th></tr></thead>
<tbody>
  <tr><td>House Bowl</td><td>✓</td><td></td><td>✓</td></tr>
  <tr><td>Garden Plate</td><td></td><td></td><td></td></tr>
</tbody></table>
"""
_WITH_NUT_COLUMN_MATRIX = """
<table><thead><tr><th>Dish</th><th>Peanut</th><th>Milk</th><th>Egg</th></tr></thead>
<tbody>
  <tr><td>House Bowl</td><td></td><td>✓</td><td></td></tr>
  <tr><td>Garden Plate</td><td></td><td></td><td></td></tr>
</tbody></table>
"""


def _assess(html):
    items = extract_items_from_allergen_matrix(html)
    profile = UserProfile.for_nuts(severity=Severity.ALLERGY)
    return score_restaurant_for_user(profile, cuisines=None, region="GB", menu_items=items)


def test_matrix_without_nut_column_does_not_vouch_for_nut_safety():
    a = _assess(_NO_NUT_COLUMN_MATRIX)
    # The milk/egg/gluten chart says nothing about nuts -> must not trigger the
    # "allergen chart present, nuts not listed" clean pull-down to likely_ok.
    assert a.tier != "likely_ok"
    assert a.evidence_basis != "allergen_matrix"


def test_matrix_with_nut_column_still_vouches_when_unmarked():
    a = _assess(_WITH_NUT_COLUMN_MATRIX)
    # A chart that DOES have a peanut column and marks no nut anywhere is real evidence
    # of absence -> the clean pull-down applies (basis allergen_matrix).
    assert a.evidence_basis == "allergen_matrix"


# --- Q3: one registrable-domain helper shared across modules (co.uk aware) ---------
def test_registrable_domain_helpers_agree():
    from safeplate.extraction2.discover import _registrable_domain as d
    from safeplate.brave_search import _registrable_domain as b
    from safeplate.allergy_registry import _registrable as a
    from safeplate.textutil import registrable_domain
    for host, expected in [
        ("shop.foo.co.uk", "foo.co.uk"),     # two-level TLD kept whole
        ("orders.bar.com", "bar.com"),
        ("www.baz.com.au", "baz.com.au"),
        ("Brand.COM", "brand.com"),
    ]:
        assert registrable_domain(host) == expected
        assert d(host) == b(host) == a(host) == expected
