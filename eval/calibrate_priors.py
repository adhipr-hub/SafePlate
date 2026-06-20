"""Calibrate the cuisine nut-risk baselines against the labeled benchmark.

Every value in ``CUISINE_NUT_BASELINE`` is a hand-picked guess (the module says so).
This tool asks: do those guesses RANK cuisines the way labeled real-world prevalence
does, and what would a data-fitted value be? It is a DECISION AID, not an auto-tuner
-- it prints suggestions; a human decides what to commit (a handful of labeled cases
per cuisine is noisy, so we smooth and flag low-confidence rows).

    python eval/calibrate_priors.py

Empirical prevalence uses Laplace smoothing: (pos + a) / (n + 2a), a=0.5, so a
cuisine with 3/3 positives doesn't claim a literal 1.0.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.datasets import labeled_restaurants as ds  # noqa: E402
from safeplate.allergen_prior import (  # noqa: E402
    CUISINE_NUT_BASELINE, DEFAULT_CUISINE_BASELINE,
)

_SMOOTH = 0.5


def _empirical(pos: int, n: int) -> float:
    return (pos + _SMOOTH) / (n + 2 * _SMOOTH)


def _spearman(pairs: list[tuple[float, float]]) -> float:
    """Rank correlation between two equally-long sequences (no scipy dependency)."""
    if len(pairs) < 2:
        return float("nan")

    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rx, ry = ranks(xs), ranks(ys)
    n = len(pairs)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1 - (6 * d2) / (n * (n * n - 1))


def main() -> None:
    # Count pos/total per PRIMARY cuisine over cuisine-only cases (where the cuisine
    # baseline is what actually drives the score -- no menu/signals to override it).
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # cuisine -> [pos, total]
    for case in ds.LABELED:
        if case.get("menu_items") or case.get("signals") or case.get("community"):
            continue  # not cuisine-only -> doesn't isolate the baseline
        cuisine = case["cuisines"][0]
        counts[cuisine][1] += 1
        if case["truth"] == "pos":
            counts[cuisine][0] += 1

    rows = []
    for cuisine, (pos, n) in counts.items():
        emp = _empirical(pos, n)
        cur = CUISINE_NUT_BASELINE.get(cuisine, DEFAULT_CUISINE_BASELINE)
        rows.append((cuisine, pos, n, emp, cur, emp - cur))
    rows.sort(key=lambda r: r[3], reverse=True)

    print(f"{'cuisine':16s} {'pos/n':7s} {'empirical':9s} {'current':8s} {'delta':7s} flag")
    print("-" * 60)
    for cuisine, pos, n, emp, cur, delta in rows:
        flag = ""
        if n < 3:
            flag = "low-n"
        elif abs(delta) >= 0.20:
            flag = "REVISIT" if delta > 0 else "revisit"
        print(f"{cuisine:16s} {pos}/{n:<5d} {emp:<9.2f} {cur:<8.2f} {delta:+7.2f} {flag}")

    pairs = [(r[4], r[3]) for r in rows if r[2] >= 3]  # (current, empirical), n>=3 only
    rho = _spearman(pairs)
    print("-" * 60)
    print(f"Cuisines with >=3 labeled cases: {len(pairs)}")
    print(f"Spearman rank correlation (current baseline vs empirical): {rho:.2f}")
    print("  1.0 = our hand-picked ordering matches the data perfectly; "
          "negative = inverted.")
    print("\nNOTE: labels are curated seeds, not a live audit. Treat 'REVISIT' rows as "
          "hypotheses to confirm with more real cases before editing the table.")


if __name__ == "__main__":
    main()
