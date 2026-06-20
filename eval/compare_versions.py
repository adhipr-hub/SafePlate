"""Three-version analysis: v1 (prior-only) vs v2 (deterministic Layer-5) vs v3
(hybrid batched-LLM). Covers SCORE distribution / de-quantization, PERFORMANCE
(throughput + API calls per search), and ACCURACY on the labeled benchmark.

    python eval/compare_versions.py            # no-quota parts + bounded v3 sample
    python eval/compare_versions.py --no-llm   # skip the v3 live sample entirely

v1's "score" is the raw cuisine/dish PRIOR (flat table, no menu fusion) -- the
"everything's the same / just because it's Chinese" behaviour. v2 fuses grounded
evidence + Phase-A coverage de-quantization. v3 refines v2's facts with one LLM call.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import safeplate.allergen_score as asc  # noqa: E402
from safeplate.allergen_prior import score_restaurant_prior  # noqa: E402
from safeplate.allergen_score import (  # noqa: E402
    Severity, UserProfile, score_restaurant_for_user,
)
from safeplate.allergen_score_llm import score_restaurant_with_llm  # noqa: E402
from safeplate.config import get_gemini_api_key, get_gemini_model  # noqa: E402
from eval.datasets import labeled_restaurants as ds  # noqa: E402

NUT = UserProfile.for_nuts(Severity.ALLERGY)


def _item(name, *, allergen_terms=None, method="gemini_text"):
    return {"item_name": name, "description": "", "allergen_terms": allergen_terms or [],
            "extraction_method": method}


def _v1_risk(cuisines, region="US"):
    return round(score_restaurant_prior(cuisines=cuisines, region=region, allergen="nuts").risk, 3)


def _v2_risk(**kw):
    return score_restaurant_for_user(NUT, **kw).overall_risk


def hr(title):
    print("\n" + "=" * 74 + f"\n{title}\n" + "=" * 74)


# --------------------------------------------------------------------------- #
def evidence_ladder():
    hr("1. SCORE BEHAVIOUR -- same cuisine (chinese), increasing evidence")
    ladder = [
        ("no menu", dict(cuisines=["chinese"], region="US")),
        ("3 clean dishes parsed", dict(cuisines=["chinese"], region="US",
            menu_items=[_item(f"Dish {i}") for i in range(3)])),
        ("20 clean dishes parsed", dict(cuisines=["chinese"], region="US",
            menu_items=[_item(f"Dish {i}") for i in range(20)])),
        ("20 clean dishes (UK mandate)", dict(cuisines=["chinese"], region="GB",
            menu_items=[_item(f"Dish {i}") for i in range(20)])),
        ("clean allergen chart", dict(cuisines=["chinese"], region="US",
            menu_items=[_item(f"Dish {i}", method="gemini_allergen_matrix",
                              allergen_terms=["milk"]) for i in range(20)])),
        ("named peanut dish", dict(cuisines=["chinese"], region="US",
            menu_items=[_item("Kung Pao w/ peanuts", allergen_terms=["peanut"])])),
    ]
    print(f"{'evidence':32s} {'v1 prior':9s} {'v2 det':8s}  note")
    print("-" * 74)
    for label, kw in ladder:
        v1 = _v1_risk(kw["cuisines"], kw.get("region", "US"))
        v2 = round(_v2_risk(**kw), 3)
        print(f"{label:32s} {v1:<9.3f} {v2:<8.3f}  "
              f"{'(v1 ignores the menu)' if v1 == _v1_risk(['chinese']) else ''}")
    print("\nv1 returns ONE number per cuisine no matter the menu; v2 moves "
          "continuously with the evidence.")


def dequantization():
    hr("2. DE-QUANTIZATION -- distinct scores across the labeled benchmark")
    cases = [ds.score_kwargs(c) for c in ds.LABELED]

    def distinct(discount):
        orig = asc._COVERAGE_DISCOUNT
        asc._COVERAGE_DISCOUNT = discount
        try:
            vals = [round(score_restaurant_for_user(NUT, **kw).overall_risk, 3) for kw in cases]
        finally:
            asc._COVERAGE_DISCOUNT = orig
        return vals

    v1_vals = [_v1_risk(kw["cuisines"], kw.get("region", "US")) for kw in cases]
    off = distinct(0.0)      # Phase A disabled (the old snap-to-constants behaviour)
    on = distinct(asc._COVERAGE_DISCOUNT)

    print(f"{'version':28s} distinct values / {len(cases)} cases")
    print("-" * 74)
    print(f"{'v1 (prior only)':28s} {len(set(v1_vals))}")
    print(f"{'v2 WITHOUT Phase A':28s} {len(set(off))}")
    print(f"{'v2 WITH Phase A':28s} {len(set(on))}")
    print("\nMore distinct values = less attractor-collapse = the list visibly ranks "
          "rather than tying.")


def performance():
    hr("3. PERFORMANCE")
    # Throughput of the deterministic scorer (the v2 default + the v3 floor).
    kw = dict(cuisines=["thai"], region="US",
              menu_items=[_item(f"Dish {i}") for i in range(20)])
    n = 5000
    t0 = time.perf_counter()
    for _ in range(n):
        score_restaurant_for_user(NUT, **kw)
    dt = time.perf_counter() - t0
    print(f"deterministic scorer throughput: {n/dt:,.0f} scores/sec "
          f"({dt/n*1e6:.1f} us each) -- no network, no quota")

    print("\nLLM scoring calls per SEARCH of N menu-backed restaurants:")
    print(f"  {'version':26s} extraction LLM      scoring LLM")
    print("  " + "-" * 60)
    print(f"  {'v1 (prose heuristics)':26s} ~chunks/restaurant  0 (prior only)")
    print(f"  {'v2 (deterministic)':26s} ~1-8/restaurant*    0")
    print(f"  {'v3 BEFORE batching':26s} ~1-8/restaurant*    N (one per restaurant)")
    print(f"  {'v3 AFTER batching':26s} ~1-8/restaurant*    1 (whole search)")
    print("  * bounded by early-stop + Phase-D link-select skip + result cache "
          "(0 on a warm cache).")


def accuracy(run_llm):
    hr("4. ACCURACY on the labeled benchmark (false-negatives = dangerous)")
    pos = ds.positives()
    neg = ds.negatives()

    def fn_rate(scorer):
        miss = sum(1 for c in pos if scorer(c).tier == "likely_ok")
        over = sum(1 for c in neg if scorer(c).tier == "avoid")
        return miss, len(pos), over, len(neg)

    v2 = lambda c: score_restaurant_for_user(NUT, **ds.score_kwargs(c))
    m, np_, o, nn = fn_rate(v2)
    print(f"v2 (rules)  false-neg {m}/{np_}   over-warn {o}/{nn}")

    if not run_llm:
        print("v3 (AI)     skipped (--no-llm)")
        return
    api_key, model = get_gemini_api_key(), get_gemini_model()
    if not api_key:
        print("v3 (AI)     skipped (no Gemini key)")
        return

    # Bounded representative sample so we don't burn the daily quota: the grounded
    # cases + a spread of cuisine-only ones.
    sample = ds._GROUNDED + [c for c in ds._CUISINE_ONLY[:8]]
    print(f"\nv3 live sample: {len(sample)} representative cases (tier agreement + "
          "conservatism vs v2)")
    print(f"  {'case':34s} {'truth':5s} {'v2':10s} {'v3':10s} dRisk")
    agree = same = 0
    deltas = []
    for c in sample:
        kw = ds.score_kwargs(c)
        d = score_restaurant_for_user(NUT, **kw)
        h = score_restaurant_with_llm(NUT, api_key=api_key, model=model, **kw)
        deltas.append(h.overall_risk - d.overall_risk)
        if d.tier == h.tier:
            same += 1
        agree += 1
        flag = "" if d.tier == h.tier else "  <-diff"
        print(f"  {c['name'][:34]:34s} {c['truth']:5s} "
              f"{d.tier:10s} {h.tier:10s} {h.overall_risk - d.overall_risk:+.3f}{flag}")
    if deltas:
        mean_d = sum(deltas) / len(deltas)
        print(f"\n  tier agreement v2/v3: {same}/{agree}")
        print(f"  mean risk delta (v3 - v2): {mean_d:+.3f} "
              f"({'v3 more conservative' if mean_d > 0 else 'v3 less conservative'})")
        if all(d != 0 for d in deltas) is False:
            pass
        # Safety check: did v3 EVER drop a positive below the v2 floor?
        unsafe = [c["name"] for c in sample
                  if c["truth"] == "pos"
                  and score_restaurant_with_llm(NUT, api_key=api_key, model=model,
                                                **ds.score_kwargs(c)).tier == "likely_ok"]
        print(f"  v3 positives downgraded to 'likely_ok': "
              f"{unsafe if unsafe else 'none (guardrails held)'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="skip the v3 live sample")
    args = ap.parse_args()
    evidence_ladder()
    dequantization()
    performance()
    accuracy(run_llm=not args.no_llm)


if __name__ == "__main__":
    main()
