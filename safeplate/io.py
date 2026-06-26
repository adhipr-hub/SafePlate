from __future__ import annotations

import csv
from collections.abc import Callable, Sequence
from dataclasses import asdict
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from safeplate.textutil import slugify


T = TypeVar("T")


def timestamped_output_paths(
    label: str,
    out_dir: Path,
    prefix: str,
    suffixes: Sequence[str],
) -> tuple[Path, ...]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H%M%S")
    base = f"{prefix}_{slugify(label)}_{stamp}"
    return tuple(out_dir / f"{base}{suffix}" for suffix in suffixes)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
        return list(csv.DictReader(csv_file))


def write_dataclass_json(path: Path, rows: Sequence[Any]) -> None:
    path.write_text(
        json.dumps([asdict(row) for row in rows], indent=2),
        encoding="utf-8",
    )


def write_dataclass_csv(
    path: Path,
    rows: Sequence[T],
    *,
    fieldnames: Sequence[str],
    transform: Callable[[dict[str, Any], T], None] | None = None,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        # extrasaction="ignore": a dataclass may carry internal-only fields (e.g.
        # matrix column metadata) that aren't part of the published CSV schema.
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            record = asdict(row)
            if transform is not None:
                transform(record, row)
            writer.writerow(record)
