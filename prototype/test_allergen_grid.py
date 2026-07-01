"""Tests for the prototype multilingual allergen lexicon + DOM grid scraper."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prototype.allergen_grid import canonical_allergens, extract_from_allergen_grid


def _by_name(items):
    return {i.item_name: set(i.allergen_terms) for i in items}


# --- (b) multilingual lexicon ------------------------------------------------
def test_canonical_german():
    assert canonical_allergens("Milch, Soja, Weizen") == {"milk", "soy", "wheat"}


def test_canonical_french():
    assert {"milk", "egg", "gluten"} <= canonical_allergens("lait, œuf, gluten")


def test_canonical_japanese():
    assert {"milk", "soy", "wheat"} <= canonical_allergens("牛乳 大豆 小麦")


def test_canonical_eu_extras():
    assert canonical_allergens("Celery, Mustard, Sulphur dioxide, Lupin") == {
        "celery", "mustard", "sulphites", "lupin"
    }


def test_canonical_word_boundary_no_false_positive():
    # "ei" (German egg) must NOT match inside "protein" / "weight".
    assert "egg" not in canonical_allergens("high protein, net weight 200g")


def test_canonical_vertical_cjk_headers():
    # Japanese allergen tables stack characters vertically (newline between chars) --
    # whitespace must be ignored for CJK so "small wheat" still matches.
    assert canonical_allergens("小\n麦") == {"wheat"}
    assert "shrimp" in canonical_allergens("エ\nビ")  # katakana shrimp
    assert "crab" in canonical_allergens("か\nに")


# --- (a) DOM allergen-grid scraper -------------------------------------------
def test_grid_english_table():
    html = """<table>
      <tr><th>Dish</th><th>Milk</th><th>Egg</th><th>Gluten</th><th>Soy</th></tr>
      <tr><td>Chicken Ramen</td><td>✓</td><td></td><td>✓</td><td>✓</td></tr>
      <tr><td>Green Salad</td><td></td><td></td><td></td><td></td></tr>
    </table>"""
    by = _by_name(extract_from_allergen_grid(html))
    assert by["Chicken Ramen"] == {"milk", "gluten", "soy"}
    assert "Green Salad" not in by  # no allergens -> not emitted


def test_grid_german_table_letter_marks():
    html = """<table>
      <tr><th>Gericht</th><th>Milch</th><th>Ei</th><th>Weizen</th></tr>
      <tr><td>Pizza Margherita</td><td>x</td><td></td><td>x</td></tr>
    </table>"""
    by = _by_name(extract_from_allergen_grid(html))
    assert by["Pizza Margherita"] == {"milk", "wheat"}


def test_grid_aria_role_table():
    html = """<div role="table">
      <div role="row"><span role="columnheader">Item</span>
        <span role="columnheader">Milk</span><span role="columnheader">Peanut</span></div>
      <div role="row"><span role="cell">Pad Thai</span>
        <span role="cell"></span><span role="cell">●</span></div>
    </div>"""
    by = _by_name(extract_from_allergen_grid(html))
    assert by["Pad Thai"] == {"peanut"}


def test_grid_icon_cells():
    # presence shown by an <img> (no text); empty cell = absent.
    html = """<table>
      <tr><th>Name</th><th>Milk</th><th>Soy</th></tr>
      <tr><td>Tofu Bowl</td><td></td><td><img alt="contains" src="x.svg"></td></tr>
    </table>"""
    by = _by_name(extract_from_allergen_grid(html))
    assert by["Tofu Bowl"] == {"soy"}


def test_grid_not_an_allergen_table():
    html = "<table><tr><th>Name</th><th>Price</th></tr><tr><td>Fries</td><td>$3</td></tr></table>"
    assert extract_from_allergen_grid(html) == []


def test_grid_long_text_is_not_a_mark():
    # A misaligned column (e.g. a crust-type name) must NOT be read as a positive
    # allergen mark -- only short symbols / known marks count. (Domino's JP bug.)
    html = """<table>
      <tr><th>Dish</th><th>Milk</th><th>Egg</th></tr>
      <tr><td>Garlic Shrimp</td><td>Hand Toss Crust</td><td>✓</td></tr>
    </table>"""
    by = _by_name(extract_from_allergen_grid(html))
    assert by["Garlic Shrimp"] == {"egg"}      # NOT milk (the long crust text)


def test_grid_uniform_single_allergen_is_suppressed():
    # If every dish parses to the SAME single allergen, it's almost certainly a
    # column-misalignment artifact -> distrust the whole grid (safety: no false data).
    rows = "".join(
        f"<tr><td>Dish {i}</td><td>crust text {i}</td><td></td></tr>" for i in range(6)
    )
    html = f"<table><tr><th>Item</th><th>Milk</th><th>Egg</th></tr>{rows}</table>"
    assert extract_from_allergen_grid(html) == []
