from safeplate.extraction2 import locality as L

SANTA_MONICA = ("https://www.sweetmaplesf.com/files/"
                "02-28-2026-sweet-maple-santa-monica-menu-02-27-2026-pdf.pdf")
CUPERTINO_ADDR = "20010 Stevens Creek Blvd, Cupertino, CA 95014, USA"


def test_city_from_address():
    assert L.city_from_address(CUPERTINO_ADDR) == "cupertino"
    assert L.city_from_address("Palo Alto, CA") == "palo-alto"
    assert L.city_from_address("") is None
    assert L.city_from_address("SingleField") is None


def test_source_city_slug_extracts_place_dropping_name_and_descriptors():
    assert L.source_city_slug(SANTA_MONICA, "Sweet Maple") == "santa-monica"
    assert L.source_city_slug(
        "https://x.com/menu-cupertino", "Sweet Maple") == "cupertino"
    # pure descriptor filename -> no city
    assert L.source_city_slug("https://x.com/files/dinner-menu.pdf", "Sweet Maple") is None


def test_url_has_city():
    assert L.url_has_city("https://x.com/menu-cupertino", "cupertino") is True
    assert L.url_has_city(SANTA_MONICA, "cupertino") is False


def test_menu_city_mismatch():
    # wrong-location PDF for a Cupertino diner -> mismatch
    assert L.menu_city_mismatch(SANTA_MONICA, CUPERTINO_ADDR, "Sweet Maple") is True
    # the diner's own city menu -> not a mismatch
    assert L.menu_city_mismatch(
        "https://www.sweetmaplesf.com/menu-cupertino", CUPERTINO_ADDR, "Sweet Maple") is False
    # unreadable city -> never assert a mismatch
    assert L.menu_city_mismatch("https://x.com/files/menu.pdf", CUPERTINO_ADDR, "Sweet Maple") is False
    assert L.menu_city_mismatch(SANTA_MONICA, "", "Sweet Maple") is False
