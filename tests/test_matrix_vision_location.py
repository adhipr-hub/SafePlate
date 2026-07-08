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


import time

import pytest

from safeplate import cache_store
from safeplate.extraction2 import interpret_llm


def _pdf_payload():
    # Mirror how existing tests build a PDF Payload (see tests/test_pdfplumber_gating.py
    # for the constructor convention); adjust field names to the real Payload dataclass.
    from safeplate.extraction2.schema import Payload, PayloadKind
    return Payload(url="https://x.example/allergens.pdf", kind=PayloadKind.VISUAL,
                   source_type="pdf", text="allergen chart", content=b"%PDF-fake")


def test_pdf_matrix_key_uses_v2_prefix(monkeypatch):
    seen = {}
    monkeypatch.setattr(interpret_llm.cache_store, "load",
                        lambda ns, key: seen.setdefault("key", key))
    with pytest.raises(Exception):
        # load returns a str (not a blob) -> downstream will fail; we only
        # care that the KEY was computed with the new prefix before that.
        interpret_llm.interpret_pdf_matrix(_pdf_payload(), api_key="k", model="m")
    import hashlib
    expected = hashlib.sha1(b"pdfmatrix2:" + b"m" + b":" + b"%PDF-fake").hexdigest()
    assert seen["key"] == expected


def test_pdf_matrix_cache_hit_returns_location_texts(monkeypatch):
    blob = {"at": time.time(), "items": [], "location_texts": ["Sydney NSW"]}
    monkeypatch.setattr(interpret_llm.cache_store, "load", lambda ns, key: blob)
    items, texts = interpret_llm.interpret_pdf_matrix(_pdf_payload(), api_key="k", model="m")
    assert texts == ["Sydney NSW"]


def test_pdf_matrix_old_blob_without_field(monkeypatch):
    blob = {"at": time.time(), "items": []}
    monkeypatch.setattr(interpret_llm.cache_store, "load", lambda ns, key: blob)
    items, texts = interpret_llm.interpret_pdf_matrix(_pdf_payload(), api_key="k", model="m")
    assert items == [] and texts == []


def test_pdf_matrix_saves_location_texts(monkeypatch, tmp_path):
    from safeplate.menu_text import MenuItemRecord

    monkeypatch.setattr(interpret_llm.cache_store, "load", lambda ns, key: None)
    saved = {}
    monkeypatch.setattr(interpret_llm.cache_store, "save",
                        lambda ns, key, blob: saved.update(blob=blob))
    fake_item = MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="",
        category="", item_name="Burger", description="", price="",
        dietary_terms=[], allergen_terms=[],
        source_type="", extraction_method="", confidence=0.9,
        raw_text="", fetched_at="",
    )
    monkeypatch.setattr(
        "safeplate.menu_fetch_llm.extract_allergen_matrix_via_gemini_pdf",
        lambda *a, **k: ([fake_item], ["12 Foo St, Sydney"]),
    )
    items, texts = interpret_llm.interpret_pdf_matrix(_pdf_payload(), api_key="k", model="m")
    assert texts == ["12 Foo St, Sydney"]
    assert saved["blob"]["location_texts"] == ["12 Foo St, Sydney"]


def _matrix_pdf_payload():
    # The pipeline's matrix branch requires a NON-VISUAL payload (VISUAL returns
    # early via interpret_visual, never reaching the matrix check) with
    # source_type="pdf" and allergen-y text so _looks_allergen fires. Task 2's
    # _pdf_payload() (VISUAL) is deliberately left untouched.
    from safeplate.extraction2.schema import Payload, PayloadKind
    return Payload(url="https://x.example/allergens.pdf", kind=PayloadKind.TEXT,
                   source_type="pdf", text="allergen chart", content=b"%PDF-fake")


def _default_policy():
    # Mirror tests/test_llm_call_accounting.py's extract_menu call shape.
    from safeplate.extraction2.schema import Policy
    return Policy.HYBRID


def _matrix_item():
    from safeplate.menu_text import MenuItemRecord
    return MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="",
        category="", item_name="Burger", description="", price="",
        dietary_terms=[], allergen_terms=["milk"], source_type="",
        extraction_method="gemini_pdf_matrix", confidence=0.9,
        raw_text="", fetched_at="",
    )


def test_matrix_location_text_stamps_coverage_region(monkeypatch):
    from safeplate.extraction2 import pipeline

    fake_item = _matrix_item()
    # The footer-URL snippet ("shakeshack.com.au") is what the unchanged
    # detect_source_region keys on -- single-word country names ("Australia")
    # are deliberately excluded as too noisy (see extraction2/region.py).
    monkeypatch.setattr(
        pipeline.interpret_llm, "interpret_pdf_matrix",
        lambda p, **k: ([fake_item], ["Shake Shack Australia Pty Ltd, Sydney NSW",
                                      "shakeshack.com.au"]),
    )
    # Text LLM finds nothing net-new -> the matrix-only return path is taken.
    monkeypatch.setattr(pipeline.interpret_llm, "interpret_text",
                        lambda p, **k: ([], False, 0))
    payload = _matrix_pdf_payload()  # pdf + allergen-y text -> matrix branch fires
    result = pipeline.extract_menu(
        [payload], policy=_default_policy(), llm_enabled=True,
        gemini_api_key="k", gemini_model="m",
    )
    assert result.coverage[0].region == "AU"


def test_no_location_text_keeps_todays_stamp(monkeypatch):
    from safeplate.extraction2 import pipeline

    fake_item = _matrix_item()
    monkeypatch.setattr(pipeline.interpret_llm, "interpret_pdf_matrix",
                        lambda p, **k: ([fake_item], []))
    monkeypatch.setattr(pipeline.interpret_llm, "interpret_text",
                        lambda p, **k: ([], False, 0))
    result = pipeline.extract_menu(
        [_matrix_pdf_payload()], policy=_default_policy(), llm_enabled=True,
        gemini_api_key="k", gemini_model="m",
    )
    assert result.coverage[0].region == ""  # "allergen chart" text has no region tell


def test_location_snippets_never_become_items(monkeypatch):
    # Spec req 4: snippets are provenance hints only. Even dish-like snippet
    # text must not appear among extracted items.
    from safeplate.extraction2 import pipeline

    fake_item = _matrix_item()
    monkeypatch.setattr(
        pipeline.interpret_llm, "interpret_pdf_matrix",
        lambda p, **k: ([fake_item], ["Peanut Chicken Special, 5 Sydney Rd"]),
    )
    monkeypatch.setattr(pipeline.interpret_llm, "interpret_text",
                        lambda p, **k: ([], False, 0))
    result = pipeline.extract_menu(
        [_matrix_pdf_payload()], policy=_default_policy(), llm_enabled=True,
        gemini_api_key="k", gemini_model="m",
    )
    assert [i.item_name for i in result.items] == ["Burger"]


def test_result_cache_version_bumped_for_location_capture():
    # Protects the user's cache-clear decision (spec §Requirements 6): all
    # pre-location-capture results must re-extract.
    from safeplate.extraction2 import discover
    assert discover._RESULT_CACHE_VERSION == "7"


if __name__ == "__main__":
    unittest.main()
