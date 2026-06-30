"""Safety eval: the NORTH-STAR metric we'd never measured -- the FALSE-NEGATIVE rate
(saying "okay" when nuts are actually present), per scoring engine.

For a nut-allergic user the asymmetry is everything: a false negative (a nut-present
place scored 'likely_ok') is dangerous; a false positive (a nut-free place scored
'avoid') is merely annoying. We label scenarios with ground truth and measure both,
for the deterministic (v2) scorer and the hybrid LLM (v3) scorer.

    python eval/safety_eval.py          # v3 column needs Gemini quota; falls back to v2

Verdict policy: a POSITIVE (nuts present) is a MISS only if scored 'likely_ok'
('caution' still warns the user). A NEGATIVE (genuinely nut-free) is an OVER-WARN if
scored 'avoid'.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.datasets import labeled_restaurants as ds  # noqa: E402
from safeplate.allergen_score import (  # noqa: E402
    Severity, UserProfile, score_restaurant_for_user,
)
from safeplate.allergen_score_llm import score_restaurant_with_llm  # noqa: E402
from safeplate.config import get_gemini_api_key, get_gemini_model  # noqa: E402


NUT = UserProfile.for_nuts(Severity.ALLERGY)

# Pull the labeled benchmark (curated, but far larger + more diverse than the old
# inline 8). (label, ground_truth, kwargs).
SCENARIOS = [(c["name"], c["truth"], ds.score_kwargs(c)) for c in ds.LABELED]


def _classify(tier, truth):
    if truth == "pos":
        return "MISS" if tier == "likely_ok" else "ok"        # caution/avoid both warn
    return "overwarn" if tier == "avoid" else "ok"            # neg scored avoid = over-warn


def main() -> None:
    api_key, model = get_gemini_api_key(), get_gemini_model()
    rows = []  # (label, cuisine, truth, det_tier, hyb_tier, det_class, hyb_class)
    for label, truth, kw in SCENARIOS:
        cuisine = (kw.get("cuisines") or ["?"])[0]
        grounded = bool(kw.get("menu_items"))  # real evidence vs cuisine-only (self-referential)
        det = score_restaurant_for_user(NUT, **kw)
        hyb = score_restaurant_with_llm(NUT, api_key=api_key, model=model, **kw)
        rows.append((label, cuisine, truth, det.tier, hyb.tier,
                     _classify(det.tier, truth), _classify(hyb.tier, truth), grounded))

    print(f"Benchmark: {len(rows)} labeled cases "
          f"({sum(1 for r in rows if r[2]=='pos')} pos / "
          f"{sum(1 for r in rows if r[2]=='neg')} neg)\n")

    # Only the failures are worth eyeballing; the full table is large.
    bad = [r for r in rows if r[5] != "ok" or r[6] != "ok"]
    if bad:
        print("FAILURES (a MISS is a positive scored 'likely_ok'; over-warn is a "
              "negative scored 'avoid'):")
        print(f"  {'scenario':34s} {'cuisine':14s} {'truth':5s} {'v2':9s} v3")
        for label, cuisine, truth, dt, ht, dc, hc, _grounded in bad:
            print(f"  {label[:34]:34s} {cuisine:14s} {truth:5s} "
                  f"{(dt if dc!='ok' else '-'):9s} {(ht if hc!='ok' else '-')}")
    else:
        print("No failures: every positive warned, every negative was not over-warned.")

    def rate(idx, truth, badtag):
        items = [r for r in rows if r[2] == truth]
        return sum(1 for r in items if r[idx] == badtag), len(items)

    print("\n" + "-" * 60)
    for name, idx in (("v2 (rules)", 5), ("v3 (AI)", 6)):
        fn, npos = rate(idx, "pos", "MISS")
        fp, nneg = rate(idx, "neg", "overwarn")
        print(f"{name:10s}  FALSE-NEGATIVE (missed nuts): {fn}/{npos}   "
              f"over-warn (nut-free->avoid): {fp}/{nneg}")
    if not api_key:
        print("  NOTE: v3 NOT MEASURED -- no GEMINI_API_KEY, so the v3 column is just the "
              "v2 deterministic fallback, not the LLM scorer.")

    # Honest headline: cuisine-only positives are labelled from the SAME baseline that
    # scores them (partly self-referential), so their 0/N is structurally easy. The
    # GROUNDED positives (real menu/chart evidence) are the non-circular signal.
    g_fn = sum(1 for r in rows if r[2] == "pos" and r[7] and r[5] == "MISS")
    g_pos = sum(1 for r in rows if r[2] == "pos" and r[7])
    c_pos = sum(1 for r in rows if r[2] == "pos" and not r[7])
    print(f"\nGROUNDED-only v2 false-negative (the non-circular signal): {g_fn}/{g_pos}")
    print(f"  ({c_pos} cuisine-only positives are partly self-referential -- "
          "their result is reassuring, not conclusive.)")

    # Per-cuisine false-negative breakdown (v2) -- where would a real diner be missed?
    by_cuisine: dict[str, list[int]] = {}
    for label, cuisine, truth, dt, ht, dc, hc, _grounded in rows:
        if truth != "pos":
            continue
        agg = by_cuisine.setdefault(cuisine, [0, 0])
        agg[1] += 1
        if dc == "MISS":
            agg[0] += 1
    misses = {c: v for c, v in by_cuisine.items() if v[0]}
    print("\nv2 false-negatives by cuisine: "
          + (", ".join(f"{c} {v[0]}/{v[1]}" for c, v in misses.items()) if misses else "none"))
    print("\nFalse negatives are the dangerous direction; lower is safer.")


if __name__ == "__main__":
    main()
