# RDS Cache Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route SafePlate's seven paid-API JSON caches through one shared store that writes to RDS Postgres when `DATABASE_URL` is set and to the existing disk files otherwise.

**Architecture:** New module `safeplate/cache_store.py` exposes `load(namespace, key)` / `save(namespace, key, blob)` with two backends: disk (byte-identical to today's `data/.cache/<ns>/<hash>.json` files, the default) and Postgres (one `cache_entries` table, upserts, lazy pool init, disk fallback on every error). Seven call sites swap their file I/O for store calls; TTL/version/negative-cache logic stays in the callers untouched.

**Tech Stack:** Python 3.12, stdlib, `psycopg[binary,pool]>=3.1` (new), pytest.

**Spec:** `docs/superpowers/specs/2026-07-07-rds-cache-store-design.md`

## Global Constraints

- With `DATABASE_URL` unset, behavior must be byte-identical to today (same file paths `data/.cache/<namespace>/<sha1>.json`, same JSON contents). The existing test suite must pass untouched.
- `cache_store` must never raise out of `load`/`save`: any Postgres problem degrades to disk with a rate-limited warning (max one per 60s).
- TTL checks, cache-version bumps, key hashing, and "don't cache failures/partials" guards stay at the call sites — the store treats payloads as opaque dicts.
- Out of scope: `http` and `robots` disk caches, Secrets Manager, any phase-2/3 schema.
- New dependency is exactly `psycopg[binary,pool]>=3.1`; the import is deferred so machines without it still run in disk mode.
- Tests must not require a live database. Run tests with `python -m pytest <file> -v` (Windows PowerShell dev environment).
- Postgres URLs default to `sslmode=require` unless the URL already specifies an sslmode.

---

### Task 1: `get_database_url()` config getter

**Files:**
- Modify: `safeplate/config.py` (add getter after `get_cache_dir`, ~line 83)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Produces: `safeplate.config.get_database_url() -> str | None` — trimmed `DATABASE_URL` env value, or `None` when unset/blank. Task 2's store calls this.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
def test_get_database_url_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert config.get_database_url() is None


def test_get_database_url_blank_is_none(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert config.get_database_url() is None


def test_get_database_url_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", " postgresql://u:p@h:5432/db ")
    assert config.get_database_url() == "postgresql://u:p@h:5432/db"
```

Check the top of `tests/test_config.py` first: it already imports the config module (as `config` or via `from safeplate import config`) — match whatever name it uses.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -v -k database_url`
Expected: 3 FAILED with `AttributeError: ... has no attribute 'get_database_url'`

- [ ] **Step 3: Implement** — in `safeplate/config.py`, directly after `get_cache_dir()`:

```python
def get_database_url() -> str | None:
    """Postgres URL for the shared cache store (and future DB-backed features).
    Unset/blank means every cache stays on the local disk -- the default for dev
    machines and tests. Standard form:
    postgresql://user:password@host:5432/dbname"""
    url = os.environ.get("DATABASE_URL", "")
    url = url.strip()
    return url or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: all PASS (new 3 + existing).

- [ ] **Step 5: Commit**

```bash
git add safeplate/config.py tests/test_config.py
git commit -m "feat(config): DATABASE_URL getter for the cache store"
```

---

### Task 2: `cache_store` disk backend

**Files:**
- Create: `safeplate/cache_store.py`
- Test: `tests/test_cache_store.py` (new)

**Interfaces:**
- Consumes: `get_cache_dir()`, `get_database_url()` from `safeplate.config`.
- Produces: `cache_store.load(namespace: str, key: str) -> dict | None` and `cache_store.save(namespace: str, key: str, blob: dict) -> None`; test hook `cache_store._reset_for_tests()`. Tasks 3–6 rely on exactly these names.

- [ ] **Step 1: Write the failing tests** — create `tests/test_cache_store.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cache_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'safeplate.cache_store'` (collection error).

- [ ] **Step 3: Implement** — create `safeplate/cache_store.py` (disk backend + selection skeleton; the Postgres internals arrive in Task 3):

```python
"""Shared cache store for every paid-API result cache.

Backends:
- disk (default): data/.cache/<namespace>/<key>.json -- byte-identical to the
  pre-store per-call-site file code, used whenever DATABASE_URL is unset or
  Postgres is unavailable.
- Postgres (DATABASE_URL set): one cache_entries table, upsert on save.

Failure stance is safety-asymmetric like the rest of SafePlate: a database
problem must never fail a request -- every error degrades to disk with a
rate-limited warning. TTL/version logic stays in the callers; payloads are
opaque JSON dicts here.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from safeplate.config import get_cache_dir, get_database_url

logger = logging.getLogger("safeplate.cache_store")

_POOL_RETRY_SECONDS = 60.0   # after a failed pool init, stay on disk this long
_WARN_EVERY_SECONDS = 60.0   # rate-limit "DB down" warnings

_pool = None
_pool_lock = threading.Lock()
_pool_failed_at = 0.0
_last_warn_at = 0.0


def load(namespace: str, key: str) -> dict[str, Any] | None:
    """Cached blob or None (miss/unreadable). Postgres first when configured."""
    return _disk_load(namespace, key)


def save(namespace: str, key: str, blob: dict[str, Any]) -> None:
    """Persist a blob. Postgres upsert when configured; disk otherwise."""
    _disk_save(namespace, key, blob)


# --------------------------------------------------------------------------- #
# disk backend (the pre-store behavior, byte-identical)

def _disk_path(namespace: str, key: str):
    return get_cache_dir() / namespace / f"{key}.json"


def _disk_load(namespace: str, key: str) -> dict[str, Any] | None:
    try:
        blob = json.loads(_disk_path(namespace, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return blob if isinstance(blob, dict) else None


def _disk_save(namespace: str, key: str, blob: dict[str, Any]) -> None:
    path = _disk_path(namespace, key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(blob), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Postgres backend (pool built lazily in Task 3; without a URL there is none)

def _get_pool():
    if not get_database_url():
        return None
    return _pool


def _warn(message: str) -> None:
    global _last_warn_at
    now = time.time()
    if now - _last_warn_at >= _WARN_EVERY_SECONDS:
        _last_warn_at = now
        logger.warning(message)


def _reset_for_tests() -> None:
    """Clear pool + backoff + warn state so each test starts fresh."""
    global _pool, _pool_failed_at, _last_warn_at
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception:
                pass
        _pool = None
        _pool_failed_at = 0.0
        _last_warn_at = 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cache_store.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add safeplate/cache_store.py tests/test_cache_store.py
git commit -m "feat(cache): shared cache_store module, disk backend"
```

---

### Task 3: `cache_store` Postgres backend

**Files:**
- Modify: `safeplate/cache_store.py` (replace `load`, `save`, `_get_pool`; add `_new_pool`, `_pg_save`, `_with_sslmode`, `_CREATE_SQL`)
- Modify: `requirements.txt` (add psycopg)
- Test: `tests/test_cache_store.py` (append)

**Interfaces:**
- Consumes: Task 2's module layout (`_disk_load`, `_disk_save`, `_warn`, `_pool` globals).
- Produces: same public `load`/`save` (signatures unchanged); `_new_pool(url)` seam that tests monkeypatch; table `cache_entries(namespace TEXT, key TEXT, payload JSONB, created_at TIMESTAMPTZ, PRIMARY KEY(namespace, key))`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cache_store.py`:

```python
from contextlib import contextmanager


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
```

(add `import os` to the imports at the top of the file alongside `import json`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cache_store.py -v`
Expected: the new tests FAIL (`test_pg_hit_skips_disk` returns None because `load` ignores the pool; `test_pool_init_failure_backs_off` fails with `AttributeError: _new_pool`; sslmode test fails with `AttributeError: _with_sslmode`). Task 2's tests still PASS.

- [ ] **Step 3: Implement** — in `safeplate/cache_store.py`, replace `load`, `save`, and `_get_pool` with, and add the new helpers:

```python
_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS cache_entries (
    namespace  TEXT        NOT NULL,
    key        TEXT        NOT NULL,
    payload    JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key)
)
"""


def load(namespace: str, key: str) -> dict[str, Any] | None:
    """Cached blob or None. Postgres first (when configured); a Postgres MISS
    falls through to disk and promotes a disk hit into Postgres (lazy migration
    of a warm file cache); a Postgres ERROR degrades to disk."""
    pool = _get_pool()
    if pool is not None:
        try:
            with pool.connection() as conn:
                row = conn.execute(
                    "SELECT payload FROM cache_entries WHERE namespace = %s AND key = %s",
                    (namespace, key),
                ).fetchone()
        except Exception as exc:
            _warn(f"cache DB read failed ({exc!r}); serving from disk")
        else:
            if row is not None:
                blob = row[0]
                return blob if isinstance(blob, dict) else None
            blob = _disk_load(namespace, key)
            if blob is not None:
                _pg_save(pool, namespace, key, blob)  # promote warm file entry
            return blob
    return _disk_load(namespace, key)


def save(namespace: str, key: str, blob: dict[str, Any]) -> None:
    """Upsert into Postgres when configured; disk otherwise. A Postgres error
    writes to disk instead, so a paid result is never lost."""
    pool = _get_pool()
    if pool is not None and _pg_save(pool, namespace, key, blob):
        return
    _disk_save(namespace, key, blob)
```

```python
def _get_pool():
    """The shared connection pool, or None when DATABASE_URL is unset, psycopg
    is missing, or the last init attempt failed <60s ago (backoff so a dead DB
    doesn't add a connect timeout to every cache call)."""
    global _pool, _pool_failed_at
    url = get_database_url()
    if not url:
        return None
    if _pool is not None:
        return _pool
    if time.time() - _pool_failed_at < _POOL_RETRY_SECONDS:
        return None
    with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            _pool = _new_pool(_with_sslmode(url))
        except Exception as exc:
            _pool_failed_at = time.time()
            _warn(f"cache DB unavailable ({exc!r}); using disk cache")
            return None
    return _pool


def _new_pool(url: str):
    """Open the psycopg pool and ensure the table exists. Separate function so
    tests can stub it; ImportError (psycopg not installed) is handled by the
    caller like any other init failure -> disk mode."""
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        url, min_size=0, max_size=4, kwargs={"connect_timeout": 5}, open=True
    )
    with pool.connection() as conn:
        conn.execute(_CREATE_SQL)
    return pool


def _pg_save(pool, namespace: str, key: str, blob: dict[str, Any]) -> bool:
    try:
        from psycopg.types.json import Jsonb

        payload: Any = Jsonb(blob)
    except ImportError:  # fake pools in tests don't need real psycopg
        payload = blob
    try:
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO cache_entries (namespace, key, payload) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (namespace, key) DO UPDATE "
                "SET payload = EXCLUDED.payload, created_at = now()",
                (namespace, key, payload),
            )
        return True
    except Exception as exc:
        _warn(f"cache DB write failed ({exc!r}); writing to disk")
        return False


def _with_sslmode(url: str) -> str:
    """RDS requires TLS; default sslmode=require unless the URL already chose one."""
    if "sslmode=" in url:
        return url
    return url + ("&" if "?" in url else "?") + "sslmode=require"
```

Then add the dependency to `requirements.txt` after the `playwright` block:

```
# Postgres cache store (RDS). Optional at runtime: without DATABASE_URL -- or
# without this package installed -- every cache stays on the local disk.
psycopg[binary,pool]>=3.1
```

Install it: `pip install "psycopg[binary,pool]>=3.1"`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cache_store.py -v`
Expected: 14 PASS, 1 SKIPPED (the live-database test skips without `SAFEPLATE_TEST_DATABASE_URL`).

- [ ] **Step 5: Commit**

```bash
git add safeplate/cache_store.py tests/test_cache_store.py requirements.txt
git commit -m "feat(cache): Postgres backend with disk fallback + lazy promotion"
```

---

### Task 4: Migrate the extraction2 call sites (4 caches)

**Files:**
- Modify: `safeplate/extraction2/discover.py` (`_result_cache_path`/`_load_result_cache`/`_save_result_cache`, lines ~489–546; import at line 28)
- Modify: `safeplate/extraction2/interpret_llm.py` (pdfmatrix block lines ~153–184; `_cached_or_call_inner` lines ~226–260; import at line 9)
- Modify: `safeplate/extraction2/allergy_signals.py` (`_cached_or_call` lines ~177–205; `get_cache_dir` import)
- Test: `tests/test_cache_store_call_sites.py` (new)

**Interfaces:**
- Consumes: `cache_store.load` / `cache_store.save` from Task 2/3.
- Produces: namespaces `"extraction2_result"`, `"extraction2_pdfmatrix"`, `"extraction2_llm"`, `"extraction2_allergy"` in the store. No public signature changes.

- [ ] **Step 1: Write the failing routing tests** — create `tests/test_cache_store_call_sites.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cache_store_call_sites.py -v`
Expected: 3 FAILED with `AttributeError: module ... has no attribute 'cache_store'`.

- [ ] **Step 3: Migrate `discover.py`** — change the import at line 28 from `from safeplate.config import get_cache_dir` to `from safeplate import cache_store` (nothing else in the file uses `get_cache_dir`; verify with a search before deleting). Replace `_result_cache_path` with a key function, and rewire load/save:

```python
def _result_cache_key(website_url: str, model: str, discriminator: str = "") -> str:
    return hashlib.sha1(
        f"{_RESULT_CACHE_VERSION}:{model}:{website_url}:{discriminator}".encode("utf-8")
    ).hexdigest()
```

In `_load_result_cache`, replace the `try: blob = json.loads(...) except (OSError, ValueError): return None` block with:

```python
    blob = cache_store.load(
        "extraction2_result", _result_cache_key(website_url, model, discriminator)
    )
    if blob is None:
        return None
```

(the negative-TTL check, TTL check, and dataclass rehydration below it stay exactly as they are).

In `_save_result_cache`, replace the path/mkdir/write_text/`except OSError` block so the whole function body becomes:

```python
def _save_result_cache(website_url: str, model: str, result, discriminator: str = "") -> None:
    from dataclasses import asdict

    cache_store.save(
        "extraction2_result",
        _result_cache_key(website_url, model, discriminator),
        {
            "at": time.time(),
            "items": [asdict(i) for i in result.items],
            "coverage": [asdict(c) for c in result.coverage],
            "signals": [asdict(s) for s in result.allergy_signals],
            "diet_signals": [asdict(s) for s in result.diet_signals],
        },
    )
```

- [ ] **Step 4: Migrate `interpret_llm.py`** — change the import at line 9 from `from safeplate.config import get_cache_dir` to `from safeplate import cache_store`. In the pdfmatrix function (~line 155), replace

```python
    path = get_cache_dir() / "extraction2_pdfmatrix" / f"{key}.json"
    if use_cache:
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - blob.get("at", 0) <= _CACHE_TTL:
                return [MenuItemRecord(**item) for item in blob["items"]]
        except (OSError, ValueError, KeyError, TypeError):
            pass
```

with

```python
    if use_cache:
        blob = cache_store.load("extraction2_pdfmatrix", key)
        try:
            if blob is not None and time.time() - blob.get("at", 0) <= _CACHE_TTL:
                return [MenuItemRecord(**item) for item in blob["items"]]
        except (KeyError, TypeError):
            pass
```

and replace its save block (`if items:` ... `except OSError: pass`) with

```python
    if items:  # only cache real results; never cache a quota/transient failure
        cache_store.save(
            "extraction2_pdfmatrix",
            key,
            {"at": time.time(), "items": [asdict(i) for i in items]},
        )
```

In `_cached_or_call_inner` (~line 226), replace

```python
    path = get_cache_dir() / "extraction2_llm" / f"{key}.json"
    if use_cache:
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - blob.get("at", 0) <= _CACHE_TTL:
                return blob["parsed"]
        except (OSError, ValueError, KeyError):
            pass
```

with

```python
    if use_cache:
        blob = cache_store.load("extraction2_llm", key)
        if (
            blob is not None
            and "parsed" in blob
            and time.time() - blob.get("at", 0) <= _CACHE_TTL
        ):
            return blob["parsed"]
```

and its save block (`try: path.parent.mkdir...except OSError: pass`) with

```python
    cache_store.save("extraction2_llm", key, {"at": time.time(), "parsed": parsed})
```

- [ ] **Step 5: Migrate `allergy_signals.py`** — swap the `get_cache_dir` import for `from safeplate import cache_store` (check nothing else uses it in the file). In `_cached_or_call` (~line 177), replace the load block with

```python
    key = hashlib.sha1(f"allergysig:{model}:{text}".encode("utf-8")).hexdigest()
    blob = cache_store.load("extraction2_allergy", key)
    if blob is not None and "parsed" in blob and time.time() - blob.get("at", 0) <= _CACHE_TTL:
        return blob["parsed"]
```

and the save block with

```python
    cache_store.save("extraction2_allergy", key, {"at": time.time(), "parsed": parsed})
    return parsed
```

- [ ] **Step 6: Run the routing tests, then the full suite**

Run: `python -m pytest tests/test_cache_store_call_sites.py -v`
Expected: 3 PASS.

Run: `python -m pytest -q`
Expected: full suite PASS (default equivalence: no `DATABASE_URL` in tests → disk backend → identical files).

- [ ] **Step 7: Commit**

```bash
git add safeplate/extraction2/discover.py safeplate/extraction2/interpret_llm.py safeplate/extraction2/allergy_signals.py tests/test_cache_store_call_sites.py
git commit -m "refactor(extraction2): route all four paid caches through cache_store"
```

---

### Task 5: Migrate community, diet, and v1 menu call sites (3 caches)

**Files:**
- Modify: `safeplate/community_signals.py` (`_cache_path`/`_load_cache`/`_save_cache`, lines ~294–335; import at line 28)
- Modify: `safeplate/diet_llm.py` (`_cache_path`/`_load_cache`/`_save_cache`, lines ~125–145; `get_cache_dir` import)
- Modify: `safeplate/menu_fetch_llm.py` (`_cache_path`/`_load_cache`/`_save_cache`, lines ~440–466; `get_cache_dir` import)
- Test: `tests/test_cache_store_call_sites.py` (append)

**Interfaces:**
- Consumes: `cache_store.load` / `cache_store.save`.
- Produces: namespaces `"community_signals"`, `"diet_llm"`, `"llm_menu"` in the store. No public signature changes.

- [ ] **Step 1: Write the failing routing tests** — append to `tests/test_cache_store_call_sites.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cache_store_call_sites.py -v`
Expected: the 3 new tests FAIL with `AttributeError: ... no attribute 'cache_store'`; Task 4's 3 still PASS.

- [ ] **Step 3: Migrate `community_signals.py`** — change the line-28 import to `from safeplate import cache_store` (keep other config imports the file still uses — check first; if `get_cache_dir` was imported alone, replace it). Replace the three functions at the bottom:

```python
def _cache_key(restaurant_name: str, address: str | None, want_dishes: bool) -> str:
    return hashlib.sha1(
        f"{_CACHE_VERSION}:{restaurant_name}:{address or ''}:{int(want_dishes)}".encode("utf-8")
    ).hexdigest()


def _load_cache(restaurant_name: str, address: str | None, want_dishes: bool) -> CommunityResult | None:
    blob = cache_store.load("community_signals", _cache_key(restaurant_name, address, want_dishes))
    if blob is None:
        return None
    if time.time() - blob.get("at", 0) > _CACHE_TTL:
        return None
    from safeplate.diet_score import DietSignal

    try:
        return CommunityResult(
            signals=[CommunitySignal(**s) for s in blob.get("signals", [])],
            dishes=[MenuItemRecord(**d) for d in blob.get("dishes", [])],
            quotes=list(blob.get("quotes", [])),
            diet_signals=[DietSignal(**d) for d in blob.get("diet_signals", [])],
        )
    except (TypeError, KeyError):
        return None


def _save_cache(restaurant_name: str, address: str | None, want_dishes: bool, result: CommunityResult) -> None:
    from dataclasses import asdict

    cache_store.save(
        "community_signals",
        _cache_key(restaurant_name, address, want_dishes),
        {
            "at": time.time(),
            "signals": [asdict(s) for s in result.signals],
            "dishes": [asdict(d) for d in result.dishes],
            "quotes": result.quotes,
            "diet_signals": [asdict(d) for d in result.diet_signals],
        },
    )
```

- [ ] **Step 4: Migrate `diet_llm.py`** — swap the `get_cache_dir` import for `from safeplate import cache_store`; `_cache_key` stays as-is; replace `_cache_path`, `_load_cache`, `_save_cache` with:

```python
def _load_cache(key: str):
    blob = cache_store.load("diet_llm", key)
    if blob is None:
        return None
    if time.time() - blob.get("at", 0) > _CACHE_TTL:
        return None
    return blob.get("parsed")


def _save_cache(key: str, parsed) -> None:
    cache_store.save("diet_llm", key, {"at": time.time(), "parsed": parsed})
```

- [ ] **Step 5: Migrate `menu_fetch_llm.py`** — swap the `get_cache_dir` import for `from safeplate import cache_store` (keep the `Path` import if anything else in the file uses it — check first). Replace `_cache_path`, `_load_cache`, `_save_cache`:

```python
def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _load_cache(url: str) -> dict[str, Any] | None:
    payload = cache_store.load("llm_menu", _cache_key(url))
    if payload is None:
        return None
    if time.time() - payload.get("fetched_at", 0) > _CACHE_TTL_SECONDS:
        return None
    extraction = payload.get("extraction")
    return extraction if isinstance(extraction, dict) else None


def _save_cache(url: str, extraction: dict[str, Any]) -> None:
    cache_store.save(
        "llm_menu",
        _cache_key(url),
        {"fetched_at": time.time(), "extraction": extraction},
    )
```

Search the file for any other `_cache_path(` callers before deleting it; if one exists, convert it to the `_cache_key` form too.

- [ ] **Step 6: Run the routing tests, then the full suite**

Run: `python -m pytest tests/test_cache_store_call_sites.py -v`
Expected: 6 PASS.

Run: `python -m pytest -q`
Expected: full suite PASS.

- [ ] **Step 7: Commit**

```bash
git add safeplate/community_signals.py safeplate/diet_llm.py safeplate/menu_fetch_llm.py tests/test_cache_store_call_sites.py
git commit -m "refactor(cache): community/diet/v1-menu caches through cache_store"
```

---

### Task 6: Warm-cache bulk migration script

**Files:**
- Create: `scripts/migrate_cache_to_db.py`
- Test: `tests/test_migrate_cache_to_db.py` (new)

**Interfaces:**
- Consumes: `cache_store.save`, `cache_store._get_pool`, `config.get_cache_dir`, `config.get_database_url`.
- Produces: CLI `python scripts/migrate_cache_to_db.py` (exit 0 on success, 1 when no/unreachable DB); module constant `NAMESPACES` (the seven in-scope namespaces).

- [ ] **Step 1: Write the failing test** — create `tests/test_migrate_cache_to_db.py`:

```python
"""Bulk import of the on-disk warm cache into Postgres (stubbed store)."""
import importlib.util
import json
from pathlib import Path

import pytest

from safeplate import cache_store


def _load_script():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "migrate_cache_to_db", root / "scripts" / "migrate_cache_to_db.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migrates_all_namespaces_and_skips_junk(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SAFEPLATE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")

    (tmp_path / "extraction2_result").mkdir(parents=True)
    (tmp_path / "extraction2_result" / "aaa.json").write_text(
        json.dumps({"at": 1.0, "items": []}), encoding="utf-8"
    )
    (tmp_path / "diet_llm").mkdir(parents=True)
    (tmp_path / "diet_llm" / "bbb.json").write_text(
        json.dumps({"at": 2.0, "parsed": []}), encoding="utf-8"
    )
    (tmp_path / "diet_llm" / "junk.json").write_text("{broken", encoding="utf-8")
    # out-of-scope namespace must NOT be imported
    (tmp_path / "http").mkdir(parents=True)
    (tmp_path / "http" / "page.json").write_text(json.dumps({"body": "x"}), encoding="utf-8")

    saved = []
    monkeypatch.setattr(cache_store, "save", lambda ns, key, blob: saved.append((ns, key, blob)))
    monkeypatch.setattr(cache_store, "_get_pool", lambda: object())  # "connected"

    script = _load_script()
    assert script.main() == 0
    assert ("extraction2_result", "aaa", {"at": 1.0, "items": []}) in saved
    assert ("diet_llm", "bbb", {"at": 2.0, "parsed": []}) in saved
    assert not any(ns == "http" for ns, _, _ in saved)
    assert len(saved) == 2
    out = capsys.readouterr().out
    assert "imported 2 entries" in out and "1 unreadable" in out


def test_exits_1_without_database_url(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SAFEPLATE_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    script = _load_script()
    assert script.main() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_migrate_cache_to_db.py -v`
Expected: FAIL — `FileNotFoundError` for `scripts/migrate_cache_to_db.py` in `_load_script`.

- [ ] **Step 3: Implement** — create `scripts/migrate_cache_to_db.py`:

```python
"""One-shot import of the on-disk JSON caches into the Postgres cache_entries
table -- so the warm cache built up on this machine keeps saving API money
after the cutover to RDS. Idempotent: re-running upserts the same entries.

Usage (after DATABASE_URL is set in the environment):
    python scripts/migrate_cache_to_db.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safeplate import cache_store
from safeplate.config import get_cache_dir, get_database_url

# The seven paid-API namespaces (spec: docs/superpowers/specs/
# 2026-07-07-rds-cache-store-design.md). http/robots stay on disk on purpose.
NAMESPACES = [
    "extraction2_result",
    "extraction2_llm",
    "extraction2_pdfmatrix",
    "extraction2_allergy",
    "community_signals",
    "diet_llm",
    "llm_menu",
]


def main() -> int:
    if not get_database_url():
        print("DATABASE_URL is not set -- nothing to migrate into.", file=sys.stderr)
        return 1
    if cache_store._get_pool() is None:
        print("Could not connect to the database (see warning above).", file=sys.stderr)
        return 1
    total = skipped = 0
    for namespace in NAMESPACES:
        count = 0
        for path in sorted((get_cache_dir() / namespace).glob("*.json")):
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                skipped += 1
                continue
            if not isinstance(blob, dict):
                skipped += 1
                continue
            cache_store.save(namespace, path.stem, blob)
            count += 1
        total += count
        print(f"{namespace}: {count} entries")
    print(f"imported {total} entries ({skipped} unreadable files skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_migrate_cache_to_db.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Full-suite verification**

Run: `python -m pytest -q`
Expected: entire suite PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/migrate_cache_to_db.py tests/test_migrate_cache_to_db.py
git commit -m "feat(cache): one-shot warm-cache import script for RDS cutover"
```

---

## Post-implementation (ops, not code — hand to the user)

From the spec's EC2 cutover checklist, after all tasks are merged:

1. On the EC2: `git pull`, then `pip install -r requirements.txt` (pulls psycopg).
2. Fetch the master password from Secrets Manager (RDS console → `safeplatedb` → the linked secret) once.
3. Set in the app's environment (systemd unit or `.env`, never committed):
   `DATABASE_URL=postgresql://safeplate:<password>@safeplatedb.cfu68uauyqn4.us-east-2.rds.amazonaws.com:5432/postgres?sslmode=require`
4. `python scripts/migrate_cache_to_db.py` — expect per-namespace counts.
5. Restart the app. Verify: repeat a search (should be instant, zero Gemini calls) and `SELECT count(*) FROM cache_entries;` grows on first-time searches.
