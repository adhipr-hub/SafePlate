from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

from safeplate.geo import haversine_meters
from safeplate.schemas import RestaurantRecord


QUALITY_FIELDS = [
    "name",
    "address",
    "website_url",
    "phone_number",
    "opening_hours",
    "categories",
    "source_last_updated",
]


def restaurant_quality_score(row: RestaurantRecord) -> float:
    present = sum(1 for field in QUALITY_FIELDS if _has_value(getattr(row, field)))
    return round(present / len(QUALITY_FIELDS), 3)


def build_quality_summary(
    *,
    rows: list[RestaurantRecord],
    location: str,
    radius_meters: int,
    limit: int,
    provider: str,
) -> dict[str, Any]:
    total = len(rows)
    source_counts = _source_counts(rows)
    source_coverage = _source_coverage(rows, provider)
    field_counts = {
        field: sum(1 for row in rows if _has_value(getattr(row, field)))
        for field in QUALITY_FIELDS
    }
    missing_fields = {
        field: total - count for field, count in field_counts.items()
    }

    scores = [row.data_quality_score for row in rows]
    average_score = round(sum(scores) / total, 3) if total else 0.0

    return {
        "location": location,
        "radius_meters": radius_meters,
        "limit": limit,
        "provider": provider,
        "total_rows": total,
        "source_counts": source_counts,
        "source_coverage": source_coverage,
        "average_data_quality_score": average_score,
        "field_counts": field_counts,
        "missing_fields_by_column": missing_fields,
        "rows": {
            "with_name": field_counts["name"],
            "with_address": field_counts["address"],
            "with_website": field_counts["website_url"],
            "with_phone": field_counts["phone_number"],
            "with_opening_hours": field_counts["opening_hours"],
            "with_cuisine_or_categories": field_counts["categories"],
            "with_source_last_updated": field_counts["source_last_updated"],
        },
    }


def print_quality_summary(summary: dict[str, Any]) -> None:
    total = summary["total_rows"]
    rows = summary["rows"]

    print("")
    print("Data quality:")
    print(f"- {rows['with_name']}/{total} have names")
    print(f"- {rows['with_address']}/{total} have addresses")
    print(f"- {rows['with_website']}/{total} have websites")
    print(f"- {rows['with_phone']}/{total} have phone numbers")
    print(f"- {rows['with_opening_hours']}/{total} have opening hours")
    print(f"- {rows['with_cuisine_or_categories']}/{total} have cuisine/category tags")
    print(f"- {rows['with_source_last_updated']}/{total} have source freshness tags")
    print(f"- average quality score: {summary['average_data_quality_score']}")

    source_counts = summary["source_counts"]
    if source_counts:
        print("")
        print("Source coverage:")
        for source, count in source_counts.items():
            print(f"- {count} rows from {source}")

        coverage = summary["source_coverage"]
        if len(coverage["expected_sources"]) > 1:
            source_label = (
                "both sources"
                if len(coverage["expected_sources"]) == 2
                else "all sources"
            )
            print(
                f"- {coverage['matched_in_all_sources']} restaurants found in "
                f"{source_label}"
            )
            print(
                "- "
                f"{coverage['not_covered_in_all_sources']} restaurants not covered in "
                f"{source_label}"
            )
            for source, count in coverage["only_by_source"].items():
                print(f"- {count} restaurants only found in {source}")


def write_quality_summary(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | tuple | set | dict):
        return bool(value)
    return True


def _source_counts(rows: list[RestaurantRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.source_name] = counts.get(row.source_name, 0) + 1
    return dict(sorted(counts.items()))


def _source_coverage(
    rows: list[RestaurantRecord],
    provider: str,
) -> dict[str, Any]:
    expected_sources = _expected_sources(rows, provider)
    groups = _match_restaurant_groups(rows)
    all_sources = set(expected_sources)

    matched_in_all = 0
    only_by_source = {source: 0 for source in expected_sources}
    missing_by_source = {source: 0 for source in expected_sources}

    for group in groups:
        group_sources = {row.source_name for row in group}
        if all_sources and all_sources.issubset(group_sources):
            matched_in_all += 1
            continue

        if len(group_sources) == 1:
            source = next(iter(group_sources))
            only_by_source[source] = only_by_source.get(source, 0) + 1

        for source in expected_sources:
            if source not in group_sources:
                missing_by_source[source] = missing_by_source.get(source, 0) + 1

    return {
        "expected_sources": expected_sources,
        "matched_restaurant_groups": len(groups),
        "matched_in_all_sources": matched_in_all,
        "not_covered_in_all_sources": len(groups) - matched_in_all,
        "only_by_source": only_by_source,
        "missing_by_source": missing_by_source,
    }


def _expected_sources(rows: list[RestaurantRecord], provider: str) -> list[str]:
    if provider == "both":
        return ["geoapify", "openstreetmap"]
    if provider == "all":
        return ["geoapify", "google_places", "openstreetmap"]
    return sorted({row.source_name for row in rows})


def _match_restaurant_groups(
    rows: list[RestaurantRecord],
) -> list[list[RestaurantRecord]]:
    groups: list[list[RestaurantRecord]] = []
    for row in rows:
        group = _find_matching_group(row, groups)
        if group is None:
            groups.append([row])
        else:
            group.append(row)
    return groups


def _find_matching_group(
    row: RestaurantRecord,
    groups: list[list[RestaurantRecord]],
) -> list[RestaurantRecord] | None:
    row_name = _normalized_name(row.name)
    if not row_name:
        return None

    for group in groups:
        representative = group[0]
        if row_name != _normalized_name(representative.name):
            continue
        if haversine_meters(
            row.latitude,
            row.longitude,
            representative.latitude,
            representative.longitude,
        ) <= 75:
            return group
    return None


@lru_cache(maxsize=4096)
def _normalized_name(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]+", "", name.lower())

