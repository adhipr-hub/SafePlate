from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, List, TypeVar

T = TypeVar("T")
R = TypeVar("R")


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
        # executor.map preserves the order of the input iterable.
        return list(executor.map(func, materialized))
