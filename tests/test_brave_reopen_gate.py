from types import SimpleNamespace
from safeplate.extraction2.discover import _used_menu_city_mismatch

CUP = "20010 Stevens Creek Blvd, Cupertino, CA 95014, USA"
SM_PDF = ("https://www.sweetmaplesf.com/files/"
          "02-28-2026-sweet-maple-santa-monica-menu-02-27-2026-pdf.pdf")
CUP_PAGE = "https://www.sweetmaplesf.com/menu-cupertino"


def _items(url):
    return [SimpleNamespace(item_name="x", menu_source_url=url)]


def test_used_menu_city_mismatch_true_for_wrong_city():
    assert _used_menu_city_mismatch(_items(SM_PDF), CUP, "Sweet Maple") is True


def test_used_menu_city_mismatch_false_for_home_city():
    assert _used_menu_city_mismatch(_items(CUP_PAGE), CUP, "Sweet Maple") is False


def test_used_menu_city_mismatch_false_without_address():
    assert _used_menu_city_mismatch(_items(SM_PDF), None, "Sweet Maple") is False
