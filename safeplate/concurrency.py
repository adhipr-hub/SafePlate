from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, List, TypeVar

T = TypeVar("T")
R = TypeVar("R")


class TokenBucket:
    """Shared rate limiter. Hands out at most ``rate`` tokens per second across ALL
    threads, allowing a short burst up to ``capacity``. ``acquire`` blocks just long
    enough to stay under the rate -- the governor that keeps API callers off 429s
    regardless of how fast individual calls return (a pure semaphore can't, since
    fast/cached replies let many fire within one 1s window)."""

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self.updated) * self.rate
                )
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            time.sleep(wait)  # sleep OUTSIDE the lock so other threads can refill-check


def map_concurrent(
    func: Callable[[T], R],
    items: Iterable[T],
    *,
    max_workers: int,
) -> List[R]:
    """Apply ``func`` to each item using a thread pool, preserving input order.

    SafePlate's work is I/O-bound (HTTP fetches, OCR/LLM calls), so threads give
    a near-linear speedup despite the GIL. Falls back to a plain serial loop when
    there is nothing to gain from a pool.
    """
    materialized = list(items)
    if not materialized:
        return []

    workers = max(1, min(max_workers, len(materialized)))
    if workers == 1:
        return [func(item) for item in materialized]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        # Submit in input order so results stay aligned. Isolate per-item failures:
        # one item raising must not discard the whole batch (executor.map would re-raise
        # the first exception and drop every other result). A failed item becomes None,
        # which callers already treat as "no result" (best-effort, matching the rest of
        # the pipeline).
        futures = [executor.submit(func, item) for item in materialized]
        results: List[R] = []
        for fut in futures:
            try:
                results.append(fut.result())
            except Exception:
                results.append(None)  # type: ignore[arg-type]
        return results
