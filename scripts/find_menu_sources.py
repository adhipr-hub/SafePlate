from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.concurrency import map_concurrent
from safeplate.config import get_fetch_concurrency, get_user_agent
from safeplate.menu_sources import (
    MenuSourceError,
    build_menu_output_paths,
    discover_menu_sources_for_url,
    read_restaurant_csv,
    write_menu_sources_csv,
    write_menu_sources_json,
)
from safeplate.reports import build_report_path, write_menu_sources_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find likely menu URLs from restaurant websites."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Single restaurant website URL to inspect.")
    source.add_argument("--restaurants-csv", help="Restaurant CSV from fetch_restaurants.py.")
    parser.add_argument("--restaurant-name", help="Optional name for --url mode.")
    parser.add_argument("--restaurant-source-id", help="Optional source ID for --url mode.")
    parser.add_argument("--limit-per-site", type=int, default=25)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=get_fetch_concurrency(),
        help="How many restaurant websites to inspect in parallel.",
    )
    parser.add_argument("--out-dir", default="data")
    parser.add_argument(
        "--crawl-depth",
        type=int,
        default=2,
        help="How deep to crawl likely same-site pages. Default: 2.",
    )
    parser.add_argument(
        "--no-sitemap",
        action="store_true",
        help="Skip sitemap.xml discovery.",
    )
    parser.add_argument(
        "--location-hint",
        default=None,
        help="Location text used to prefer location-specific menu URLs.",
    )
    parser.add_argument(
        "--include-ordering-pages",
        action="store_true",
        help="Include unvalidated online ordering pages such as Toast or Square.",
    )
    parser.add_argument(
        "--include-images",
        action="store_true",
        help="Include image candidates. These are noisy until OCR is added.",
    )
    parser.add_argument(
        "--html-report",
        action="store_true",
        help="Also render a visual HTML table report next to the CSV output.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    user_agent = get_user_agent()

    if args.url:
        rows = _discover_single_url(args, user_agent)
        label = args.restaurant_name or args.url
    else:
        rows = _discover_from_csv(args, user_agent)
        label = Path(args.restaurants_csv).stem

    json_path, csv_path = build_menu_output_paths(label, Path(args.out_dir))
    write_menu_sources_json(json_path, rows)
    write_menu_sources_csv(csv_path, rows)

    print(f"Saved {len(rows)} menu source candidates")
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")
    if args.html_report:
        html_path = build_report_path(csv_path)
        write_menu_sources_report(
            csv_path=csv_path,
            html_path=html_path,
            title=f"Menu Sources - {label}",
            restaurants_csv_path=Path(args.restaurants_csv) if args.restaurants_csv else None,
        )
        print(f"HTML report: {html_path}")
    _print_summary(rows)


def _discover_single_url(args: argparse.Namespace, user_agent: str):
    try:
        return discover_menu_sources_for_url(
            website_url=args.url,
            restaurant_name=args.restaurant_name,
            restaurant_source_id=args.restaurant_source_id,
            user_agent=user_agent,
            limit=args.limit_per_site,
            include_ordering_pages=args.include_ordering_pages,
            include_images=args.include_images,
            crawl_depth=args.crawl_depth,
            use_sitemap=not args.no_sitemap,
            location_hint=args.location_hint,
        )
    except MenuSourceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _discover_from_csv(args: argparse.Namespace, user_agent: str):
    restaurants = read_restaurant_csv(Path(args.restaurants_csv))
    with_website = [
        restaurant
        for restaurant in restaurants
        if (restaurant.get("website_url") or "").strip()
    ]
    skipped_without_website = len(restaurants) - len(with_website)

    def inspect(restaurant: dict[str, str]):
        website_url = (restaurant.get("website_url") or "").strip()
        name = restaurant.get("name") or None
        source_id = restaurant.get("source_id") or None
        location_hint = args.location_hint or _location_hint_from_restaurant(restaurant)
        _safe_print(f"Inspecting website: {name or website_url}")
        try:
            return discover_menu_sources_for_url(
                website_url=website_url,
                restaurant_name=name,
                restaurant_source_id=source_id,
                user_agent=user_agent,
                limit=args.limit_per_site,
                include_ordering_pages=args.include_ordering_pages,
                include_images=args.include_images,
                crawl_depth=args.crawl_depth,
                use_sitemap=not args.no_sitemap,
                location_hint=location_hint,
                # Keep per-site concurrency polite; restaurants run in parallel already.
                max_workers=4,
            )
        except MenuSourceError as exc:
            _safe_print(f"Warning: {exc}", stream=sys.stderr)
            return exc

    results = map_concurrent(
        inspect, with_website, max_workers=max(1, args.max_workers)
    )

    rows = []
    sites_with_candidates = 0
    sites_without_candidates = 0
    for result in results:
        if isinstance(result, MenuSourceError):
            sites_without_candidates += 1
            continue
        rows.extend(result)
        if result:
            sites_with_candidates += 1
        else:
            sites_without_candidates += 1

    print("")
    print("Website inspection:")
    print(f"- {len(with_website)} websites inspected")
    print(f"- {sites_with_candidates} websites with menu candidates")
    print(f"- {sites_without_candidates} websites without menu candidates")
    print(f"- {skipped_without_website} restaurants skipped without website_url")
    return rows


def _print_summary(rows) -> None:
    counts = {}
    for row in rows:
        counts[row.source_type] = counts.get(row.source_type, 0) + 1

    print("")
    print("Menu source types:")
    if not counts:
        print("- none found")
        return
    for source_type, count in sorted(counts.items()):
        print(f"- {count} {source_type}")

    restaurant_names = {
        row.restaurant_source_id or row.restaurant_name
        for row in rows
        if row.restaurant_source_id or row.restaurant_name
    }
    validated_restaurant_names = {
        row.restaurant_source_id or row.restaurant_name
        for row in rows
        if (row.restaurant_source_id or row.restaurant_name)
        and row.validation_status == "validated"
    }
    print("")
    print("Restaurant coverage:")
    print(f"- {len(restaurant_names)} restaurants had at least one menu candidate")
    print(
        "- "
        f"{len(validated_restaurant_names)} restaurants had at least one validated menu candidate"
    )


def _safe_print(message: str, stream=None) -> None:
    stream = stream or sys.stdout
    encoding = stream.encoding or "utf-8"
    safe_message = message.encode(encoding, errors="replace").decode(encoding)
    print(safe_message, file=stream)


def _location_hint_from_restaurant(restaurant: dict[str, str]) -> str | None:
    address = restaurant.get("address") or ""
    for part in address.split(","):
        cleaned = part.strip()
        if cleaned and not any(char.isdigit() for char in cleaned):
            return cleaned
    return None


if __name__ == "__main__":
    main()
