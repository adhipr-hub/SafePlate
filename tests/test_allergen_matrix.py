from __future__ import annotations

import unittest

from safeplate.allergen_matrix import (
    extract_items_from_allergen_matrix,
    looks_like_allergen_matrix,
    _header_allergen,
)
from safeplate.soup import make_soup


MATRIX_HTML = """
<html><body>
<table>
  <thead>
    <tr>
      <th>Menu Item</th><th>Peanut</th><th>Tree Nuts</th><th>Milk</th>
      <th>Egg</th><th>Soy</th><th>Gluten</th>
    </tr>
  </thead>
  <tbody>
    <tr><td>Pad Thai</td><td>X</td><td></td><td></td><td>X</td><td>X</td><td></td></tr>
    <tr><td>Green Salad</td><td></td><td>-</td><td></td><td></td><td></td><td></td></tr>
    <tr><td>Cheeseburger</td><td>No</td><td>No</td><td>&#10003;</td><td>&#10003;</td><td>No</td><td>&#10003;</td></tr>
  </tbody>
</table>
</body></html>
"""

NUTRITION_HTML = """
<html><body>
<table>
  <thead><tr><th>Item</th><th>Calories</th><th>Total Fat</th><th>Sodium</th><th>Carbs</th></tr></thead>
  <tbody><tr><td>Fries</td><td>300</td><td>15g</td><td>200mg</td><td>40g</td></tr></tbody>
</table>
</body></html>
"""


class AllergenMatrixTests(unittest.TestCase):
    def test_parses_dish_allergen_grid(self) -> None:
        records = extract_items_from_allergen_matrix(MATRIX_HTML)
        by_name = {r.item_name: r.allergen_terms for r in records}

        self.assertEqual(by_name.get("Pad Thai"), ["egg", "peanut", "soy"])
        self.assertEqual(by_name.get("Green Salad"), [])
        self.assertEqual(by_name.get("Cheeseburger"), ["egg", "gluten", "milk"])

    def test_records_use_authoritative_method_and_no_price(self) -> None:
        records = extract_items_from_allergen_matrix(MATRIX_HTML)
        pad_thai = next(r for r in records if r.item_name == "Pad Thai")
        self.assertEqual(pad_thai.extraction_method, "allergen_matrix")
        self.assertEqual(pad_thai.price, "")
        self.assertGreaterEqual(pad_thai.confidence, 0.85)

    def test_nutrition_table_is_not_treated_as_matrix(self) -> None:
        self.assertEqual(extract_items_from_allergen_matrix(NUTRITION_HTML), [])
        self.assertFalse(looks_like_allergen_matrix(make_soup(NUTRITION_HTML)))

    def test_looks_like_allergen_matrix_detects_grid(self) -> None:
        self.assertTrue(looks_like_allergen_matrix(make_soup(MATRIX_HTML)))

    def test_header_alias_mapping_and_coconut_guard(self) -> None:
        self.assertEqual(_header_allergen("Peanut"), "peanut")
        self.assertEqual(_header_allergen("Tree Nuts"), "tree nut")
        self.assertEqual(_header_allergen("Shellfish"), "shellfish")
        self.assertEqual(_header_allergen("Fish"), "fish")
        self.assertEqual(_header_allergen("Gluten / Wheat"), "gluten")
        # "Coconut" contains "nut" but must not map to a tree nut.
        self.assertIsNone(_header_allergen("Coconut"))
        self.assertIsNone(_header_allergen("Calories"))

    def test_multilingual_header_aliases(self) -> None:
        # German
        self.assertEqual(_header_allergen("Milch"), "milk")
        self.assertEqual(_header_allergen("Weizen"), "wheat")
        self.assertEqual(_header_allergen("Erdnüsse"), "peanut")
        self.assertEqual(_header_allergen("Sellerie"), "celery")
        self.assertEqual(_header_allergen("Haselnuss"), "tree nut")
        # French / Spanish / Italian
        self.assertEqual(_header_allergen("Lait"), "milk")
        self.assertEqual(_header_allergen("Œufs"), "egg")
        self.assertEqual(_header_allergen("Moutarde"), "mustard")
        self.assertEqual(_header_allergen("Pescado"), "fish")
        self.assertEqual(_header_allergen("Frutta a guscio"), "tree nut")

    def test_multilingual_no_false_friend(self) -> None:
        # The German wheat header must NOT be mis-read as egg via a naive "ei" alias.
        self.assertEqual(_header_allergen("Weizen"), "wheat")

    def test_german_allergen_table_end_to_end(self) -> None:
        html = """<table><thead><tr>
            <th>Gericht</th><th>Milch</th><th>Eier</th><th>Weizen</th>
            <th>Erdnuss</th><th>Sellerie</th></tr></thead>
          <tbody><tr><td>Pizza Margherita</td><td>x</td><td></td><td>x</td>
            <td></td><td></td></tr></tbody></table>"""
        records = extract_items_from_allergen_matrix(html)
        pizza = next(r for r in records if r.item_name == "Pizza Margherita")
        self.assertIn("milk", pizza.allergen_terms)
        self.assertIn("wheat", pizza.allergen_terms)
        self.assertNotIn("peanut", pizza.allergen_terms)


if __name__ == "__main__":
    unittest.main()
