from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.concurrency import map_concurrent
from safeplate.config import (
    get_gemini_api_key,
    get_gemini_concurrency,
    get_gemini_model,
)
from safeplate.coerce import chunks as _chunks
from safeplate.coerce import float_value as _float_value
from safeplate.gemini_menu import (
    GeminiRestaurantEvidence,
    GeminiMenuError,
    build_gemini_output_paths,
    extract_restaurant_candidate_evidence_with_gemini,
    extract_restaurant_evidence_with_gemini,
    group_menu_item_rows_by_restaurant,
    group_menu_text_rows_by_restaurant,
    menu_item_rows_to_candidates,
    menu_text_rows_to_sources,
    write_gemini_evidence_json,
    write_gemini_items_csv,
    write_gemini_notes_csv,
)
from safeplate.io import read_csv_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use Gemini to extract structured menu evidence from cleaned menu text."
        )
    )
    parser.add_argument(
        "--menu-text-csv",
        help=(
            "CSV with cleaned source text. Required for "
            "text-only mode and optional context for candidate mode."
        ),
    )
    parser.add_argument(
        "--menu-items-csv",
        help=(
            "CSV with deterministic menu item candidates. "
            "When provided, Gemini cleans/enriches candidates instead of discovering "
            "items from raw text."
        ),
    )
    parser.add_argument("--out-dir", default="data")
    parser.add_argument(
        "--model",
        default=get_gemini_model(),
        help="Gemini model to use. Default comes from GEMINI_MODEL or gemini-3.1-flash-lite.",
    )
    parser.add_argument(
        "--max-chars-per-restaurant",
        type=int,
        default=18000,
        help="Maximum cleaned evidence characters sent to Gemini per restaurant.",
    )
    parser.add_argument(
        "--limit-restaurants",
        type=int,
        default=0,
        help="Optional limit for small tests. 0 means no limit.",
    )
    parser.add_argument(
        "--max-candidates-per-restaurant",
        type=int,
        default=200,
        help="Maximum deterministic item candidates sent to Gemini per restaurant.",
    )
    parser.add_argument(
        "--candidate-chunk-size",
        type=int,
        default=50,
        help="Number of deterministic candidates sent in each Gemini call.",
    )
    parser.add_argument(
        "--allow-ungrounded",
        action="store_true",
        help=(
            "Disable the grounding guardrail. By default, any item/note whose "
            "evidence quote is not found verbatim in the source menu text is dropped."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=get_gemini_concurrency(),
        help=(
            "How many restaurants to send to Gemini in parallel. Keep modest to "
            "respect API rate limits. Override with SAFEPLATE_GEMINI_CONCURRENCY."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = get_gemini_api_key()
    if not api_key:
        print(
            "Error: GEMINI_API_KEY is not set. Set it before running Gemini extraction.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not args.menu_text_csv and not args.menu_items_csv:
        print(
            "Error: provide --menu-text-csv for text-only mode or --menu-items-csv for candidate mode.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    menu_text_rows = (
        read_csv_rows(Path(args.menu_text_csv))
        if args.menu_text_csv
        else []
    )
    if args.menu_items_csv:
        output_rows = _run_candidate_mode(args, api_key, menu_text_rows)
        output_label = f"candidate_{Path(args.menu_items_csv).stem}"
    else:
        output_rows = _run_text_only_mode(args, api_key, menu_text_rows)
        output_label = Path(args.menu_text_csv).stem

    json_path, item_csv_path, note_csv_path = build_gemini_output_paths(
        output_label,
        Path(args.out_dir),
    )
    write_gemini_evidence_json(json_path, output_rows)
    write_gemini_items_csv(item_csv_path, output_rows)
    write_gemini_notes_csv(note_csv_path, output_rows)

    item_count = sum(
        len(row.extraction.get("menu_items", []))
        for row in output_rows
    )
    note_count = sum(
        len(row.extraction.get("restaurant_notes", []))
        for row in output_rows
    )

    print(f"Saved {len(output_rows)} restaurant evidence records")
    print(f"Saved {item_count} Gemini menu item rows")
    print(f"Saved {note_count} Gemini restaurant note rows")
    print(f"JSON:  {json_path}")
    print(f"Items: {item_csv_path}")
    print(f"Notes: {note_csv_path}")


def _run_text_only_mode(
    args: argparse.Namespace,
    api_key: str,
    menu_text_rows: list[dict[str, str]],
):
    groups = group_menu_text_rows_by_restaurant(menu_text_rows)
    if args.limit_restaurants:
        groups = groups[: args.limit_restaurants]

    def extract(group):
        restaurant_name, restaurant_source_id, rows = group
        sources = menu_text_rows_to_sources(rows)
        if not sources:
            return None
        print(f"Extracting Gemini menu evidence: {restaurant_name}")
        try:
            return extract_restaurant_evidence_with_gemini(
                restaurant_name=restaurant_name,
                restaurant_source_id=restaurant_source_id,
                sources=sources,
                api_key=api_key,
                model=args.model,
                max_chars=args.max_chars_per_restaurant,
                require_grounded_quotes=not args.allow_ungrounded,
            )
        except GeminiMenuError as exc:
            print(f"Warning: {restaurant_name}: {exc}", file=sys.stderr)
            return None

    results = map_concurrent(extract, groups, max_workers=max(1, args.concurrency))
    return [row for row in results if row is not None]


def _run_candidate_mode(
    args: argparse.Namespace,
    api_key: str,
    menu_text_rows: list[dict[str, str]],
):
    menu_item_rows = read_csv_rows(Path(args.menu_items_csv))
    groups = group_menu_item_rows_by_restaurant(menu_item_rows)
    if args.limit_restaurants:
        groups = groups[: args.limit_restaurants]

    context_by_restaurant = {
        (restaurant_source_id, restaurant_name): rows
        for restaurant_name, restaurant_source_id, rows
        in group_menu_text_rows_by_restaurant(menu_text_rows)
    }

    def extract(group):
        restaurant_name, restaurant_source_id, rows = group
        candidates = menu_item_rows_to_candidates(
            rows,
            max_candidates=args.max_candidates_per_restaurant,
        )
        if not candidates:
            return None
        context_sources = menu_text_rows_to_sources(
            context_by_restaurant.get((restaurant_source_id, restaurant_name), [])
        )
        print(
            f"Extracting Gemini candidate evidence: {restaurant_name} "
            f"({len(candidates)} candidates)"
        )
        chunk_results = []
        for chunk_index, chunk in enumerate(
            _chunks(candidates, args.candidate_chunk_size),
            start=1,
        ):
            print(
                f"  chunk {chunk_index}: candidates "
                f"{chunk[0]['candidate_id']}..{chunk[-1]['candidate_id']}"
            )
            try:
                chunk_results.append(
                    extract_restaurant_candidate_evidence_with_gemini(
                        restaurant_name=restaurant_name,
                        restaurant_source_id=restaurant_source_id,
                        candidates=chunk,
                        context_sources=(context_sources if chunk_index == 1 else []),
                        api_key=api_key,
                        model=args.model,
                        max_context_chars=args.max_chars_per_restaurant,
                        require_grounded_quotes=not args.allow_ungrounded,
                    )
                )
            except GeminiMenuError as exc:
                print(
                    f"Warning: {restaurant_name} chunk {chunk_index}: {exc}",
                    file=sys.stderr,
                )
        if chunk_results:
            return _merge_candidate_chunk_results(chunk_results)
        return None

    results = map_concurrent(extract, groups, max_workers=max(1, args.concurrency))
    return [row for row in results if row is not None]


def _merge_candidate_chunk_results(rows):
    first = rows[0]
    menu_items = []
    evidence_sources_by_key = {}
    notes_by_key = {}
    warnings = []
    signal_rows = []

    for row in rows:
        extraction = row.extraction
        menu_items.extend(extraction.get("menu_items", []))
        warnings.extend(extraction.get("extraction_warnings", []))
        signal_rows.append(extraction.get("restaurant_signals", {}))
        for source in row.evidence_sources:
            key = (
                source.get("menu_source_url", ""),
                source.get("source_type", ""),
                source.get("extraction_method", ""),
            )
            evidence_sources_by_key[key] = source
        for note in extraction.get("restaurant_notes", []):
            key = (
                note.get("note_type", ""),
                note.get("note_text", ""),
                note.get("source_url", ""),
            )
            notes_by_key[key] = note

    return GeminiRestaurantEvidence(
        restaurant_name=first.restaurant_name,
        restaurant_source_id=first.restaurant_source_id,
        model=first.model,
        extracted_at=first.extracted_at,
        evidence_sources=list(evidence_sources_by_key.values()),
        extraction={
            "restaurant_name": first.restaurant_name,
            "menu_items": menu_items,
            "restaurant_notes": list(notes_by_key.values()),
            "restaurant_signals": _merge_restaurant_signals(signal_rows),
            "extraction_warnings": list(dict.fromkeys(warnings)),
        },
    )


def _merge_restaurant_signals(rows):
    bool_fields = [
        "has_allergy_disclaimer",
        "has_cross_contact_warning",
        "mentions_staff_allergy_instruction",
        "has_vegan_options",
        "has_vegetarian_options",
        "has_gluten_free_options",
        "has_tofu_or_plant_protein_options",
    ]
    return {
        **{
            field: any(bool(row.get(field)) for row in rows)
            for field in bool_fields
        },
        "evidence_confidence": max(
            (
                _float_value(row.get("evidence_confidence"))
                for row in rows
            ),
            default=0.0,
        ),
    }




if __name__ == "__main__":
    main()
