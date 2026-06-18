from safeplate.extraction2.discover import (
    _city_token, _pdf_mentions, _is_pdf_url, _registrable_domain, _harvest_links,
    _seed_urls,
)


def test_pdf_mentions_compact_match():
    text = "Welcome to Pizza Express. Our menu includes..."
    assert _pdf_mentions(text, "Pizza Express") is True


def test_pdf_mentions_token_majority():
    text = "the wagamama allergen guide lists every dish"
    assert _pdf_mentions(text, "Wagamama") is True


def test_pdf_mentions_rejects_wrong_restaurant():
    text = "Nando's peri-peri chicken menu and nutritional info"
    assert _pdf_mentions(text, "Pizza Express") is False


def test_pdf_mentions_empty():
    assert _pdf_mentions("", "Anything") is False


def test_city_token():
    assert _city_token("5152 Moorpark Avenue, San Jose, CA, 95129") == "San Jose"
    assert _city_token("Cupertino") is None
    assert _city_token(None) is None


def test_is_pdf_url_handles_query_and_fragment():
    # Shopify & many CDNs cache-bust PDFs with ?v= -- a bare endswith('.pdf') misses them.
    assert _is_pdf_url("https://cdn.shopify.com/s/files/Allergen_Menu.pdf?v=123") is True
    assert _is_pdf_url("https://x.com/a/Menu.PDF#page=2") is True
    assert _is_pdf_url("https://x.com/menu") is False


def test_registrable_domain():
    assert _registrable_domain("orders.lazydogrestaurants.com") == "lazydogrestaurants.com"
    assert _registrable_domain("www.foo.com") == "foo.com"
    assert _registrable_domain("shop.foo.co.uk") == "foo.co.uk"


def test_seed_urls_adds_root_for_deep_pages():
    # A location landing page -> also seed the site homepage (where category links live).
    assert _seed_urls("https://lazydogrestaurants.com/pages/cupertino-ca") == [
        "https://lazydogrestaurants.com/pages/cupertino-ca",
        "https://lazydogrestaurants.com/",
    ]


def test_seed_urls_root_only_once():
    assert _seed_urls("https://x.com/") == ["https://x.com/"]
    assert _seed_urls("https://x.com") == ["https://x.com"]


def test_harvest_keeps_subdomain_and_querystring_pdf_drops_offsite():
    html = (
        '<a href="https://orders.lazydogrestaurants.com/menu">Menu</a>'
        '<a href="https://cdn.shopify.com/s/files/Allergen.pdf?v=9">Allergens</a>'
        '<a href="https://facebook.com/lazydog">Facebook</a>'
        '<a href="/pages/about">About</a>'
    )
    base = "https://lazydogrestaurants.com/pages/cupertino-ca"
    urls = [u for u, _ in _harvest_links(html, base)]
    assert any("orders.lazydogrestaurants.com/menu" in u for u in urls)  # same-site subdomain kept
    assert any("Allergen.pdf?v=9" in u for u in urls)                    # off-site query-string PDF kept
    assert any(u.endswith("/pages/about") for u in urls)                 # same-site path kept
    assert not any("facebook.com" in u for u in urls)                    # off-site non-PDF dropped
