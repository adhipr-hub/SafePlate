from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.reports import build_report_path, write_menu_sources_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a visual HTML report for menu-source CSV findings."
    )
    parser.add_argument("--menu-sources-csv", required=True)
    parser.add_argument("--restaurants-csv", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--title", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = Path(args.menu_sources_csv)
    out_dir = Path(args.out_dir) if args.out_dir else None
    html_path = build_report_path(csv_path, out_dir)

    write_menu_sources_report(
        csv_path=csv_path,
        html_path=html_path,
        title=args.title,
        restaurants_csv_path=Path(args.restaurants_csv) if args.restaurants_csv else None,
    )

    print(f"HTML report: {html_path}")


if __name__ == "__main__":
    main()
