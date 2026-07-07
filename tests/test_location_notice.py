from types import SimpleNamespace
from safeplate.menu_service import _location_notice_for

CUP = "20010 Stevens Creek Blvd, Cupertino, CA 95014, USA"
SM_PDF = ("https://www.sweetmaplesf.com/files/"
          "02-28-2026-sweet-maple-santa-monica-menu-02-27-2026-pdf.pdf")
CUP_PAGE = "https://www.sweetmaplesf.com/menu-cupertino"


def _item(url):
    return SimpleNamespace(menu_source_url=url, allergen_terms=["nuts"])


def _cov(url):
    return SimpleNamespace(url=url, region="")


def test_labeled_mismatch_when_used_source_names_other_city():
    n = _location_notice_for([_cov(SM_PDF)], [_item(SM_PDF)],
                             address=CUP, restaurant_name="Sweet Maple")
    assert n == {"verified": False, "shownCity": "Santa Monica",
                 "homeCity": "Cupertino", "confidence": "labeled"}


def test_no_notice_when_used_source_is_home_city():
    n = _location_notice_for([_cov(CUP_PAGE)], [_item(CUP_PAGE)],
                             address=CUP, restaurant_name="Sweet Maple")
    assert n is None


def test_inferred_when_home_menu_discovered_but_not_used():
    # A Cupertino page was discovered (coverage) but items came from an unlabeled
    # source that isn't the Cupertino menu.
    used = "https://www.sweetmaplesf.com/files/menu.pdf"
    n = _location_notice_for([_cov(CUP_PAGE), _cov(used)], [_item(used)],
                             address=CUP, restaurant_name="Sweet Maple")
    assert n == {"verified": False, "shownCity": "",
                 "homeCity": "Cupertino", "confidence": "inferred"}


def test_no_notice_without_address():
    assert _location_notice_for([_cov(SM_PDF)], [_item(SM_PDF)],
                                address="", restaurant_name="Sweet Maple") is None
