"""Locks the exact Sweet Maple (Cupertino) defects found on 2026-07-06:
a wine-origin 'New Zealand' must NOT tag the US menu foreign, and the Santa Monica
fallback menu MUST raise a location-mismatch notice."""
from types import SimpleNamespace

from safeplate.extraction2.region import detect_source_region
from safeplate.menu_service import _location_notice_for

CUP = "20010 Stevens Creek Blvd, Cupertino, CA 95014, USA"
SM_PDF = ("https://www.sweetmaplesf.com/files/"
          "02-28-2026-sweet-maple-santa-monica-menu-02-27-2026-pdf.pdf")
WINE_TEXT = ("Matua, Sauvignon Blanc, New Zealand $14. "
             "The first New Zealand Sauvignon Blanc. 20010 Stevens Creek Blvd.")


def test_wine_origin_does_not_tag_region():
    assert detect_source_region(WINE_TEXT, SM_PDF) is None


def test_santa_monica_fallback_raises_location_notice():
    items = [SimpleNamespace(menu_source_url=SM_PDF, allergen_terms=["nuts"])]
    coverage = [SimpleNamespace(url=SM_PDF, region="")]
    n = _location_notice_for(coverage, items, address=CUP, restaurant_name="Sweet Maple")
    assert n["confidence"] == "labeled"
    assert n["shownCity"] == "Santa Monica"
    assert n["homeCity"] == "Cupertino"
    assert n["verified"] is False
