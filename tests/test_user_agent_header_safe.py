"""An HTTP header value must be Latin-1 encodable (RFC 9110); stdlib ``http.client``
encodes header values as latin-1 in ``putheader`` and raises ``UnicodeEncodeError``
on any character above U+00FF. ``User-Agent`` is the only non-constant header the
raw-urllib callers (geo/brave/providers) send, so a misconfigured
``SAFEPLATE_USER_AGENT`` containing non-Latin characters (e.g. CJK) crashed the
FIRST outbound call of every search -- geocoding -- and no results ever loaded.

``get_user_agent()`` now guarantees a Latin-1-safe value: drop the un-encodable
characters (keeping Latin-1 accents), falling back to the default if nothing usable
survives."""

from safeplate.config import DEFAULT_USER_AGENT, get_user_agent


def test_default_user_agent_is_latin1_safe(monkeypatch):
    monkeypatch.delenv("SAFEPLATE_USER_AGENT", raising=False)
    ua = get_user_agent()
    ua.encode("latin-1")  # must not raise
    assert ua == DEFAULT_USER_AGENT


def test_non_latin_user_agent_is_sanitized_not_crashing(monkeypatch):
    # 31 non-Latin chars after an ASCII prefix -- the exact shape of the reported crash.
    monkeypatch.setenv("SAFEPLATE_USER_AGENT", "SafePlate日本語のレストランメニューを検索する君/0.1")
    ua = get_user_agent()
    ua.encode("latin-1")  # the crash guard: must not raise
    assert ua  # non-empty
    assert "SafePlate" in ua and "/0.1" in ua


def test_all_non_latin_user_agent_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SAFEPLATE_USER_AGENT", "日本語レストラン")
    ua = get_user_agent()
    ua.encode("latin-1")
    assert ua == DEFAULT_USER_AGENT


def test_latin1_accents_are_preserved(monkeypatch):
    # é / ü are within Latin-1 (<= U+00FF), so they never crashed and stay intact.
    monkeypatch.setenv("SAFEPLATE_USER_AGENT", "SafePlate-café-münchen/0.1")
    ua = get_user_agent()
    assert ua == "SafePlate-café-münchen/0.1"
