"""API keys go into HTTP headers (e.g. Google Places' X-Goog-Api-Key), which stdlib
http.client encodes as latin-1. Pasting a MASKED key -- the UI's bullet dots (U+2022,
ordinal 8226) standing in for the hidden middle -- produces a value that cannot encode
as a header and crashes deep in http.client with an opaque
    UnicodeEncodeError: 'latin-1' codec can't encode characters in position 8-38
before the request is even sent. The key getters now reject such a value with a clear,
actionable message (surfaced to the user as a 400) instead of the cryptic codec crash."""

import pytest

from safeplate import config


def test_masked_google_key_raises_clear_error(monkeypatch):
    # "AIzaSyXX" + 31 bullet dots + "qw" -- the exact shape from the crash report.
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "AIzaSyXX" + "•" * 31 + "qw")
    with pytest.raises(ValueError) as exc:
        config.get_google_places_api_key()
    message = str(exc.value)
    assert "GOOGLE_PLACES_API_KEY" in message
    assert "masked" in message.lower()


def test_valid_google_key_is_returned_stripped(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "  AIzaSyA1b2C3d4E5f6G7h8_valid-key  \n")
    assert config.get_google_places_api_key() == "AIzaSyA1b2C3d4E5f6G7h8_valid-key"


def test_unset_google_key_is_none(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    assert config.get_google_places_api_key() is None


def test_whitespace_only_google_key_is_none(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "   \n\t ")
    assert config.get_google_places_api_key() is None


def test_all_key_getters_reject_masked_values(monkeypatch):
    masked = "abc12345" + "•" * 20
    for env_name, getter in [
        ("GEOAPIFY_API_KEY", config.get_geoapify_api_key),
        ("GOOGLE_PLACES_API_KEY", config.get_google_places_api_key),
        ("BRAVE_SEARCH_API_KEY", config.get_brave_search_api_key),
        ("GEMINI_API_KEY", config.get_gemini_api_key),
    ]:
        monkeypatch.setenv(env_name, masked)
        with pytest.raises(ValueError, match=env_name):
            getter()
        monkeypatch.delenv(env_name, raising=False)
