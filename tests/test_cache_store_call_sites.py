"""Each migrated call site must route through safeplate.cache_store.

These monkeypatch cache_store.load: a HIT must be served without touching the
network (the downstream call is patched to explode), proving both the routing
and the namespace."""
import time

import pytest


def test_result_cache_load_routes_through_store(monkeypatch):
    from safeplate.extraction2 import discover

    seen = {}

    def fake_load_with_origin(namespace, key):
        seen["args"] = (namespace, key)
        return (None, None)

    monkeypatch.setattr(discover.cache_store, "load_with_origin", fake_load_with_origin)
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


def test_community_signals_cache_hit_serves_from_store(monkeypatch):
    from safeplate import community_signals

    monkeypatch.setattr(
        community_signals.cache_store, "load",
        lambda ns, key: {"at": time.time(), "signals": [], "dishes": [], "quotes": [], "diet_signals": []}
        if ns == "community_signals" else None,
    )
    result = community_signals._load_cache("Nut House Cafe", "1 Main St", False)
    assert result is not None
    assert result.signals == [] and result.quotes == []


def test_diet_llm_cache_hit_serves_from_store(monkeypatch):
    from safeplate import diet_llm

    monkeypatch.setattr(
        diet_llm.cache_store, "load",
        lambda ns, key: {"at": time.time(), "parsed": [{"n": "salad"}]} if ns == "diet_llm" else None,
    )
    assert diet_llm._load_cache("somekey") == [{"n": "salad"}]


def test_llm_menu_cache_hit_serves_from_store(monkeypatch):
    from safeplate import menu_fetch_llm

    extraction = {"menu_items": [{"item_name": "pho"}]}
    monkeypatch.setattr(
        menu_fetch_llm.cache_store, "load",
        lambda ns, key: {"fetched_at": time.time(), "extraction": extraction}
        if ns == "llm_menu" else None,
    )
    assert menu_fetch_llm._load_cache("https://pho.example/menu") == extraction


def test_result_cache_hit_stamps_origin(monkeypatch):
    from safeplate.extraction2 import discover

    blob = {"at": time.time(), "items": [], "coverage": [], "signals": [], "diet_signals": []}
    monkeypatch.setattr(
        discover.cache_store, "load_with_origin",
        lambda ns, key: (blob, "postgres") if ns == "extraction2_result" else (None, None),
    )
    result = discover._load_result_cache("https://tag.example", "m")
    assert result is not None
    assert result.cache_origin == "postgres"


def test_result_cache_save_stamps_destination(monkeypatch):
    from safeplate.extraction2 import discover
    from safeplate.extraction2.schema import MenuExtractionResult

    monkeypatch.setattr(discover.cache_store, "save", lambda ns, key, blob: "postgres")
    result = MenuExtractionResult(items=[], coverage=[])
    assert result.cache_saved_to is None
    discover._save_result_cache("https://tag.example", "m", result)
    assert result.cache_saved_to == "postgres"
