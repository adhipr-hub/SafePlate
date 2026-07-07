"""cache_store: disk backend (default) -- byte-identical to the old file caches."""
import json
import os
from contextlib import contextmanager

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


# --------------------------------------------------------------------------- #
# Postgres backend tests (using FakePool to avoid live database dependency)


class FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        stmt = sql.lstrip().upper()
        if stmt.startswith("SELECT"):
            namespace, key = params
            if (namespace, key) in self._rows:
                return FakeResult((self._rows[(namespace, key)],))
            return FakeResult(None)
        if stmt.startswith("INSERT"):
            namespace, key, payload = params
            # psycopg wraps JSONB params in Jsonb (payload.obj holds the dict)
            self._rows[(namespace, key)] = getattr(payload, "obj", payload)
        return FakeResult(None)  # CREATE TABLE etc.


class FakePool:
    def __init__(self, fail=False):
        self.rows = {}
        self.fail = fail

    @contextmanager
    def connection(self):
        if self.fail:
            raise RuntimeError("db down")
        yield FakeConn(self.rows)

    def close(self):
        pass


def _use_fake_pool(monkeypatch, pool):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    cache_store._pool = pool


def test_pg_hit_skips_disk(monkeypatch, tmp_path):
    pool = FakePool()
    pool.rows[("extraction2_llm", "k1")] = {"at": 1.0, "parsed": {"x": 1}}
    _use_fake_pool(monkeypatch, pool)
    assert cache_store.load("extraction2_llm", "k1") == {"at": 1.0, "parsed": {"x": 1}}
    assert not list(tmp_path.rglob("*.json"))  # disk untouched


def test_pg_save_upserts_and_skips_disk(monkeypatch, tmp_path):
    pool = FakePool()
    _use_fake_pool(monkeypatch, pool)
    cache_store.save("diet_llm", "k2", {"at": 1.0, "parsed": ["a"]})
    cache_store.save("diet_llm", "k2", {"at": 2.0, "parsed": ["b"]})
    assert pool.rows[("diet_llm", "k2")] == {"at": 2.0, "parsed": ["b"]}
    assert not list(tmp_path.rglob("*.json"))


def test_pg_miss_falls_back_to_disk_and_promotes(monkeypatch, tmp_path):
    pool = FakePool()
    _use_fake_pool(monkeypatch, pool)
    cache_store._disk_save("community_signals", "warm", {"at": 3.0, "signals": []})
    assert cache_store.load("community_signals", "warm") == {"at": 3.0, "signals": []}
    # lazy migration: the warm file entry was promoted into Postgres
    assert pool.rows[("community_signals", "warm")] == {"at": 3.0, "signals": []}


def test_pg_error_falls_back_to_disk(monkeypatch, tmp_path, caplog):
    pool = FakePool(fail=True)
    _use_fake_pool(monkeypatch, pool)
    cache_store._disk_save("llm_menu", "k3", {"fetched_at": 1.0, "extraction": {}})
    with caplog.at_level("WARNING", logger="safeplate.cache_store"):
        assert cache_store.load("llm_menu", "k3") == {"fetched_at": 1.0, "extraction": {}}
        cache_store.save("llm_menu", "k4", {"fetched_at": 2.0, "extraction": {}})
    # save fell back to disk so the paid result is not lost
    assert cache_store._disk_load("llm_menu", "k4") == {"fetched_at": 2.0, "extraction": {}}
    # warnings are rate-limited: two failures within 60s -> one warning
    assert len([r for r in caplog.records if "cache DB" in r.message]) == 1


def test_pool_init_failure_backs_off(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    attempts = []

    def boom(url):
        attempts.append(url)
        raise RuntimeError("no route to host")

    monkeypatch.setattr(cache_store, "_new_pool", boom)
    assert cache_store._get_pool() is None
    assert cache_store._get_pool() is None  # inside backoff window: no 2nd attempt
    assert len(attempts) == 1
    # and the store still works, on disk
    cache_store.save("diet_llm", "k5", {"at": 1.0, "parsed": []})
    assert cache_store.load("diet_llm", "k5") == {"at": 1.0, "parsed": []}


def test_with_sslmode_appends_require():
    assert cache_store._with_sslmode("postgresql://u:p@h/db") == (
        "postgresql://u:p@h/db?sslmode=require"
    )
    assert cache_store._with_sslmode("postgresql://u:p@h/db?a=1") == (
        "postgresql://u:p@h/db?a=1&sslmode=require"
    )
    assert cache_store._with_sslmode("postgresql://u:p@h/db?sslmode=verify-full") == (
        "postgresql://u:p@h/db?sslmode=verify-full"
    )


@pytest.mark.skipif(
    not os.environ.get("SAFEPLATE_TEST_DATABASE_URL"),
    reason="no test database configured (set SAFEPLATE_TEST_DATABASE_URL to run)",
)
def test_live_database_round_trip(monkeypatch):
    # Optional real-Postgres check; skipped everywhere except a machine that
    # points SAFEPLATE_TEST_DATABASE_URL at a disposable database.
    monkeypatch.setenv("DATABASE_URL", os.environ["SAFEPLATE_TEST_DATABASE_URL"])
    cache_store._reset_for_tests()
    cache_store.save("cache_store_livetest", "k1", {"at": 1.0, "parsed": ["x"]})
    assert cache_store.load("cache_store_livetest", "k1") == {"at": 1.0, "parsed": ["x"]}
