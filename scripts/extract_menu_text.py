from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.config import get_gemini_api_key, get_gemini_model, get_user_agent
from safeplate.menu_text import (
    build_menu_item_output_paths,
    build_menu_text_output_paths,
    extract_menu_items_from_sources,
    extract_menu_text_from_sources,
    read_csv_rows,
    write_menu_items_csv,
    write_menu_items_json,
    write_menu_text_csv,
    write_menu_text_json,
)
from safeplate.reports import build_extraction_report_path, write_menu_extraction_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract visible text from validated menu-source pages."
    )
    parser.add_argument("--menu-sources-csv", required=True)
    parser.add_argument("--out-dir", default="data")
    parser.add_argument("--include-unvalidated", action="store_true")
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--max-items-per-source", type=int, default=250)
    parser.add_argument(
        "--html-report",
        action="store_true",
        help="Also render an HTML report showing extraction contribution statistics.",
    )
    parser.add_argument(
        "--use-llm-fallback",
        action="store_true",
        help=(
            "When the static parser and embedded-JSON scan find no items for an "
            "HTML menu page, let Gemini fetch and read the URL (needs GEMINI_API_KEY)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    menu_source_rows = read_csv_rows(Path(args.menu_sources_csv))
    text_rows = extract_menu_text_from_sources(
        menu_source_rows=menu_source_rows,
        user_agent=get_user_agent(),
        include_unvalidated=args.include_unvalidated,
        max_chars=args.max_chars,
    )
    item_rows = extract_menu_items_from_sources(
        menu_source_rows=menu_source_rows,
        user_agent=get_user_agent(),
        include_unvalidated=args.include_unvalidated,
        max_items_per_source=args.max_items_per_source,
        use_llm_fallback=args.use_llm_fallback,
        gemini_api_key=get_gemini_api_key() if args.use_llm_fallback else None,
        gemini_model=get_gemini_model(),
    )

    label = Path(args.menu_sources_csv).stem
    out_dir = Path(args.out_dir)
    json_path, csv_path = build_menu_text_output_paths(label, out_dir)
    item_json_path, item_csv_path = build_menu_item_output_paths(label, out_dir)

    write_menu_text_json(json_path, text_rows)
    write_menu_text_csv(csv_path, text_rows)
    write_menu_items_json(item_json_path, item_rows)
    write_menu_items_csv(item_csv_path, item_rows)

    print(f"Saved {len(text_rows)} menu text records")
    print(f"Text JSON: {json_path}")
    print(f"Text CSV:  {csv_path}")
    print(f"Saved {len(item_rows)} menu item candidates")
    print(f"Item JSON: {item_json_path}")
    print(f"Item CSV:  {item_csv_path}")
    if args.html_report:
        html_path = build_extraction_report_path(item_csv_path)
        write_menu_extraction_report(
            text_csv_path=csv_path,
            item_csv_path=item_csv_path,
            html_path=html_path,
            title=f"Menu Extraction - {label}",
        )
        print(f"HTML report: {html_path}")


if __name__ == "__main__":
    main()
