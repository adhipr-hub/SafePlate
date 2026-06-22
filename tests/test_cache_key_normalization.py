from __future__ import annotations

from safeplate.extraction2.discover import _normalize_cache_url


def test_utm_params_and_clean_url_share_one_key():
    clean = "https://locations.thecheesecakefactory.com/ca/santa-clara-58.html"
    tagged = clean + "?utm_source=Google&utm_medium=Maps&utm_campaign=Google+Places"
    assert _normalize_cache_url(clean) == _normalize_cache_url(tagged)


def test_trailing_slash_www_and_scheme_are_normalized():
    a = "https://www.example.com/menu/"
    b = "http://example.com/menu"
    assert _normalize_cache_url(a) == _normalize_cache_url(b)


def test_distinct_paths_stay_distinct():
    assert _normalize_cache_url("https://x.com/menu") != _normalize_cache_url("https://x.com/allergens")


def test_empty_is_safe():
    assert _normalize_cache_url("") == ""
    assert _normalize_cache_url(None) == ""
