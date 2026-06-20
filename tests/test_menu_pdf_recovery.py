from safeplate.extraction2.discover import (
    _city_token, _pdf_mentions, _is_pdf_url, _registrable_domain, _harvest_links,
    _seed_urls, _select_links, _heuristic_is_confident,
)
import safeplate.extraction2.discover as discover


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


# --- Phase D: cost wins -----------------------------------------------------

def test_heuristic_confident_on_allergen_link():
    sel = _heuristic_is_confident([(("https://x.com/allergens", "Allergens"), "allergen")])
    assert sel is True


def test_heuristic_confident_on_pdf():
    sel = _heuristic_is_confident([(("https://x.com/menu.pdf", "Menu"), "menu")])
    assert sel is True


def test_heuristic_not_confident_on_plain_menu_page():
    sel = _heuristic_is_confident([(("https://x.com/menu", "Menu"), "menu")])
    assert sel is False


def test_select_links_skips_llm_when_allergen_link_present(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("LLM link-select should be skipped on a confident heuristic")
    monkeypatch.setattr(discover, "_llm_select", _boom)
    links = [("https://x.com/allergen-guide", "Allergen Guide"),
             ("https://x.com/about", "About")]
    out = _select_links(links, api_key="k", model="m")
    assert any(kind == "allergen" for _link, kind in out)


def test_select_links_uses_llm_when_only_ambiguous_links(monkeypatch):
    called = {"n": 0}
    def _fake_llm(links, **k):
        called["n"] += 1
        return [(links[0], "menu")]
    monkeypatch.setattr(discover, "_llm_select", _fake_llm)
    links = [("https://x.com/our-food", "Our Food"), ("https://x.com/about", "About")]
    _select_links(links, api_key="k", model="m")
    assert called["n"] == 1  # ambiguous -> LLM consulted


def test_negative_cache_expires_sooner_than_positive(tmp_path, monkeypatch):
    import dataclasses, json, time
    from safeplate.menu_text import MenuItemRecord

    monkeypatch.setattr(discover, "get_cache_dir", lambda: tmp_path)
    two_days = time.time() - 2 * 24 * 60 * 60  # older than negative TTL (1d), within positive (7d)

    fields = {f.name: "" for f in dataclasses.fields(MenuItemRecord)}
    fields.update(item_name="Pad Thai", allergen_terms=["peanut"], dietary_terms=[], confidence=1.0)

    def _write(url, *, items):
        p = discover._result_cache_path(url, "m")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"at": two_days, "items": items, "coverage": [], "signals": []}))

    # A real (non-empty) hit at 2 days old is still served (within the 7-day TTL).
    _write("https://hit.example", items=[fields])
    assert discover._load_result_cache("https://hit.example", "m") is not None

    # An empty (negative) entry at the same age has expired (1-day negative TTL).
    _write("https://empty.example", items=[])
    assert discover._load_result_cache("https://empty.example", "m") is None
