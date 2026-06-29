"""Home-country / official-source guard for the Brave web-search fallback.

The global chain benchmark surfaced safety-critical provenance failures: a US
Burger King's allergen matrix came from Malta (.mt), Starbucks' from Switzerland
(.ch), etc. A wrong-country allergen matrix is dangerous for an allergy app
(different recipes / suppliers / labelling laws). These guards (a) bias the Brave
queries toward the restaurant's home country ("din tai fung usa menu pdf") and
(b) DEMOTE foreign-ccTLD results to a last-resort fallback (kept, not dropped --
content-locale validation later labels them as from-another-region) so a
wrong-country matrix is never silently trusted.
"""
from safeplate.extraction2.discover import (
    Candidate,
    _home_country,
    _region_token,
    _host_country,
    _is_foreign_source,
    _rank_sources,
    _allergen_queries,
    _menu_pdf_queries,
)


# --- home country detection --------------------------------------------------

def test_home_country_from_us_address():
    assert _home_country("5154 Moorpark Ave, San Jose, CA 95129, USA", "") == "US"


def test_home_country_from_uk_address():
    assert _home_country("10 Foo St, London W1D 3QF, UK", "") == "GB"


def test_home_country_falls_back_to_website_cctld():
    # No country in the address segment -> read the site's ccTLD.
    assert _home_country("Some Street, Toronto", "https://timhortons.ca/") == "CA"


def test_home_country_unknown_returns_none():
    assert _home_country("", "https://example.com/") is None


# --- host country from ccTLD -------------------------------------------------

def test_host_country_cctld():
    assert _host_country("burgerking.com.mt") == "MT"
    assert _host_country("starbucks.ch") == "CH"
    assert _host_country("foo.co.uk") == "GB"


def test_host_country_generic_is_neutral():
    assert _host_country("cdn.example.com") is None
    assert _host_country("foo.org") is None
    assert _host_country("bucket.s3.amazonaws.com") is None


# --- foreign source detection ------------------------------------------------

def test_foreign_source_flags_wrong_country_cctld():
    assert _is_foreign_source("https://burgerking.com.mt/allergens.pdf", "US") is True
    assert _is_foreign_source("https://starbucks.ch/allergy.pdf", "US") is True


def test_foreign_source_allows_neutral_and_home():
    assert _is_foreign_source("https://cdn.example.com/menu.pdf", "US") is False
    assert _is_foreign_source("https://nandos.co.uk/allergens.pdf", "GB") is False


def test_foreign_source_no_home_country_allows_everything():
    # If we can't tell the home country, don't filter (graceful degradation).
    assert _is_foreign_source("https://starbucks.ch/allergy.pdf", None) is False


# --- ranking + filtering -----------------------------------------------------

def _c(url):
    return Candidate(url=url, anchor_text="", kind="allergen", source="brave")


def test_rank_sources_demotes_foreign_below_official_and_neutral():
    cands = [
        _c("https://bk.com.mt/allergens.pdf"),             # foreign (Malta) -> last
        _c("https://thirdparty.com/bk-allergens.pdf"),     # neutral
        _c("https://bk.com/allergens.pdf"),                # official domain
    ]
    out = _rank_sources(cands, official_regdomain="bk.com", home_country="US")
    urls = [c.url for c in out]
    assert urls[0] == "https://bk.com/allergens.pdf"       # official first
    assert urls[1] == "https://thirdparty.com/bk-allergens.pdf"  # neutral next
    assert urls[-1] == "https://bk.com.mt/allergens.pdf"   # foreign kept, but LAST


def test_rank_sources_prefers_home_cctld_over_neutral():
    cands = [
        _c("https://aggregator.com/nandos.pdf"),           # neutral
        _c("https://nandos.co.uk/allergens.pdf"),          # home ccTLD (GB)
    ]
    out = _rank_sources(cands, official_regdomain="", home_country="GB")
    assert out[0].url == "https://nandos.co.uk/allergens.pdf"


def test_rank_sources_no_home_country_is_passthrough():
    cands = [_c("https://starbucks.ch/a.pdf"), _c("https://x.com/b.pdf")]
    out = _rank_sources(cands, official_regdomain="", home_country=None)
    assert len(out) == 2  # nothing dropped when home unknown


# --- query biasing -----------------------------------------------------------

def test_region_token():
    assert _region_token("US") == "USA"
    assert _region_token("GB") == "UK"
    assert _region_token(None) == ""


def test_allergen_queries_inject_region():
    qs = _allergen_queries(domain="bk.com", restaurant_name="Burger King",
                           city=None, region="USA")
    assert any("filetype:pdf" in q and "USA" in q for q in qs)
    assert any(q.startswith("site:bk.com") for q in qs)


def test_menu_pdf_queries_inject_region():
    # The user's example: "din tai fung usa menu pdf".
    qs = _menu_pdf_queries(restaurant_name="Din Tai Fung", city=None, region="USA")
    assert any("USA" in q and "menu filetype:pdf" in q for q in qs)


def test_queries_dedupe_and_skip_blank_region():
    qs = _menu_pdf_queries(restaurant_name="X", city=None, region="")
    assert qs == ['"X" menu filetype:pdf']
