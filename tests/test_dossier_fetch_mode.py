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
