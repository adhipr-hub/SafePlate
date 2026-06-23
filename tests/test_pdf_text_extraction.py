from __future__ import annotations

import time
from unittest import mock

import pytest

from safeplate import timing
from safeplate.menu_text import _PDF_MAX_PAGES, _pdf_text_from_bytes

fitz = pytest.importorskip("fitz")  # PyMuPDF


def _make_pdf(n_pages: int) -> bytes:
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"PAGE_MARKER_{i}")
    return doc.tobytes()


def test_extracts_text_from_pdf_bytes():
    text = _pdf_text_from_bytes(_make_pdf(3))
    assert "PAGE_MARKER_0" in text
    assert "PAGE_MARKER_2" in text


def test_page_cap_truncates_giant_pdf_quickly():
    # A PDF far larger than the cap must parse only the first _PDF_MAX_PAGES pages,
    # and do it fast (the guard that stops a huge nutrition PDF stalling a worker).
    pdf = _make_pdf(_PDF_MAX_PAGES + 30)
    t0 = time.perf_counter()
    text = _pdf_text_from_bytes(pdf)
    elapsed = time.perf_counter() - t0

    assert f"PAGE_MARKER_{_PDF_MAX_PAGES - 1}" in text       # last allowed page present
    assert f"PAGE_MARKER_{_PDF_MAX_PAGES}" not in text       # first capped page absent
    assert f"PAGE_MARKER_{_PDF_MAX_PAGES + 29}" not in text  # final page absent
    assert elapsed < 2.0                                     # bounded, not a stall


def test_bad_bytes_return_empty_not_raise():
    assert _pdf_text_from_bytes(b"not a pdf at all") == ""


def test_pdf_parse_records_timing_span():
    # With timing enabled, the PDF parse stage must be observable so the bench can
    # attribute wall-clock to it.
    with mock.patch.object(timing, "_ENABLED", True):
        timing.reset()
        _pdf_text_from_bytes(_make_pdf(2))
        snap = timing.snapshot()
    assert "pdf_parse" in snap
    assert snap["pdf_parse"]["count"] == 1
