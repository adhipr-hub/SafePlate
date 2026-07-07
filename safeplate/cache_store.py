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
