from __future__ import annotations

import json
from pathlib import Path

from safeplate.io import timestamped_output_paths
from safeplate.io import write_dataclass_csv
from safeplate.io import write_dataclass_json
from safeplate.schemas import RestaurantRecord


CSV_FIELDS = [
    "name",
    "address",
    "latitude",
    "longitude",
    "distance_meters",
    "rating",
    "review_count",
    "price_level",
    "categories",
    "website_url",
    "phone_number",
    "opening_hours",
    "business_status",
    "is_open_now",
    "service_options",
    "source_last_updated",
    "data_quality_score",
    "source_name",
    "source_id",
    "fetched_at",
    "raw_payload",
]


def build_output_paths(location: str, out_dir: Path) -> tuple[Path, Path, Path]:
    json_path, csv_path, summary_path = timestamped_output_paths(
        location,
        out_dir,
        "restaurants",
        (".json", ".csv", ".summary.json"),
    )
    return json_path, csv_path, summary_path


def write_json(path: Path, rows: list[RestaurantRecord]) -> None:
    write_dataclass_json(path, rows)


def write_csv(path: Path, rows: list[RestaurantRecord]) -> None:
    def transform(record: dict, row: RestaurantRecord) -> None:
        record["categories"] = "; ".join(row.categories)
        record["service_options"] = json.dumps(row.service_options, sort_keys=True)
        record["raw_payload"] = json.dumps(row.raw_payload, sort_keys=True)

    write_dataclass_csv(path, rows, fieldnames=CSV_FIELDS, transform=transform)
