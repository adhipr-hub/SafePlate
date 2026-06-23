"""Flag-gated, thread-safe per-stage timing for the request path.

Off by default (zero behavioural change). Set ``SAFEPLATE_TIMING=1`` to collect
wall-clock per named stage across all threads, then read ``snapshot()``. The
request path resets at the start of a search and folds the snapshot into the
response under ``"timing"`` so a real breakdown can be observed end to end.

Because the menu-backed list runs many extractions concurrently, a stage's
``total_s`` (summed across threads) can exceed the wall-clock of the phase that
spawned them -- that gap is exactly the parallelism. Compare a phase's own span
(wall) against the summed child spans to see where the time really goes.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator

_ENABLED = (os.environ.get("SAFEPLATE_TIMING") or "").strip().lower() in ("1", "true", "yes")
_LOCK = threading.Lock()
_STATS: "dict[str, dict[str, float]]" = {}


def enabled() -> bool:
    return _ENABLED


def reset() -> None:
    with _LOCK:
        _STATS.clear()


def record(name: str, seconds: float) -> None:
    if not _ENABLED:
        return
    with _LOCK:
        stat = _STATS.get(name)
        if stat is None:
            stat = _STATS[name] = {"count": 0.0, "total_s": 0.0, "max_s": 0.0}
        stat["count"] += 1
        stat["total_s"] += seconds
        stat["max_s"] = max(stat["max_s"], seconds)


@contextmanager
def span(name: str) -> Iterator[None]:
    if not _ENABLED:
        yield
        return
    start = time.monotonic()
    try:
        yield
    finally:
        record(name, time.monotonic() - start)


def snapshot() -> "dict[str, dict[str, float]]":
    with _LOCK:
        out: dict[str, dict[str, float]] = {}
        for name, stat in _STATS.items():
            count = int(stat["count"])
            out[name] = {
                "count": count,
                "total_s": round(stat["total_s"], 3),
                "max_s": round(stat["max_s"], 3),
                "avg_s": round(stat["total_s"] / count, 3) if count else 0.0,
            }
        return out
