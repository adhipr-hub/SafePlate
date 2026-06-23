"""Tier 2: skip the slow pdfplumber table pass on PDFs that cannot contain a
readable allergen grid. The gate is a safe SUPERSET -- pdfplumber only emits a grid
when >=3 allergen column headers appear as text, so gating on >=3 allergen terms (or
an allergen/nutrition keyword) in the extracted text never drops a grid it could
find, while skipping the expensive extract_tables() on plain menu/policy PDFs."""

from __future__ import annotations

from unittest import mock

from safeplate.allergen_matrix import _pdf_text_could_have_allergen_grid
from safeplate.extraction2.interpret_structured import interpret_structured
from safeplate.extraction2.schema import Payload, PayloadKind
from safeplate.menu_text import MenuItemRecord


class GatePredicateTest:
    pass


def test_gate_true_with_three_allergen_terms():
    assert _pdf_text_could_have_allergen_grid("Contains milk, egg and peanuts")


def test_gate_true_with_allergen_keyword():
    assert _pdf_text_could_have_allergen_grid("Full allergen information below")


def test_gate_false_on_plain_prose():
    assert not _pdf_text_could_have_allergen_grid(
        "Our modern slavery statement and corporate policy for the financial year"
    )


def test_gate_false_with_only_two_allergen_terms():
    # Fewer than the >=3 distinct columns pdfplumber requires -> it could not grid it.
    assert not _pdf_text_could_have_allergen_grid("served with milk and egg")


def _pdf_payload(text: str) -> Payload:
    return Payload(url="https://x.test/menu.pdf", source_type="pdf",
                   kind=PayloadKind.TEXT, text=text, content=b"%PDF-1.4 fake")


def test_interpret_structured_skips_pdfplumber_without_allergen_hint():
    payload = _pdf_payload("Lunch specials and fresh sandwiches served daily")
    with mock.patch(
        "safeplate.allergen_matrix.extract_items_from_allergen_pdf",
        side_effect=AssertionError("pdfplumber must not run without an allergen hint"),
    ):
        assert interpret_structured(payload) == []


def test_interpret_structured_runs_pdfplumber_with_allergen_hint():
    payload = _pdf_payload("Allergen chart: milk, egg, peanut, soy, wheat")
    rec = MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="",
        category="", item_name="Pad Thai", description="", price="",
        dietary_terms=[], allergen_terms=["peanut"], source_type="pdf",
        extraction_method="allergen_matrix_pdf", confidence=0.6, raw_text="x",
        fetched_at="",
    )
    with mock.patch(
        "safeplate.allergen_matrix.extract_items_from_allergen_pdf",
        return_value=[rec],
    ) as m:
        items = interpret_structured(payload)
    m.assert_called_once()
    assert items == [rec]


def test_interpret_structured_runs_pdfplumber_when_text_empty():
    # Empty text layer (scanned PDF): we cannot rule out a grid, so still run it
    # (the call is cheap with no text and stays output-identical).
    payload = _pdf_payload("")
    with mock.patch(
        "safeplate.allergen_matrix.extract_items_from_allergen_pdf",
        return_value=[],
    ) as m:
        interpret_structured(payload)
    m.assert_called_once()
