"""Offline, re-runnable quality gate for the multi-allergen + diet layer (Task 11).

Two jobs:

1. Score a small FIXED set of representative restaurants for a matrix of
   allergens (milk, gluten, egg, sesame) + diets (vegan, vegetarian), and print
   a summary table of tier / verdict counts. No network, no API keys -- the
   menus are hand-built ``MenuItemRecord`` fixtures below.

2. NUT-PARITY GUARD (the important part): score the same fixed restaurants for
   a fixed nuts profile and compare (tier, overall_risk) per restaurant against
   a committed baseline JSON (``eval/baseline_nut_parity.json``). This is the
   thing that proves multi-allergen work never regressed the nut scorer, which
   is the one gate that must never silently drift.

    python eval/bench_multi_allergen.py                  # score + assert parity
    python eval/bench_multi_allergen.py --update-baseline # (re)write the baseline

On a parity mismatch the script prints a diff and exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safeplate.allergen_score import (  # noqa: E402
    AllergenPref, Severity, UserProfile, score_restaurant_for_user,
)
from safeplate.diet_score import assess_diet  # noqa: E402
from safeplate.menu_text import MenuItemRecord  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent / "baseline_nut_parity.json"

ALLERGEN_MATRIX = ("milk", "gluten", "egg", "sesame")
DIET_MATRIX = ("vegan", "vegetarian")


def _item(
    item_name,
    *,
    allergen_terms=(),
    extraction_method="allergen_matrix",
    matrix_allergen_columns=(),
    dietary_terms=(),
):
    """Build a real MenuItemRecord (all required fields present). The dish-name
    field is ``item_name``, NOT ``name``."""
    return MenuItemRecord(
        restaurant_name="", restaurant_source_id="", menu_source_url="", category="",
        item_name=item_name, description="", price="", dietary_terms=list(dietary_terms),
        allergen_terms=list(allergen_terms), source_type="", extraction_method=extraction_method,
        confidence=0.9, raw_text="", fetched_at="",
        matrix_allergen_columns=tuple(matrix_allergen_columns),
    )


# --------------------------------------------------------------------------- #
# Fixed restaurant set -- deterministic, in-file, no network.
# --------------------------------------------------------------------------- #
RESTAURANTS = [
    {
        "name": "Trattoria Milano",
        "cuisines": ["italian"],
        "region": "US",
        "menu_items": [
            _item("Fettuccine Alfredo", allergen_terms=["milk", "gluten"],
                  matrix_allergen_columns=("milk", "gluten", "egg", "sesame", "peanut", "tree nut")),
            _item("Garden Salad", allergen_terms=[],
                  matrix_allergen_columns=("milk", "gluten", "egg", "sesame", "peanut", "tree nut"),
                  dietary_terms=["vegan"]),
            _item("Margherita Pizza", allergen_terms=["milk", "gluten"],
                  matrix_allergen_columns=("milk", "gluten", "egg", "sesame", "peanut", "tree nut")),
        ],
    },
    {
        "name": "Bangkok Kitchen",
        "cuisines": ["thai"],
        "region": "US",
        "menu_items": [
            _item("Pad Thai", allergen_terms=["peanut", "egg"],
                  matrix_allergen_columns=("peanut", "tree nut", "egg", "milk", "gluten", "sesame")),
            _item("Tom Yum Soup", allergen_terms=["fish"],
                  matrix_allergen_columns=("peanut", "tree nut", "egg", "milk", "gluten", "sesame")),
            _item("Som Tam (Papaya Salad)", allergen_terms=["peanut"],
                  matrix_allergen_columns=("peanut", "tree nut", "egg", "milk", "gluten", "sesame")),
        ],
    },
    {
        "name": "Green Leaf Vegan Cafe",
        "cuisines": ["vegan", "american"],
        "region": "US",
        "menu_items": [
            _item("Buddha Bowl", allergen_terms=["sesame"],
                  matrix_allergen_columns=("milk", "egg", "gluten", "sesame", "peanut", "tree nut"),
                  dietary_terms=["vegan"]),
            _item("Cashew Cheese Plate", allergen_terms=["tree nut"],
                  matrix_allergen_columns=("milk", "egg", "gluten", "sesame", "peanut", "tree nut"),
                  dietary_terms=["vegan"]),
            _item("Lentil Soup", allergen_terms=[],
                  matrix_allergen_columns=("milk", "egg", "gluten", "sesame", "peanut", "tree nut"),
                  dietary_terms=["vegan"]),
        ],
    },
    {
        "name": "Downtown Burger Joint",
        "cuisines": ["american"],
        "region": "US",
        "menu_items": [
            _item("Beef Burger", allergen_terms=["milk", "gluten", "sesame"],
                  matrix_allergen_columns=("milk", "gluten", "sesame", "egg", "peanut", "tree nut")),
            _item("Crispy Fries", allergen_terms=[],
                  matrix_allergen_columns=("milk", "gluten", "sesame", "egg", "peanut", "tree nut")),
            _item("Chicken Caesar Salad", allergen_terms=["egg", "milk", "fish"],
                  matrix_allergen_columns=("milk", "gluten", "sesame", "egg", "peanut", "tree nut")),
        ],
    },
    {
        "name": "Sakura Sushi",
        "cuisines": ["japanese"],
        "region": "US",
        "menu_items": [
            _item("California Roll", allergen_terms=["shellfish", "egg"],
                  matrix_allergen_columns=("egg", "shellfish", "fish", "gluten", "milk", "sesame")),
            _item("Peanut Sauce Udon", allergen_terms=["peanut", "gluten"],
                  matrix_allergen_columns=("egg", "shellfish", "fish", "gluten", "milk", "sesame")),
            _item("Edamame", allergen_terms=[], matrix_allergen_columns=(
                "egg", "shellfish", "fish", "gluten", "milk", "sesame"), dietary_terms=["vegan"]),
        ],
    },
    {
        "name": "Bavarian Bakery",
        "cuisines": ["german"],
        "region": "US",
        "menu_items": [
            _item("Pretzel", allergen_terms=["gluten"],
                  matrix_allergen_columns=("gluten", "milk", "egg", "sesame")),
            _item("Black Forest Cake", allergen_terms=["milk", "egg", "gluten", "tree nut"],
                  matrix_allergen_columns=("gluten", "milk", "egg", "sesame", "tree nut")),
            _item("Sesame Seed Roll", allergen_terms=["sesame", "gluten"],
                  matrix_allergen_columns=("gluten", "milk", "egg", "sesame")),
        ],
    },
    {
        "name": "No Menu Diner",
        "cuisines": ["american"],
        "region": "US",
        "menu_items": [],  # no grounded evidence at all -- exercises the prior-only path
    },
]

NUT_PROFILE = UserProfile.for_nuts(Severity.ANAPHYLAXIS)


# --------------------------------------------------------------------------- #
# Allergen + diet summary tables
# --------------------------------------------------------------------------- #
def _allergen_tier_counts() -> dict[str, Counter]:
    """allergen -> Counter({tier: count}) across all restaurants."""
    counts: dict[str, Counter] = {a: Counter() for a in ALLERGEN_MATRIX}
    for allergen in ALLERGEN_MATRIX:
        profile = UserProfile(allergens=(AllergenPref(allergen=allergen, severity=Severity.ALLERGY),))
        for r in RESTAURANTS:
            a = score_restaurant_for_user(
                profile, cuisines=r["cuisines"], region=r["region"], menu_items=r["menu_items"],
            )
            counts[allergen][a.tier] += 1
    return counts


def _diet_verdict_counts() -> dict[str, Counter]:
    counts: dict[str, Counter] = {d: Counter() for d in DIET_MATRIX}
    for diet in DIET_MATRIX:
        for r in RESTAURANTS:
            d = assess_diet(diet, menu_items=r["menu_items"], cuisines=r["cuisines"])
            counts[diet][d.verdict] += 1
    return counts


def _print_allergen_table(counts: dict[str, Counter]) -> None:
    tiers = ["avoid", "caution", "likely_ok"]
    header = f"{'allergen':<10} " + " ".join(f"{t:>10}" for t in tiers)
    print(header)
    print("-" * len(header))
    for allergen in ALLERGEN_MATRIX:
        row = f"{allergen:<10} " + " ".join(f"{counts[allergen].get(t, 0):>10}" for t in tiers)
        print(row)
    print()


def _print_diet_table(counts: dict[str, Counter]) -> None:
    verdicts = ["good_options", "limited", "not_compatible", "unknown"]
    header = f"{'diet':<12} " + " ".join(f"{v:>15}" for v in verdicts)
    print(header)
    print("-" * len(header))
    for diet in DIET_MATRIX:
        row = f"{diet:<12} " + " ".join(f"{counts[diet].get(v, 0):>15}" for v in verdicts)
        print(row)
    print()


# --------------------------------------------------------------------------- #
# Nut-parity guard
# --------------------------------------------------------------------------- #
def _current_nut_parity() -> dict[str, list]:
    """restaurant name -> [tier, round(overall_risk, 4)] for the fixed nuts profile."""
    out = {}
    for r in RESTAURANTS:
        a = score_restaurant_for_user(
            NUT_PROFILE, cuisines=r["cuisines"], region=r["region"], menu_items=r["menu_items"],
        )
        out[r["name"]] = [a.tier, round(a.overall_risk, 4)]
    return out


def _write_baseline(current: dict[str, list]) -> None:
    BASELINE_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"baseline written -> {BASELINE_PATH}")


def _assert_parity(current: dict[str, list]) -> None:
    if not BASELINE_PATH.exists():
        _write_baseline(current)
        return

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    mismatches = []
    for name, cur_val in current.items():
        base_val = baseline.get(name)
        if base_val != cur_val:
            mismatches.append((name, base_val, cur_val))
    missing = [name for name in baseline if name not in current]

    if not mismatches and not missing:
        print(f"NUT-PARITY OK: {len(current)} restaurants match {BASELINE_PATH.name}")
        return

    print("NUT-PARITY FAILURE -- nut scoring drifted from the committed baseline:")
    for name, base_val, cur_val in mismatches:
        print(f"  {name}: baseline={base_val} current={cur_val}")
    for name in missing:
        print(f"  {name}: present in baseline but missing from current run")
    print(
        "\nIf this drift is INTENTIONAL, regenerate the baseline with "
        "`python eval/bench_multi_allergen.py --update-baseline` and review the diff."
    )
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="(re)write eval/baseline_nut_parity.json from the current nut scores",
    )
    args = parser.parse_args()

    print(f"Multi-allergen + diet bench -- {len(RESTAURANTS)} fixed restaurants\n")

    print("=== Allergen tier counts (milk/gluten/egg/sesame) ===")
    _print_allergen_table(_allergen_tier_counts())

    print("=== Diet verdict counts (vegan/vegetarian) ===")
    _print_diet_table(_diet_verdict_counts())

    current = _current_nut_parity()
    print("=== Nut-parity guard ===")
    if args.update_baseline:
        _write_baseline(current)
    else:
        _assert_parity(current)


if __name__ == "__main__":
    main()
