"""cache_store: disk backend (default) -- byte-identical to the old file caches."""
import json

import pytest

from safeplate import cache_store


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch, tmp_path):
    monkeypatch.setenv("SAFEPLATE_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cache_store._reset_for_tests()
    yield tmp_path
    cache_store._reset_for_tests()


def test_disk_round_trip(tmp_path):
    blob = {"at": 123.0, "parsed": {"menu_items": ["pad thai"]}}
    cache_store.save("extraction2_llm", "abc123", blob)
    assert cache_store.load("extraction2_llm", "abc123") == blob


def test_disk_file_layout_matches_legacy(tmp_path):
    # Same path shape + JSON the old call-site code produced, so existing warm
    # caches are readable and the no-DATABASE_URL gate stays byte-identical.
    blob = {"at": 5.0, "items": []}
    cache_store.save("extraction2_result", "deadbeef", blob)
    path = tmp_path / "extraction2_result" / "deadbeef.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == blob
    assert path.read_text(encoding="utf-8") == json.dumps(blob)


def test_disk_reads_legacy_file(tmp_path):
    # A file written by the OLD code (path.write_text(json.dumps(...))) loads.
    folder = tmp_path / "community_signals"
    folder.mkdir(parents=True)
    (folder / "cafe.json").write_text(json.dumps({"at": 1.0, "signals": []}), encoding="utf-8")
    assert cache_store.load("community_signals", "cafe") == {"at": 1.0, "signals": []}


def test_disk_missing_returns_none():
    assert cache_store.load("extraction2_llm", "nope") is None


def test_disk_corrupt_returns_none(tmp_path):
    folder = tmp_path / "diet_llm"
    folder.mkdir(parents=True)
    (folder / "bad.json").write_text("{not json", encoding="utf-8")
    assert cache_store.load("diet_llm", "bad") is None


def test_no_database_url_means_no_pool():
    assert cache_store._get_pool() is None
