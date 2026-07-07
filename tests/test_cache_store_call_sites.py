"""Each migrated call site must route through safeplate.cache_store.

These monkeypatch cache_store.load: a HIT must be served without touching the
network (the downstream call is patched to explode), proving both the routing
and the namespace."""
import time

import pytest


def test_result_cache_load_routes_through_store(monkeypatch):
    from safeplate.extraction2 import discover

    seen = {}

    def fake_load(namespace, key):
        seen["args"] = (namespace, key)
        return None

    monkeypatch.setattr(discover.cache_store, "load", fake_load)
    assert discover._load_result_cache("https://tacos.example", "gemini-x") is None
    namespace, key = seen["args"]
    assert namespace == "extraction2_result"
    assert len(key) == 40  # sha1 hex, same key scheme as the old filenames


def test_llm_chunk_cache_hit_serves_from_store(monkeypatch):
    from safeplate.extraction2 import interpret_llm

    parsed = {"page_had_menu": True, "menu_items": []}
    monkeypatch.setattr(
        interpret_llm.cache_store, "load",
        lambda ns, key: {"at": time.time(), "parsed": parsed} if ns == "extraction2_llm" else None,
    )
    monkeypatch.setattr(
        interpret_llm, "_call_with_retry",
        lambda *a, **k: pytest.fail("cache hit must not call Gemini"),
    )
    out = interpret_llm._cached_or_call_inner("menu text", api_key="k", model="m")
    assert out == parsed


def test_allergy_signals_cache_hit_serves_from_store(monkeypatch):
    from safeplate.extraction2 import allergy_signals

    parsed = {"diet_statements": []}
    monkeypatch.setattr(
        allergy_signals.cache_store, "load",
        lambda ns, key: {"at": time.time(), "parsed": parsed} if ns == "extraction2_allergy" else None,
    )
    monkeypatch.setattr(
        allergy_signals, "_call_with_retry",
        lambda *a, **k: pytest.fail("cache hit must not call Gemini"),
    )
    assert allergy_signals._cached_or_call("page text", api_key="k", model="m") == parsed
