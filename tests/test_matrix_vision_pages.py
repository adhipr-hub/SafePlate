"""The allergen-matrix vision path must not silently cap at 6 pages: a long chain
allergen matrix (>6 pages) would otherwise drop allergen rows on pages 7+, a
safety-asymmetric recall loss."""

from __future__ import annotations

from unittest import mock

import pytest

from safeplate import menu_fetch_llm

fitz = pytest.importorskip("fitz")  # PyMuPDF, to synthesize a multi-page PDF
pytest.importorskip("pypdfium2")  # the renderer the vision path uses


def _make_pdf(n_pages: int) -> bytes:
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"PAGE_{i}")
    return doc.tobytes()


def test_matrix_vision_renders_all_pages_not_capped_at_six():
    pdf = _make_pdf(10)
    captured: dict[str, int] = {}

    def fake_matrix_call(payload, api_key, model):
        parts = payload["contents"][0]["parts"]
        captured["n_images"] = sum(1 for p in parts if "inline_data" in p)
        # Return a row + not-truncated so the batched call (which carries ALL
        # rendered pages) short-circuits and we measure the real page count.
        return [{"dish": "X", "allergens": ["milk"]}], False

    with mock.patch.object(menu_fetch_llm, "_matrix_call", side_effect=fake_matrix_call):
        menu_fetch_llm.extract_allergen_matrix_via_gemini_pdf(
            pdf, api_key="k", model="m"
        )

    assert captured.get("n_images") == 10
