from __future__ import annotations

import json
import unittest

from safeplate.embedded_json import extract_items_from_embedded_json
from safeplate.menu_fetch_llm import (
    _build_payload,
    _records_from_payload,
    extract_items_via_gemini_image,
    extract_items_via_gemini_url,
)


class EmbeddedJsonTests(unittest.TestCase):
    def test_recovers_items_from_next_data_blob(self) -> None:
        data = {
            "props": {
                "pageProps": {
                    "menu": {
                        "sections": [
                            {
                                "name": "Udon",
                                "items": [
                                    {"name": "Spicy Beef Udon", "price": "12.95",
                                     "description": "udon, beef, scallion"},
                                    {"name": "Tempura Udon", "price": 1395},
                                ],
                            }
                        ]
                    }
                }
            }
        }
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(data)
            + "</script></body></html>"
        )
        rows = extract_items_from_embedded_json(html)
        names = {r.item_name: r.price for r in rows}
        self.assertEqual(names.get("Spicy Beef Udon"), "$12.95")
        # Integer cents are normalized to dollars.
        self.assertEqual(names.get("Tempura Udon"), "$13.95")
        self.assertTrue(all(r.extraction_method == "embedded_json" for r in rows))

    def test_ignores_schema_org_jsonld_and_objects_without_price(self) -> None:
        html = (
            '<script type="application/ld+json">'
            '{"@type":"Menu","name":"ignored"}</script>'
            '<script type="application/json">'
            '{"widgets":[{"name":"Hero Banner"},{"title":"About Us"}]}</script>'
        )
        # ld+json is left to the dedicated extractor; objects with no price are skipped.
        self.assertEqual(extract_items_from_embedded_json(html), [])


class GeminiUrlContextTests(unittest.TestCase):
    def test_no_api_key_returns_empty(self) -> None:
        self.assertEqual(
            extract_items_via_gemini_url("https://x.com/menu", api_key=None), []
        )

    def test_payload_uses_url_context_tool_and_includes_url(self) -> None:
        payload = _build_payload("https://x.com/menu")
        self.assertIn({"url_context": {}}, payload["tools"])
        self.assertIn("https://x.com/menu", payload["contents"][0]["parts"][0]["text"])
        self.assertIn("responseJsonSchema", payload["generationConfig"])

    def test_image_extraction_no_key_returns_empty(self) -> None:
        self.assertEqual(
            extract_items_via_gemini_image(b"fakebytes", content_type="image/png", api_key=None),
            [],
        )

    def test_records_method_override_for_image(self) -> None:
        parsed = {"page_had_menu": True, "menu_items": [
            {"item_name": "Cashew Stir Fry", "price": "150", "evidence_quote": "Cashew Stir Fry 150"}]}
        rows = _records_from_payload(parsed, "", "X", "id", extraction_method="gemini_image")
        self.assertEqual(rows[0].extraction_method, "gemini_image")

    def test_records_require_evidence_quote_and_cap_confidence(self) -> None:
        parsed = {
            "page_had_menu": True,
            "menu_items": [
                {"item_name": "Pad Thai", "price": "$14", "confidence": 0.99,
                 "evidence_quote": "Pad Thai $14", "allergen_mentions": ["peanut"]},
                {"item_name": "No Quote Item", "price": "$9"},  # dropped: no quote
            ],
        }
        rows = _records_from_payload(parsed, "https://x.com/menu", "X", "id1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].item_name, "Pad Thai")
        self.assertLessEqual(rows[0].confidence, 0.65)  # LLM reads are capped
        self.assertEqual(rows[0].allergen_terms, "peanut")
        self.assertEqual(rows[0].extraction_method, "gemini_url_context")


class PdfAllergenMatrixTests(unittest.TestCase):
    def test_pdfplumber_text_grid_maps_dishes_to_allergens(self) -> None:
        from safeplate.allergen_matrix import _records_from_text_grid
        grid = [
            ["Dish", "Peanut", "Milk", "Egg", "Soy", "Gluten"],
            ["Falafel Wrap", "", "", "", "X", "X"],
            ["Cashew Curry", "X", "Yes", "", "", ""],
        ]
        recs = {r.item_name: r.allergen_terms for r in _records_from_text_grid(grid, set())}
        self.assertEqual(recs["Falafel Wrap"], ["gluten", "soy"])
        self.assertEqual(recs["Cashew Curry"], ["milk", "peanut"])

    def test_text_grid_rejects_non_allergen_table(self) -> None:
        from safeplate.allergen_matrix import _records_from_text_grid
        nutrition = [["Item", "Calories", "Fat", "Sodium"], ["Fries", "300", "15", "200"]]
        self.assertEqual(_records_from_text_grid(nutrition, set()), [])

    def test_gemini_matrix_no_key_returns_empty(self) -> None:
        from safeplate.menu_fetch_llm import extract_allergen_matrix_via_gemini_pdf
        self.assertEqual(extract_allergen_matrix_via_gemini_pdf(b"%PDF-1.4", api_key=None), [])


if __name__ == "__main__":
    unittest.main()
