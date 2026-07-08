"""Vision matrix read also transcribes visible location text (spec:
docs/superpowers/specs/2026-07-07-vision-location-capture-design.md)."""
import unittest
from unittest import mock

from safeplate import menu_fetch_llm


class SanitizeLocationTextsTests(unittest.TestCase):
    def test_caps_dedupes_and_cleans(self):
        raw = ["  12 Foo St, Sydney NSW  ", "", 42, "12 Foo St, Sydney NSW",
               "x" * 500] + [f"snippet {i}" for i in range(10)]
        out = menu_fetch_llm._sanitize_location_texts(raw)
        self.assertEqual(out[0], "12 Foo St, Sydney NSW")   # stripped
        self.assertEqual(len(out), 8)                        # capped at 8
        self.assertEqual(len(out[1]), 120)                   # each capped at 120
        self.assertEqual(len(set(out)), len(out))            # deduped
        self.assertTrue(all(isinstance(s, str) for s in out))

    def test_non_list_is_empty(self):
        self.assertEqual(menu_fetch_llm._sanitize_location_texts(None), [])
        self.assertEqual(menu_fetch_llm._sanitize_location_texts("Sydney"), [])


class MatrixCallLocationTests(unittest.TestCase):
    def _response(self, parsed):
        return {"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": __import__("json").dumps(parsed)}]}}]}

    def test_matrix_call_returns_location_texts(self):
        parsed = {"rows": [{"dish": "Burger", "allergens": ["milk"]}],
                  "columns": ["milk"],
                  "visible_location_text": ["Shake Shack Australia Pty Ltd, Sydney NSW"]}
        with mock.patch.object(menu_fetch_llm, "_post_gemini_generate_content",
                               return_value=self._response(parsed)):
            rows, columns, truncated, texts = menu_fetch_llm._matrix_call(
                {"contents": []}, "key", "model")
        self.assertEqual(texts, ["Shake Shack Australia Pty Ltd, Sydney NSW"])
        self.assertEqual([r["dish"] for r in rows], ["Burger"])

    def test_matrix_call_tolerates_missing_field(self):
        parsed = {"rows": [], "columns": []}
        with mock.patch.object(menu_fetch_llm, "_post_gemini_generate_content",
                               return_value=self._response(parsed)):
            rows, columns, truncated, texts = menu_fetch_llm._matrix_call(
                {"contents": []}, "key", "model")
        self.assertEqual(texts, [])


class ExtractReturnShapeTests(unittest.TestCase):
    def test_no_key_returns_empty_tuple(self):
        items, texts = menu_fetch_llm.extract_allergen_matrix_via_gemini_pdf(
            b"%PDF-1.4", api_key=None)
        self.assertEqual(items, [])
        self.assertEqual(texts, [])

    def test_schema_declares_field_optional(self):
        props = menu_fetch_llm.ALLERGEN_MATRIX_SCHEMA["properties"]
        self.assertIn("visible_location_text", props)
        self.assertNotIn("visible_location_text",
                         menu_fetch_llm.ALLERGEN_MATRIX_SCHEMA["required"])


if __name__ == "__main__":
    unittest.main()
