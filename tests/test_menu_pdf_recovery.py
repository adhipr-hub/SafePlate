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


# --- Off-site locality gate (Cicero's San Jose <- Interlochen MI regression) --

def _brave_recovery_result(monkeypatch, pdf_text):
    """Run discover_and_extract with no on-site sources and ONE Brave menu-PDF
    candidate whose extracted text is `pdf_text`; returns (extract_calls, result)."""
    from types import SimpleNamespace

    pdf_url = ("https://wnam-cdn.menuweb.menu/storage/media/companies_menu_pdf/"
               "71638799/ciceros-pizza-parlor-interlochen-menu.pdf")
    monkeypatch.setattr(discover, "discover_sources", lambda *a, **k: [])
    monkeypatch.setattr(
        discover, "_brave_menu_pdf_candidates",
        lambda **k: [discover.Candidate(url=pdf_url, anchor_text="",
                                        kind="menu", source="brave_menu_pdf")],
    )
    payload = SimpleNamespace(url=pdf_url, source_type="pdf", text=pdf_text)
    monkeypatch.setattr("safeplate.extraction2.acquire.acquire",
                        lambda *a, **k: payload)
    calls = {"extract": 0}

    def _fake_extract(payloads, **kwargs):
        calls["extract"] += 1
        return SimpleNamespace(
            items=[SimpleNamespace(item_name="Cheese Pizza")],
            coverage=[], llm_calls=1, incomplete=False,
        )

    monkeypatch.setattr("safeplate.extraction2.pipeline.extract_menu", _fake_extract)
    _cands, result = discover.discover_and_extract(
        "https://cicerospizza.com",
        user_agent="SafePlateTest/1.0",
        restaurant_name="Cicero's Pizza",
        address="6138 Bollinger Rd, San Jose, CA 95129, USA",
        api_key="k", brave_api_key="b",
    )
    return calls["extract"], result


def test_brave_recovery_rejects_wrong_city_pdf(monkeypatch):
    # Abridged REAL text of the Interlochen aggregator PDF: names the restaurant
    # (passes _pdf_mentions) but declares Interlochen / Australia, never San Jose.
    from tests.test_locality import CICEROS_INTERLOCHEN_PDF_TEXT

    extract_calls, result = _brave_recovery_result(
        monkeypatch, CICEROS_INTERLOCHEN_PDF_TEXT)
    assert extract_calls == 0          # rejected BEFORE any extraction/merge
    assert result.items == []          # better no menu than the wrong menu
    assert result.coverage == []       # no coverage stamp (no from-Australia notice)


def test_brave_recovery_keeps_right_city_pdf(monkeypatch):
    text = ("Cicero's Pizza Menu\n6138 Bollinger Rd, San Jose, CA 95129\n"
            "CHEESE PIZZA $10\nPEPPERONI PIZZA $12\n")
    extract_calls, result = _brave_recovery_result(monkeypatch, text)
    assert extract_calls == 1
    assert [it.item_name for it in result.items] == ["Cheese Pizza"]


def test_negative_cache_expires_sooner_than_positive(tmp_path, monkeypatch):
    import dataclasses, time
    from safeplate import cache_store
    from safeplate.menu_text import MenuItemRecord

    monkeypatch.setenv("SAFEPLATE_CACHE_DIR", str(tmp_path))
    two_days = time.time() - 2 * 24 * 60 * 60  # older than negative TTL (1d), within positive (7d)

    fields = {f.name: "" for f in dataclasses.fields(MenuItemRecord)}
    fields.update(item_name="Pad Thai", allergen_terms=["peanut"], dietary_terms=[], confidence=1.0)

    def _write(url, *, items):
        cache_store.save(
            "extraction2_result",
            discover._result_cache_key(url, "m"),
            {"at": two_days, "items": items, "coverage": [], "signals": []},
        )

    # A real (non-empty) hit at 2 days old is still served (within the 7-day TTL).
    _write("https://hit.example", items=[fields])
    assert discover._load_result_cache("https://hit.example", "m") is not None

    # An empty (negative) entry at the same age has expired (1-day negative TTL).
    _write("https://empty.example", items=[])
    assert discover._load_result_cache("https://empty.example", "m") is None
