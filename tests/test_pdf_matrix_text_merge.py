"""A TEXT-based allergen/ingredient PDF (e.g. Pressed Juicery's, ~117 products with a
facility cross-contact note) is UNDER-read by the Gemini vision matrix path (it found
22). The vision result must no longer SUPPRESS the plain-text LLM: run both and UNION,
with the matrix's allergen-bearing rows winning on dedupe, so the full catalog is
recovered WITHOUT losing allergen data (no tier regression)."""

from unittest import mock

from safeplate.extraction2 import pipeline
from safeplate.extraction2 import interpret_llm
from safeplate.extraction2.schema import Payload, PayloadKind, Policy
from safeplate.menu_text import MenuItemRecord


def _rec(name, allergens=()):
    return MenuItemRecord(
        restaurant_name="Pressed", restaurant_source_id="", menu_source_url="",
        category="", item_name=name, description="", price="",
        dietary_terms=[], allergen_terms=list(allergens), source_type="pdf",
        extraction_method=("gemini_allergen_matrix" if allergens else "gemini_text"),
        confidence=0.6, raw_text=name, fetched_at="",
    )


def _pdf_payload():
    return Payload(url="https://x.test/allergens.pdf", source_type="pdf",
                   kind=PayloadKind.TEXT,
                   text="Allergen info. Beauty Tonic. Carrot Juice. Greens 3.",
                   content=b"%PDF fake")


def test_allergen_pdf_unions_matrix_and_text_keeping_allergens():
    matrix_items = [_rec("Non-Dairy Almond Milk Chocolate", allergens=["tree nut"])]
    text_items = [
        _rec("Non-Dairy Almond Milk Chocolate"),  # dup of matrix -> matrix wins
        _rec("Beauty Tonic"),
        _rec("Carrot Juice"),
        _rec("Greens 3"),
    ]
    with mock.patch.object(pipeline, "interpret_structured", return_value=[]), \
         mock.patch.object(interpret_llm, "interpret_pdf_matrix", return_value=matrix_items), \
         mock.patch.object(interpret_llm, "interpret_text",
                           return_value=(text_items, False, 2)), \
         mock.patch.object(pipeline, "verify", side_effect=lambda items, p, require_grounding: (items, [])):
        result = pipeline.extract_menu(
            [_pdf_payload()], policy=Policy.HYBRID, llm_enabled=True, gemini_api_key="k"
        )

    names = {i.item_name for i in result.items}
    # Full catalog recovered (matrix ∪ text), not just the 1 matrix row.
    assert names == {"Non-Dairy Almond Milk Chocolate", "Beauty Tonic",
                     "Carrot Juice", "Greens 3"}
    # Allergen data preserved: the matrix row keeps its tree-nut flag (won the dedupe).
    almond = next(i for i in result.items if i.item_name == "Non-Dairy Almond Milk Chocolate")
    assert almond.allergen_terms == ["tree nut"]
    # Accounting: 1 vision + 2 text chunk calls.
    assert result.llm_calls == 3
