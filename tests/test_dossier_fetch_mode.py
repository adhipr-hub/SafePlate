"""The Deep-Dive Dossier renders JS-built sites (fetch_mode="auto") while every
other path stays static. These tests lock the fetch_mode threading at each seam
with the fetch layer monkeypatched -- no real browser or network in CI."""

from __future__ import annotations

from types import SimpleNamespace

import safeplate.extraction2.acquire as acquire_mod
from safeplate.extraction2.acquire import acquire
from safeplate.page_fetch import HtmlPage


def _fake_page(url: str) -> HtmlPage:
    return HtmlPage(requested_url=url, final_url=url,
                    html="<html><body>Menu: Dal</body></html>",
                    fetch_method="static_html")


def test_acquire_forwards_fetch_mode(monkeypatch):
    calls = []

    def fake_fetch(url, *, user_agent, fetch_mode="static", use_cache=True):
        calls.append(fetch_mode)
        return _fake_page(url)

    monkeypatch.setattr(acquire_mod, "fetch_html_page", fake_fetch)
    acquire("http://example.test/menu", source_type="website_link",
            user_agent="t", fetch_mode="auto")
    assert calls == ["auto"]


def test_acquire_defaults_to_static(monkeypatch):
    calls = []

    def fake_fetch(url, *, user_agent, fetch_mode="static", use_cache=True):
        calls.append(fetch_mode)
        return _fake_page(url)

    monkeypatch.setattr(acquire_mod, "fetch_html_page", fake_fetch)
    acquire("http://example.test/menu", source_type="website_link", user_agent="t")
    assert calls == ["static"]


from safeplate.extraction2.discover import _cache_discriminator


def test_cache_discriminator_unchanged_for_static():
    # Own-domain static: empty discriminator, exactly as before this feature
    # (existing cache entries must stay valid).
    assert _cache_discriminator("http://tandoori.example", "Tandoori Hut") == ""
    assert _cache_discriminator(
        "http://tandoori.example", "Tandoori Hut", fetch_mode="static") == ""


def test_cache_discriminator_splits_auto_runs():
    static = _cache_discriminator("http://tandoori.example", "Tandoori Hut")
    auto = _cache_discriminator(
        "http://tandoori.example", "Tandoori Hut", fetch_mode="auto")
    assert auto != static
    assert auto.endswith("+fm=auto")


import safeplate.extraction2.discover as discover_mod
from safeplate.allergen_score import Severity, UserProfile
from safeplate.menu_service import (
    _extract_and_assess_structured,
    _fetch_mode_from_payload,
)


def test_fetch_mode_from_payload_validates():
    assert _fetch_mode_from_payload({}) == "static"
    assert _fetch_mode_from_payload({"fetchMode": "auto"}) == "auto"
    assert _fetch_mode_from_payload({"fetchMode": "dynamic"}) == "dynamic"
    assert _fetch_mode_from_payload({"fetchMode": "browser!!"}) == "static"
    assert _fetch_mode_from_payload({"fetchMode": None}) == "static"


def _capture_discover(monkeypatch):
    captured = {}

    def fake_discover(website_url, **kwargs):
        captured.update(kwargs)
        return [], SimpleNamespace(
            items=[], allergy_signals=[], coverage=[], diet_signals=[]
        )

    monkeypatch.setattr(discover_mod, "discover_and_extract", fake_discover)
    return captured


def _run_extract(fetch_mode=None):
    kwargs = dict(
        name="Tandoori Hut", website_url="http://tandoori.example", address="",
        categories=[], latitude=None, longitude=None,
        profile=UserProfile.for_nuts(Severity.ALLERGY),
        user_agent="t", api_key=None,
    )
    if fetch_mode is not None:
        kwargs["fetch_mode"] = fetch_mode
    return _extract_and_assess_structured(**kwargs)


def test_extract_and_assess_forwards_fetch_mode(monkeypatch):
    captured = _capture_discover(monkeypatch)
    _run_extract(fetch_mode="auto")
    assert captured["fetch_mode"] == "auto"


def test_extract_and_assess_defaults_to_static(monkeypatch):
    # The drawer / search-card path never sets fetch_mode: default-equivalence.
    captured = _capture_discover(monkeypatch)
    _run_extract()
    assert captured["fetch_mode"] == "static"


import safeplate.page_fetch as page_fetch_mod
from safeplate.dossier import Target, _menu_payload, scan_deeper_site


def test_dossier_menu_payload_requests_auto_rendering():
    target = Target(name="Tandoori Hut", website_url="http://tandoori.example",
                    address="1 Curry Way", categories=["indian"],
                    latitude=None, longitude=None)
    payload = _menu_payload(target, {})
    assert payload["fetchMode"] == "auto"


def test_deeper_scan_internal_pages_use_auto(monkeypatch):
    calls = []

    def fake_fetch(url, *, user_agent, fetch_mode="static", use_cache=True):
        calls.append((url, fetch_mode))
        html = ('<html><body><a href="/allergy-info">Allergy info</a>'
                "</body></html>")
        return HtmlPage(requested_url=url, final_url=url, html=html,
                        fetch_method="static_html")

    monkeypatch.setattr(page_fetch_mod, "fetch_html_page", fake_fetch)
    result = scan_deeper_site("http://tandoori.example", user_agent="t",
                              api_key=None, model="gemini-test")
    # Homepage + the /allergy-info internal page, both fetched with "auto".
    assert [mode for _u, mode in calls] == ["auto", "auto"]
    assert result.pages_scanned  # scan ran (api_key=None stops before Gemini)


# --- SPA fallback: render the site root when static discovery finds no menu -------
# A fully JS-rendered (single-page-app) restaurant site has no static links to
# harvest, so discovery yields no menu candidate at all and the renderer -- wired
# into acquisition -- never gets a URL to render. The fallback hands acquisition the
# site itself as a last-resort menu candidate, but ONLY on a rendering run so static
# discovery stays byte-identical.

from safeplate.extraction2.discover import Candidate, _spa_fallback_candidate


def _cand(url: str, kind: str) -> Candidate:
    return Candidate(url=url, anchor_text="", kind=kind, source="link")


def test_spa_fallback_fires_when_auto_and_no_menu_candidate():
    fb = _spa_fallback_candidate("http://scratch-sj.square.site/", [], "auto")
    assert fb is not None
    assert fb.url == "http://scratch-sj.square.site/"
    assert fb.kind == "menu"
    assert fb.reason == "spa_fallback"


def test_spa_fallback_none_for_static_run():
    # Byte-identical invariant: the non-dossier (static) paths never get a fallback.
    assert _spa_fallback_candidate("http://scratch-sj.square.site/", [], "static") is None


def test_spa_fallback_none_when_menu_candidate_already_found():
    cands = [_cand("http://x.test/menu", "menu")]
    assert _spa_fallback_candidate("http://x.test/", cands, "auto") is None


def test_spa_fallback_fires_past_non_menu_candidates():
    # An off-site allergen PDF (from the Brave fallback) is not a menu -- the SPA
    # homepage still needs rendering to reveal the actual dishes.
    cands = [_cand("http://cdn.test/allergens.pdf", "allergen")]
    fb = _spa_fallback_candidate("http://x.test/", cands, "auto")
    assert fb is not None and fb.kind == "menu"


def test_spa_fallback_none_for_noise_website():
    # A Facebook/Instagram "website" has no renderable menu; don't waste a render.
    assert _spa_fallback_candidate("https://m.facebook.com/soulfoodsanjose", [], "auto") is None


def test_spa_fallback_none_when_url_already_a_candidate():
    cands = [_cand("http://x.test/", "allergen")]  # same URL already queued
    assert _spa_fallback_candidate("http://x.test/", cands, "auto") is None
