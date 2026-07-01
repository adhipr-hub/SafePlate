"""Wiring: when the Places "website" is a social / Maps link, discovery must not
fetch it as a seed (there is no menu there) -- it should fall through to name-based
recovery. A real site must still be fetched.
"""

from __future__ import annotations

from safeplate.extraction2 import discover as D


def _stub_fetch(calls):
    def _f(url, *args, **kwargs):
        calls.append(url)
        raise D.PageFetchError("stubbed")
    return _f


def test_social_website_is_not_fetched_as_seed(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(D, "fetch_html_page", _stub_fetch(calls))
    # No brave key -> no name-based recovery either, so this isolates the seed step.
    out = D.discover_sources(
        "https://www.instagram.com/some_bistro/",
        user_agent="t",
        restaurant_name="Some Bistro",
    )
    assert calls == []          # never fetched the Instagram profile
    assert out == []            # nothing discovered from a social link


def test_real_website_is_fetched_as_seed(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(D, "fetch_html_page", _stub_fetch(calls))
    D.discover_sources(
        "https://www.example-bistro.com/",
        user_agent="t",
        restaurant_name="Example Bistro",
    )
    assert "https://www.example-bistro.com/" in calls
