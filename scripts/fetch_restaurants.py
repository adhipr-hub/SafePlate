from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.config import (
    DEFAULT_LIMIT,
    DEFAULT_PROVIDER,
    DEFAULT_RADIUS_METERS,
    get_geoapify_api_key,
    get_google_places_api_key,
    get_user_agent,
)
from safeplate.export import build_output_paths, write_csv, write_json
from safeplate.geo import geocode_location
from safeplate.providers.geoapify import GeoapifyError
from safeplate.providers.geoapify import GEOAPIFY_CATEGORIES
from safeplate.providers.geoapify import fetch_nearby_restaurants as fetch_geoapify
from safeplate.providers.google_places import GooglePlacesError
from safeplate.providers.google_places import GOOGLE_INCLUDED_TYPES
from safeplate.providers.google_places import fetch_nearby_restaurants as fetch_google
from safeplate.providers.osm import OverpassError, fetch_nearby_restaurants
from safeplate.quality import (
    build_quality_summary,
    print_quality_summary,
    write_quality_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch nearby restaurant metadata."
    )
    parser.add_argument(
        "--location",
        required=True,
        help='Location to search around, such as "Berkeley, CA".',
    )
    parser.add_argument(
        "--food-only",
        action="store_true",
        help="Drop non-food POIs (malls, hotels, cinemas) using provider type tags.",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=DEFAULT_RADIUS_METERS,
        help=f"Search radius in meters. Default: {DEFAULT_RADIUS_METERS}.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum number of rows to save. Default: {DEFAULT_LIMIT}.",
    )
    parser.add_argument(
        "--out-dir",
        default="data",
        help="Directory where JSON and CSV outputs should be saved.",
    )
    parser.add_argument(
        "--provider",
        choices=["osm", "geoapify", "google", "both", "all"],
        default=DEFAULT_PROVIDER,
        help=f"Restaurant data provider. Default: {DEFAULT_PROVIDER}.",
    )
    parser.add_argument(
        "--geoapify-categories",
        default=",".join(GEOAPIFY_CATEGORIES),
        help=(
            "Comma-separated Geoapify categories. "
            "Example: catering or catering.restaurant,catering.cafe."
        ),
    )
    parser.add_argument(
        "--geoapify-conditions",
        default="",
        help=(
            "Optional comma-separated Geoapify conditions, such as "
            "wheelchair.yes. Used only with --provider geoapify or both."
        ),
    )
    parser.add_argument(
        "--google-included-types",
        default=",".join(GOOGLE_INCLUDED_TYPES),
        help=(
            "Comma-separated Google Places included types. "
            "Example: restaurant,cafe,meal_takeaway."
        ),
    )
    parser.add_argument(
        "--google-include-atmosphere-fields",
        action="store_true",
        help=(
            "Request extra Google service-option fields such as takeout, dine-in, "
            "and servesVegetarianFood. This can increase Google Places billing tier."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    user_agent = get_user_agent()
    geoapify_api_key = _get_geoapify_key_or_exit(args)
    google_api_key = _get_google_key_or_exit(args)

    print(f"Geocoding location: {args.location}")
    coordinates = geocode_location(args.location, user_agent=user_agent)
    print(
        "Found coordinates: "
        f"{coordinates.latitude:.6f}, {coordinates.longitude:.6f}"
    )

    print(
        f"Fetching nearby restaurants from {args.provider}: "
        f"radius={args.radius}m limit={args.limit}"
    )
    try:
        rows = _fetch_rows(
            args,
            coordinates,
            user_agent,
            geoapify_api_key,
            google_api_key,
        )
    except (OverpassError, GeoapifyError, GooglePlacesError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.food_only:
        from safeplate.places import is_food_place
        before = len(rows)
        rows = [row for row in rows if is_food_place(row.categories)]
        print(f"Filtered to food establishments: {len(rows)}/{before} kept")

    json_path, csv_path, summary_path = build_output_paths(
        args.location,
        Path(args.out_dir),
    )
    summary = build_quality_summary(
        rows=rows,
        location=args.location,
        radius_meters=args.radius,
        limit=args.limit,
        provider=args.provider,
    )

    write_json(json_path, rows)
    write_csv(csv_path, rows)
    write_quality_summary(summary_path, summary)

    print(f"Saved {len(rows)} rows")
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")
    print(f"Summary: {summary_path}")
    print_quality_summary(summary)


def _fetch_rows(
    args: argparse.Namespace,
    coordinates,
    user_agent: str,
    geoapify_api_key: str | None,
    google_api_key: str | None,
):
    if args.provider == "osm":
        return fetch_nearby_restaurants(
            latitude=coordinates.latitude,
            longitude=coordinates.longitude,
            radius_meters=args.radius,
            limit=args.limit,
            user_agent=user_agent,
        )

    if args.provider == "geoapify":
        return fetch_geoapify(
            latitude=coordinates.latitude,
            longitude=coordinates.longitude,
            radius_meters=args.radius,
            limit=args.limit,
            api_key=geoapify_api_key or "",
            user_agent=user_agent,
            categories=_split_csv_arg(args.geoapify_categories),
            conditions=_split_csv_arg(args.geoapify_conditions),
        )

    if args.provider == "google":
        return fetch_google(
            latitude=coordinates.latitude,
            longitude=coordinates.longitude,
            radius_meters=args.radius,
            limit=args.limit,
            api_key=google_api_key or "",
            user_agent=user_agent,
            included_types=_split_csv_arg(args.google_included_types),
            include_atmosphere_fields=args.google_include_atmosphere_fields,
        )

    rows = []
    rows.extend(fetch_nearby_restaurants(
        latitude=coordinates.latitude,
        longitude=coordinates.longitude,
        radius_meters=args.radius,
        limit=args.limit,
        user_agent=user_agent,
    ))
    rows.extend(fetch_geoapify(
        latitude=coordinates.latitude,
        longitude=coordinates.longitude,
        radius_meters=args.radius,
        limit=args.limit,
        api_key=geoapify_api_key or "",
        user_agent=user_agent,
        categories=_split_csv_arg(args.geoapify_categories),
        conditions=_split_csv_arg(args.geoapify_conditions),
    ))

    if args.provider == "all":
        rows.extend(fetch_google(
            latitude=coordinates.latitude,
            longitude=coordinates.longitude,
            radius_meters=args.radius,
            limit=args.limit,
            api_key=google_api_key or "",
            user_agent=user_agent,
            included_types=_split_csv_arg(args.google_included_types),
            include_atmosphere_fields=args.google_include_atmosphere_fields,
        ))

    return rows


def _get_geoapify_key_or_exit(args: argparse.Namespace) -> str | None:
    if args.provider not in ["geoapify", "both", "all"]:
        return None

    api_key = get_geoapify_api_key()
    if api_key:
        return api_key

    print(
        "Error: GEOAPIFY_API_KEY is not set. Create a free Geoapify key, then run "
        '$env:GEOAPIFY_API_KEY="your-key" before using --provider geoapify or both.',
        file=sys.stderr,
    )
    raise SystemExit(1)


def _get_google_key_or_exit(args: argparse.Namespace) -> str | None:
    if args.provider not in ["google", "all"]:
        return None

    api_key = get_google_places_api_key()
    if api_key:
        return api_key

    print(
        "Error: GOOGLE_PLACES_API_KEY is not set. Set it before using "
        "--provider google or all.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _split_csv_arg(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    main()
