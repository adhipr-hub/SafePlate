"""JS allergen-tool recovery benchmark.

Chains commonly serve per-dish allergen data through JavaScript tools (filters,
nutrition calculators) that static fetching can't read. This measures, per chain,
how many dishes-with-allergens v2 recovers end-to-end (discover -> acquire ->
extract) and WHICH method delivered them -- so we can see each tier's contribution
as we add Tier 0/1/2.

Re-runnable: discovery + LLM extraction are cached on disk, so repeat runs are
cheap. First run makes live calls (needs GEMINI + BRAVE keys).

Usage:
  python eval/bench_js_allergens.py            # all chains
  python eval/bench_js_allergens.py --limit 4
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.config import (
    get_brave_search_api_key,
    get_gemini_api_key,
    get_gemini_model,
    get_user_agent,
)
from safeplate.extraction2.discover import discover_and_extract

# (name, homepage) -- chains known to publish allergen data, several via JS tools.
CHAINS = [
    ("Chipotle", "https://www.chipotle.com"),
    ("Wagamama", "https://www.wagamama.com"),
    ("Five Guys", "https://www.fiveguys.com"),
    ("Nando's", "https://www.nandos.co.uk"),
    ("Pret A Manger", "https://www.pret.co.uk"),
    ("Greggs", "https://www.greggs.co.uk"),
    ("Pizza Express", "https://www.pizzaexpress.com"),
    ("Leon", "https://leon.co"),
    ("Pizza Hut UK", "https://www.pizzahut.co.uk"),
    ("Itsu", "https://www.itsu.com"),
]

ALLERGEN_THRESHOLD = 3  # a chain "recovered" if >=3 dishes carry allergen tags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    ua = get_user_agent()
    key = get_gemini_api_key()
    model = get_gemini_model()
    brave = get_brave_search_api_key()
    if not key:
        print("GEMINI_API_KEY not set")
        return

    chains = CHAINS[: args.limit] if args.limit else CHAINS
    print(f"{'CHAIN':16}{'cand':>5}{'items':>7}{'allg-dishes':>12}  methods (allergen-bearing)")
    print("-" * 84)
    recovered = 0
    method_totals: Counter = Counter()
    for name, url in chains:
        try:
            cands, result = discover_and_extract(
                url, user_agent=ua, restaurant_name=name,
                api_key=key, model=model, brave_api_key=brave,
            )
        except Exception as exc:
            print(f"{name:16}  ERROR: {str(exc)[:50]}")
            continue
        allergen_items = [it for it in result.items if it.allergen_terms]
        methods = Counter(it.extraction_method for it in allergen_items)
        method_totals.update(methods)
        if len(allergen_items) >= ALLERGEN_THRESHOLD:
            recovered += 1
        method_str = ", ".join(f"{m}:{c}" for m, c in methods.most_common()) or "-"
        print(f"{name:16}{len(cands):>5}{len(result.items):>7}{len(allergen_items):>12}  {method_str}")
    print("-" * 84)
    rate = recovered / len(chains) * 100 if chains else 0
    print(f"RECOVERY: {recovered}/{len(chains)} chains with >={ALLERGEN_THRESHOLD} "
          f"dishes-with-allergens ({rate:.0f}%)")
    print(f"allergen-bearing items by method: {dict(method_totals)}")


if __name__ == "__main__":
    main()
