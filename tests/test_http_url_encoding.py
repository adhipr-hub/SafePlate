"""A restaurant with an internationalised domain (a CJK/Cyrillic hostname) or a
non-ASCII path used to crash the whole search: the urllib fetch path hands the URL
straight to stdlib ``http.client``, whose ``putrequest``/``putheader`` encode the
request line + auto-generated ``Host:`` header as ascii/latin-1 and raise
``UnicodeEncodeError`` (a ``ValueError`` subclass) mid-fetch.

``http_get``/``http_post`` now normalise URLs to an ASCII-safe form (IDNA host +
percent-quoted path) like ``requests`` does internally, and guard the fetch so any
still-unencodable URL degrades to an ordinary failed fetch (no data) instead of a
500 that leaked the raw codec message to the client."""

import pytest

from safeplate import http_client
from safeplate.http_client import HttpConnectionError


def test_to_ascii_url_idna_encodes_non_ascii_host():
    out = http_client._to_ascii_url("http://メニューレストラン.com/menu")
    assert out.isascii()
    assert out.startswith("http://xn--")
    assert out.endswith("/menu")


def test_to_ascii_url_percent_encodes_non_ascii_path():
    out = http_client._to_ascii_url("http://example.com/メニュー")
    assert out == "http://example.com/%E3%83%A1%E3%83%8B%E3%83%A5%E3%83%BC"


def test_to_ascii_url_preserves_port_and_query():
    out = http_client._to_ascii_url("http://кафе.рф:8080/поиск?q=чай")
    assert out.isascii()
    assert ":8080/" in out
    assert "q=%D1%87%D0%B0%D0%B9" in out


def test_to_ascii_url_leaves_plain_ascii_untouched():
    url = "https://example.com/a/b?x=1&y=2#frag"
    assert http_client._to_ascii_url(url) is url or http_client._to_ascii_url(url) == url


def test_http_get_converts_unicode_error_to_connection_error(monkeypatch):
    # Even if a URL slips past normalisation still unencodable, the fetch must
    # degrade to a clean failed fetch -- never propagate UnicodeError (which the
    # HTTP layer above would have mistaken for a 400-worthy ValueError).
    monkeypatch.setattr(http_client, "assert_public_url", lambda _url: None)
    monkeypatch.setattr(http_client, "_HAS_REQUESTS", False)

    def _boom(*_args, **_kwargs):
        raise UnicodeEncodeError("latin-1", "x", 0, 1, "ordinal not in range(256)")

    monkeypatch.setattr(http_client, "_http_get_urllib", _boom)
    with pytest.raises(HttpConnectionError):
        http_client.http_get("http://example.com/x", user_agent="ua")


def test_http_post_converts_unicode_error_to_connection_error(monkeypatch):
    monkeypatch.setattr(http_client, "_HAS_REQUESTS", False)

    def _boom(*_args, **_kwargs):
        raise UnicodeEncodeError("latin-1", "x", 0, 1, "ordinal not in range(256)")

    monkeypatch.setattr(http_client, "_http_post_urllib", _boom)
    with pytest.raises(HttpConnectionError):
        http_client.http_post("http://example.com/x", data=b"{}", headers={})
