"""Content-locale validation: detect the region a menu/allergen source came from
and describe it relative to the diner's region."""
from safeplate.extraction2 import region as R


# --- detection from URL ccTLD (decisive) -------------------------------------

def test_detect_from_cctld():
    assert R.detect_source_region("", "https://burgerking.com.mt/allergens.pdf") == "MT"
    assert R.detect_source_region("anything", "https://starbucks.ch/a.pdf") == "CH"
    assert R.detect_source_region("", "https://nandos.co.uk/x.pdf") == "GB"


def test_detect_neutral_host_no_text_is_none():
    assert R.detect_source_region("", "https://cdn.example.com/menu.pdf") is None


# --- detection from content (the neutral-CDN case the ccTLD guard misses) -----

def test_detect_nz_from_content_on_neutral_host():
    # A NZ Burger King allergen PDF served from an Azure blob (country-neutral host)
    # -- the footer cites the .co.nz site, which is a decisive NZ tell.
    text = "Burger King allergen guide. Visit burgerking.co.nz for more. Whopper, fries."
    url = "https://bknzpublic.z8.web.core.windows.net/allergens.pdf"
    assert R.detect_source_region(text, url) == "NZ"


def test_detect_domain_in_text():
    assert R.detect_source_region("order at example.com.au", "https://cdn.x.com/n.pdf") == "AU"
    assert R.detect_source_region("see menu.example.in for more", "https://cdn.x.com/n.pdf") == "IN"


def test_detect_strong_multiword_name():
    text = "Allergen guide — proudly made in New Zealand."
    assert R.detect_source_region(text, "https://cdn.x.com/n.pdf") == "NZ"


# --- the substring-collision regressions the code review caught ---------------
# Bare ccTLD substrings (.ph/.jp/.tw/.it/.in/.de/.fr/...) must NOT match inside
# everyday URL/asset/word fragments. Each of these would previously misfire.

def test_detect_ignores_url_and_asset_fragments():
    cases = [
        "Allergen chart. Order at order.php now.",          # .php != .ph (Philippines)
        "Banner hero.jpg and logo.jpeg below.",             # .jpg/.jpeg != .jp (Japan)
        "Follow pic.twitter.com/abc for updates.",          # twitter != .tw (Taiwan)
        '{"menu.items": []}',                               # .items != .it (Italy)
        "Powered by getmenu.info platform.",                # .info != .in (India)
        "Order on cdn.deliveroo.com today.",                # deliveroo != .de (Germany)
    ]
    for text in cases:
        assert R.detect_source_region(text, "https://cdn.x.com/n.pdf") is None, text


def test_detect_incidental_country_name_does_not_fire():
    # Cuisine/incidental country WORDS are too noisy to assert a region on their own.
    for text in ["Cheese imported from Italy.", "Great India Pale Ale.",
                 "Authentic Japanese cuisine.", "Australian wagyu beef."]:
        assert R.detect_source_region(text, "https://cdn.x.com/n.pdf") is None, text


def test_detect_incidental_mention_does_not_beat_domain_tell():
    # An incidental "imported from Italy" must not outweigh a .co.uk domain tell.
    text = "Cheese imported from Italy. Order at example.co.uk today."
    assert R.detect_source_region(text, "https://cdn.x.com/n.pdf") == "GB"


# --- home country ------------------------------------------------------------

def test_home_country_from_address_and_cctld():
    assert R.home_country("5154 Moorpark Ave, San Jose, CA 95129, USA", "") == "US"
    assert R.home_country("Some St, Toronto", "https://timhortons.ca/") == "CA"
    assert R.home_country("", "https://example.com/") is None


def test_home_country_us_zip_without_country_word():
    # OSM addresses omit the country segment; a US state+ZIP tail still means US.
    assert R.home_country("123 Main St, Springfield, IL 62704", "") == "US"
    assert R.home_country("742 Evergreen Ter, Portland, OR 97086-1234", "") == "US"


def test_country_label():
    assert R.country_label("NZ") == "New Zealand"
    assert R.country_label("US") == "the United States"
    assert R.country_label("") == ""


# --- the UI notice -----------------------------------------------------------

def test_region_notice_foreign():
    n = R.region_notice(home="US", source_region="NZ")
    assert n["verified"] is False
    assert n["sourceRegion"] == "NZ"
    assert n["sourceLabel"] == "New Zealand"
    assert n["homeRegion"] == "US"


def test_region_notice_verified_when_match():
    n = R.region_notice(home="US", source_region="US")
    assert n["verified"] is True


def test_region_notice_warns_even_when_home_unknown():
    # If we can't tell the diner's region but the data is clearly from somewhere
    # specific, still warn (verified false) — silence would mean trusting it blindly.
    n = R.region_notice(home=None, source_region="NZ")
    assert n is not None and n["verified"] is False and n["sourceRegion"] == "NZ"


def test_region_notice_none_when_no_source():
    assert R.region_notice(home="US", source_region=None) is None
    assert R.region_notice(home=None, source_region=None) is None


# --- menu_service wiring: coverage region -> notice ---------------------------

def _cov(url, region, found=True, items=5):
    from safeplate.extraction2.schema import CoverageReport
    return CoverageReport(url=url, found=found, payload_kind="text", item_count=items,
                          interpreter="gemini_pdf_matrix", confidence=0.9,
                          reason="", region=region)


def _item(url, allergen=("peanut",)):
    import dataclasses
    from safeplate.menu_text import MenuItemRecord
    fields = {f.name: "" for f in dataclasses.fields(MenuItemRecord)}
    fields.update(item_name="Whopper", menu_source_url=url, dietary_terms=[],
                  allergen_terms=list(allergen), confidence=1.0)
    return MenuItemRecord(**fields)


def test_menu_service_flags_foreign_allergen_source():
    from safeplate.menu_service import _region_notice_for
    cov = [_cov("https://x.com/uk.pdf", "GB")]
    items = [_item("https://x.com/uk.pdf")]
    n = _region_notice_for(cov, items, address="1 A St, San Jose, CA, USA", website_url="")
    assert n is not None and n["verified"] is False and n["sourceRegion"] == "GB"


def test_menu_service_prefers_foreign_allergen_over_home_nonallergen():
    # The allergen data (the safety-critical part) is from GB even though a home-region
    # source also contributed (non-allergen) items -> the notice names GB.
    from safeplate.menu_service import _region_notice_for
    cov = [_cov("https://home.com/m.pdf", "US"), _cov("https://x.com/uk.pdf", "GB")]
    items = [_item("https://home.com/m.pdf", allergen=()),   # home, no allergens
             _item("https://x.com/uk.pdf")]                  # foreign, allergens
    n = _region_notice_for(cov, items, address="1 A St, San Jose, CA, USA", website_url="")
    assert n["sourceRegion"] == "GB"


def test_menu_service_no_notice_when_home_matches():
    from safeplate.menu_service import _region_notice_for
    cov = [_cov("https://x.com/us.pdf", "US")]
    items = [_item("https://x.com/us.pdf")]
    n = _region_notice_for(cov, items, address="1 A St, San Jose, CA, USA", website_url="")
    assert n is not None and n["verified"] is True
