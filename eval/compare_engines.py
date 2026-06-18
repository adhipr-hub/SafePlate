"""Three-way menu-extraction comparison: v1 vs v2-hybrid vs v2-llm-first.

Runs every engine over the SAME frozen snapshots (data/bench_snapshots/) so the
comparison isolates *interpretation* from discovery and site drift. Reports, per
engine: items emitted, restaurant coverage, a proxy junk-rate, and LLM-call count
(the cost axis for the hybrid-vs-llm-first tradeoff).

Phase 1 note: the v2 LLM interpreters are not wired yet, so v2-hybrid and
v2-llm-first are currently identical (structured-only) -- they will diverge once
Phase 2 lands. The Phase-1 story is precision: v2 emits only schema-grounded
items and zero junk, while v1's prose heuristics invent items from non-menu text
(see the Modern Slavery regression at the bottom).

Usage:
  python eval/compare_engines.py            # full snapshot comparison + regression
  python eval/compare_engines.py --regression-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.embedded_json import extract_items_from_embedded_json
from safeplate.concurrency import map_concurrent
from safeplate.config import get_gemini_api_key, get_gemini_model
from safeplate.extraction2 import Policy, extract_menu
from safeplate.extraction2.acquire import payload_from_html, payload_from_pdf_text
from safeplate.menu_text import (
    _extract_menu_items_from_html,
    _extract_menu_items_from_text,
    _extract_schema_org_menu_items_from_html,
)

SNAP_DIR = ROOT / "data" / "bench_snapshots"
MANIFEST = SNAP_DIR / "manifest.json"
FIXTURES = ROOT / "eval" / "fixtures"


# --------------------------------------------------------------------------- #
# Engines (each callable: (text, is_pdf, url) -> (list[MenuItemRecord], llm_calls)
# --------------------------------------------------------------------------- #
def v1_extract(text: str, is_pdf: bool):
    """v1 interpretation exactly as bench_extraction runs it (prose heuristics)."""
    if is_pdf:
        return _extract_menu_items_from_text(text), 0
    items = _extract_schema_org_menu_items_from_html(text) + _extract_menu_items_from_html(text)
    if not items:
        items = extract_items_from_embedded_json(text)
    return items, 0


def v2_extract(text, is_pdf, url, policy, *, llm_enabled, api_key, model):
    payload = payload_from_pdf_text(url, text) if is_pdf else payload_from_html(url, text)
    result = extract_menu(
        [payload], policy=policy, llm_enabled=llm_enabled,
        gemini_api_key=api_key, gemini_model=model,
    )
    return result.items, result.llm_calls


def build_engines(*, llm_enabled: bool, api_key: str | None, model: str | None):
    return {
        "v1": lambda t, pdf, url: v1_extract(t, pdf),
        "v2-hybrid": lambda t, pdf, url: v2_extract(
            t, pdf, url, Policy.HYBRID, llm_enabled=llm_enabled, api_key=api_key, model=model),
        "v2-llm-first": lambda t, pdf, url: v2_extract(
            t, pdf, url, Policy.LLM_FIRST, llm_enabled=llm_enabled, api_key=api_key, model=model),
        "v2-merge": lambda t, pdf, url: v2_extract(
            t, pdf, url, Policy.MERGE, llm_enabled=llm_enabled, api_key=api_key, model=model),
    }


# --------------------------------------------------------------------------- #
# Engine-INDEPENDENT proxy judge (frozen here; does NOT import v1's blocklists,
# so it cannot bias the comparison toward either engine). It is only a proxy --
# raw counts and the regression fixture are the load-bearing evidence.
# --------------------------------------------------------------------------- #
_LEGAL_NAV = (
    "modern slavery", "pursuant to", "fiscal year", "supply chain", "this statement",
    "annual report", "all rights reserved", "privacy policy", "terms of",
    "sign in", "log in", "add to cart", "view menu", "follow us", "copyright",
)


def looks_like_junk(name: str) -> bool:
    n = (name or "").strip()
    if len(n) < 2 or len(n) > 60:
        return True
    if not re.search(r"[a-zA-ZÀ-￿]", n):
        return True
    low = n.lower()
    if any(tok in low for tok in _LEGAL_NAV):
        return True
    if n[0].islower():               # description fragment, not a name
        return True
    if len(n.split()) > 8:           # sentence-like prose run-on
        return True
    digits = sum(c.isdigit() for c in n)
    return bool(digits and digits / len(n) > 0.4)


# --------------------------------------------------------------------------- #
# Snapshot comparison
# --------------------------------------------------------------------------- #
def run_snapshots(engines: dict, *, limit: int | None, workers: int) -> None:
    if not MANIFEST.exists():
        print("No snapshots. Run: python eval/bench_extraction.py --collect")
        return
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if limit:
        manifest = manifest[:limit]

    def process(entry: dict):
        is_pdf = entry["file"].endswith(".pdf.txt")
        text = (SNAP_DIR / entry["file"]).read_text(encoding="utf-8")
        # Within one entry the two v2 engines share a disk cache, so the second
        # reuses the first's Gemini call (keeps the run cheap).
        return entry, {name: fn(text, is_pdf, entry["url"]) for name, fn in engines.items()}

    results = map_concurrent(process, manifest, max_workers=workers)

    stats = {name: defaultdict(int) for name in engines}
    rest_with = {name: set() for name in engines}
    rest_all: set = set()
    v1_junk_examples: list[str] = []

    for entry, per_engine in results:
        rest_all.add(entry["restaurant"])
        for name, (items, llm_calls) in per_engine.items():
            uniq = {(it.restaurant_name, (it.item_name or "").lower().strip()) for it in items}
            junk = sum(1 for it in items if looks_like_junk(it.item_name))
            stats[name]["items"] += len(items)
            stats[name]["uniq"] += len(uniq)
            stats[name]["junk"] += junk
            stats[name]["llm"] += llm_calls
            if items:
                rest_with[name].add(entry["restaurant"])
            if name == "v1":
                for it in items:
                    if (looks_like_junk(it.item_name) and it.item_name not in v1_junk_examples
                            and len(v1_junk_examples) < 8):
                        v1_junk_examples.append(it.item_name)

    n_rest = len(rest_all)
    print("=" * 84)
    print(f"SNAPSHOT COMPARISON  ({len(manifest)} sources, {n_rest} restaurants, 6 cities)")
    print("-" * 84)
    print(f"{'ENGINE':16}{'items':>8}{'unique':>9}{'coverage':>11}{'junk':>8}{'junk%':>8}{'LLM calls':>11}")
    print("-" * 84)
    for name in engines:
        s = stats[name]
        junk_pct = s["junk"] / s["items"] * 100 if s["items"] else 0
        print(f"{name:16}{s['items']:>8}{s['uniq']:>9}{len(rest_with[name]):>6}/{n_rest:<4}"
              f"{s['junk']:>8}{junk_pct:>7.0f}%{s['llm']:>11}")
    print("=" * 84)
    print("Read: with the LLM enabled, v2 should recover the unstructured tail v1")
    print("gets via prose heuristics, but only emit grounded items (low junk).")
    print("junk% is a PROXY (legal/nav tokens, lowercase/prose/mostly-digit names);")
    print("it under/over-counts, so treat it as directional, not exact.")
    if v1_junk_examples:
        print("\nExamples of v1 non-dish / mis-segmented lines (real snapshots):")
        for ex in v1_junk_examples:
            print(f"  - {ex!r}")
    print()


# --------------------------------------------------------------------------- #
# LLM-judged scorecard (real precision/recall, replacing the regex proxy)
# --------------------------------------------------------------------------- #
def run_scorecard(engines: dict, *, limit, workers, api_key: str, model: str) -> None:
    try:
        from eval.llm_judge import judge_items, normalize
    except ImportError:
        from llm_judge import judge_items, normalize

    if not MANIFEST.exists():
        return
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if limit:
        manifest = manifest[:limit]

    def process(entry: dict):
        is_pdf = entry["file"].endswith(".pdf.txt")
        text = (SNAP_DIR / entry["file"]).read_text(encoding="utf-8")
        return entry, {name: fn(text, is_pdf, entry["url"]) for name, fn in engines.items()}

    results = map_concurrent(process, manifest, max_workers=workers)

    per_engine = {name: {} for name in engines}          # (restaurant, norm) -> item
    rest_names = {name: defaultdict(set) for name in engines}  # restaurant -> {norm}
    union: dict[str, dict] = {}                            # norm -> {name,desc,price}
    for entry, eng_out in results:
        r = entry["restaurant"]
        for name, (items, _calls) in eng_out.items():
            for it in items:
                nn = normalize(it.item_name)
                if not nn:
                    continue
                per_engine[name][(r, nn)] = it
                rest_names[name][r].add(nn)
                union.setdefault(nn, {"name": it.item_name,
                                      "description": it.description, "price": it.price})

    verdicts = judge_items(list(union.values()), api_key=api_key, model=model, workers=workers)
    union_real = {nn for nn in union if verdicts.get(nn, True)}
    n_rest = len({entry["restaurant"] for entry, _ in results})

    print("=" * 84)
    print("LLM-JUDGED SCORECARD (Gemini per-item judge, engine-independent, cached)")
    print("-" * 84)
    print(f"{'ENGINE':16}{'unique':>9}{'real':>8}{'precision':>11}{'real-cov':>11}{'rel-recall':>12}")
    print("-" * 84)
    for name in engines:
        uniq = per_engine[name]
        real_pairs = [(r, nn) for (r, nn) in uniq if verdicts.get(nn, True)]
        prec = len(real_pairs) / len(uniq) * 100 if uniq else 0
        real_cov = sum(
            1 for nns in rest_names[name].values() if any(verdicts.get(nn, True) for nn in nns)
        )
        real_names = {nn for (_r, nn) in real_pairs}
        rel_recall = len(real_names) / len(union_real) * 100 if union_real else 0
        print(f"{name:16}{len(uniq):>9}{len(real_pairs):>8}{prec:>10.0f}%"
              f"{real_cov:>8}/{n_rest:<2}{rel_recall:>11.0f}%")
    print("=" * 84)
    print("precision  = real dishes / unique emitted items (honest replacement for junk%)")
    print("real-cov   = restaurants with >=1 real dish")
    print("rel-recall = engine's distinct real dishes / union of ALL engines' real dishes")
    print(f"(judged {len(union)} distinct item strings; real pool = {len(union_real)})")
    print()


# --------------------------------------------------------------------------- #
# Regression: a non-menu corporate PDF must yield ZERO items in v2
# --------------------------------------------------------------------------- #
# A minimal real schema.org Menu -- proves v2 is precise (0 on corporate prose)
# WITHOUT being trivially "always 0": it extracts when an explicit schema exists.
_SCHEMA_ORG_MENU = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Menu","hasMenuSection":{"@type":"MenuSection",
"name":"Mains","hasMenuItem":[
 {"@type":"MenuItem","name":"Pad Thai","offers":{"@type":"Offer","price":"14.00","priceCurrency":"USD"}},
 {"@type":"MenuItem","name":"Green Curry","offers":{"@type":"Offer","price":"15.50","priceCurrency":"USD"}}
]}}</script></head><body>Welcome</body></html>
"""


def run_regression(engines: dict) -> None:
    """The architectural contract, both directions:

    1. A non-menu corporate document -> v2 emits 0. With the LLM enabled this is
       a real test of understanding: the model reads the Modern Slavery statement
       and reports no menu, rather than pairing section numbers with sentence
       fragments. No document-specific rule anywhere (v1 needed a blocklist patch
       AND a PDF-validation patch for the same doc).
    2. A real schema.org Menu -> v2 still extracts, so it is precise, not silent.
    """
    fixture = FIXTURES / "starbucks_modern_slavery.pdf.txt"
    if not fixture.exists():
        print(f"(regression skipped -- missing {fixture})")
        return
    corporate = fixture.read_text(encoding="utf-8")
    url = "https://content-prod-live.cert.starbucks.com/binary/v2/asset/137-107200.pdf"

    print("=" * 84)
    print("REGRESSION (architectural contract)")
    print("-" * 84)
    print("A) Non-menu corporate PDF (Starbucks Modern Slavery Act) -> must be 0:")
    for name in ("v2-hybrid", "v2-llm-first"):
        items, _ = engines[name](corporate, True, url)
        ok = "OK -- reads it as non-menu, 0 items" if not items else "LEAK"
        print(f"    {name:14}{len(items):>4} items   {ok}")

    print("B) Real schema.org Menu page -> v2 must still extract (precise, not silent):")
    items, _ = engines["v2-hybrid"](_SCHEMA_ORG_MENU, False, "https://example.test/menu")
    names = ", ".join(it.item_name for it in items) or "(none)"
    print(f"    {'v2-hybrid':14}{len(items):>4} items   [{names}]")
    print("=" * 84)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regression-only", action="store_true")
    ap.add_argument("--llm", action="store_true", help="Enable the v2 Gemini interpreter")
    ap.add_argument("--limit", type=int, default=None, help="Only the first N snapshots")
    ap.add_argument("--workers", type=int, default=4, help="Concurrent workers (LLM runs)")
    ap.add_argument("--judge", action="store_true",
                    help="Add the LLM-judged scorecard (real precision/recall)")
    args = ap.parse_args()

    api_key = get_gemini_api_key() if args.llm else None
    if args.llm and not api_key:
        print("--llm requested but GEMINI_API_KEY is not set; running structured-only.")
    model = get_gemini_model()
    engines = build_engines(llm_enabled=bool(api_key), api_key=api_key, model=model)
    mode = "LLM ENABLED" if api_key else "structured-only (no LLM)"
    print(f"Engines: v1 vs v2-hybrid vs v2-llm-first   [{mode}]\n")

    if not args.regression_only:
        run_snapshots(engines, limit=args.limit, workers=args.workers)
        if args.judge:
            judge_key = api_key or get_gemini_api_key()
            if not judge_key:
                print("(--judge needs GEMINI_API_KEY)")
            else:
                run_scorecard(engines, limit=args.limit, workers=args.workers,
                              api_key=judge_key, model=model)
    run_regression(engines)


if __name__ == "__main__":
    main()
