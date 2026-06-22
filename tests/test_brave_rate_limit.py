from __future__ import annotations

import threading
import time

from safeplate.brave_search import _TokenBucket
from safeplate.config import (
    DEFAULT_BRAVE_CONCURRENCY,
    DEFAULT_BRAVE_RPS,
    get_brave_concurrency,
    get_brave_rps,
)


def test_bucket_allows_initial_burst_up_to_capacity():
    # A full bucket lets `capacity` acquisitions through with no meaningful wait.
    bucket = _TokenBucket(rate=20, capacity=5)
    t0 = time.monotonic()
    for _ in range(5):
        bucket.acquire()
    assert time.monotonic() - t0 < 0.05  # burst is effectively instant


def test_bucket_throttles_beyond_capacity_to_the_rate():
    # After the burst, extra tokens arrive at `rate`/sec: 3 more at 20/s ~= 0.15s.
    bucket = _TokenBucket(rate=20, capacity=2)
    bucket.acquire()
    bucket.acquire()  # drain the initial burst
    t0 = time.monotonic()
    for _ in range(3):
        bucket.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.10  # had to wait for refills, not instant
    assert elapsed < 1.0    # but proportional to the rate, not stalled


def test_bucket_rate_is_honored_under_concurrency():
    # 30 acquisitions across 10 threads on a rate=50, capacity=5 bucket must take at
    # least (30-5)/50 = 0.5s -- proving the limiter is shared, not per-thread.
    bucket = _TokenBucket(rate=50, capacity=5)
    t0 = time.monotonic()

    def worker():
        for _ in range(3):
            bucket.acquire()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - t0 >= 0.45


def test_config_defaults_and_env_override(monkeypatch):
    monkeypatch.delenv("SAFEPLATE_BRAVE_RPS", raising=False)
    monkeypatch.delenv("SAFEPLATE_BRAVE_CONCURRENCY", raising=False)
    assert get_brave_rps() == DEFAULT_BRAVE_RPS
    assert get_brave_concurrency() == DEFAULT_BRAVE_CONCURRENCY

    monkeypatch.setenv("SAFEPLATE_BRAVE_RPS", "12.5")
    monkeypatch.setenv("SAFEPLATE_BRAVE_CONCURRENCY", "7")
    assert get_brave_rps() == 12.5
    assert get_brave_concurrency() == 7

    # Garbage / non-positive values fall back to the default rather than breaking.
    monkeypatch.setenv("SAFEPLATE_BRAVE_RPS", "nope")
    monkeypatch.setenv("SAFEPLATE_BRAVE_CONCURRENCY", "0")
    assert get_brave_rps() == DEFAULT_BRAVE_RPS
    assert get_brave_concurrency() == DEFAULT_BRAVE_CONCURRENCY
