from __future__ import annotations

from collections.abc import Iterator
from typing import Any, TypeVar


T = TypeVar("T")


def split_semicolon_terms(value: str) -> list[str]:
    return [term.strip() for term in value.split(";") if term.strip()]


def int_value(
    value: Any,
    default: int = 0,
    *,
    allow_float: bool = False,
) -> int:
    try:
        if allow_float:
            return int(float(value or default))
        return int(value or default)
    except (TypeError, ValueError):
        return default


def float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def optional_float(value: Any) -> float | None:
    if value in [None, ""]:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def optional_int(value: Any) -> int | None:
    if value in [None, ""]:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def chunks(rows: list[T], size: int) -> Iterator[list[T]]:
    chunk_size = max(1, size)
    for index in range(0, len(rows), chunk_size):
        yield rows[index : index + chunk_size]
