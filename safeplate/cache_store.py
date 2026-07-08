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
    """Cached blob or None -- provenance-free wrapper over load_with_origin."""
    return load_with_origin(namespace, key)[0]


def load_with_origin(namespace: str, key: str) -> tuple[dict[str, Any] | None, str | None]:
    """(blob, origin) where origin is "postgres", "disk", or None on a miss.
    Postgres first (when configured); a Postgres MISS falls through to disk --
    reported as "disk" (the promotion into Postgres is a side effect) -- and a
    Postgres ERROR degrades to disk."""
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
                return (blob, "postgres") if isinstance(blob, dict) else (None, None)
            blob = _disk_load(namespace, key)
            if blob is not None:
                _pg_save(pool, namespace, key, blob)  # promote warm file entry
                return blob, "disk"
            return None, None
    blob = _disk_load(namespace, key)
    return blob, ("disk" if blob is not None else None)


def save(namespace: str, key: str, blob: dict[str, Any]) -> str:
    """Upsert into Postgres when configured; disk otherwise. A Postgres error
    writes to disk instead, so a paid result is never lost. Returns the backend
    that actually took the write ("postgres" or "disk")."""
    pool = _get_pool()
    if pool is not None and _pg_save(pool, namespace, key, blob):
        return "postgres"
    _disk_save(namespace, key, blob)
    return "disk"


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
        # herd guard: another thread may have failed init (and started the
        # backoff window) while we were queued on the lock -- recheck so we
        # don't serially retry a dead DB once per queued thread
        if time.time() - _pool_failed_at < _POOL_RETRY_SECONDS:
            return None
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
        url,
        min_size=0,
        max_size=4,
        kwargs={"connect_timeout": 5},
        open=True,
        # client-side acquisition wait, not just the connect timeout above --
        # caps how long a dead DB can stall a request before we fall back to disk
        timeout=5,
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
