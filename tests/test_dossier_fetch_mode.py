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
