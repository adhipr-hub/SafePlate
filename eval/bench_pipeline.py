from __future__ import annotations

"""Live, repeatable benchmark of the PRODUCTION extraction pipeline across two dense
dining districts, for iterating speedups while proving quality holds.

It pins a CACHED manifest of real restaurants (so every run scores the same places),
runs ``discover_and_extract`` COLD (extraction caches cleared), and records per
restaurant: wall-clock, item count, allergen-bearing item count, Layer-5 nut tier +
risk, and the real Gemini call count. Results are saved under a ``--label`` and diffed
against the saved ``baseline`` so an optimization is accepted only if QUALITY holds
(items / allergen_items / tier per restaurant) and TIME drops.

Run:
  PYTHONPATH=<repo root> python eval/bench_pipeline.py --label baseline
  PYTHONPATH=<repo root> python eval/bench_pipeline.py --label opt1   # diffs vs baseline
"""

import argparse
import json
import shutil
import time
from types import SimpleNamespace

from safeplate.config import (
    get_cache_dir,
    get_gemini_api_key,
    get_gemini_model,
    get_google_places_api_key,
    get_user_agent,
)
from safeplate.providers.google_places import fetch_nearby_restaurants
from safeplate.places import is_food_place
from safeplate.allergen_prior import normalize_cuisine, region_from_address
from safeplate.allergen_score import (
    RestaurantSignals,
    Severity,
    UserProfile,
    assess_restaurant_record,
)

# Two restaurant-dense districts on opposite coasts.
AREAS = (
    ("Santana Row, San Jose CA", 37.3209, -121.9476, 700),
    ("Lower Manhattan, NYC", 40.7128, -74.0060, 700),
)
N_PER_AREA = 5

_CACHE_SUBDIRS = ("extraction2_result", "extraction2_llm", "extraction2_pdfmatrix")


def _bench_dir():
    d = get_cache_dir() / "bench_pipeline"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clear_extraction_caches() -> int:
    root = get_cache_dir()
    n = 0
    for sub in _CACHE_SUBDIRS:
        path = root / sub
        try:
            if path.exists():
                shutil.rmtree(path)
                n += 1
        except OSError:
            pass
    return n


def _manifest_path():
    return _bench_dir() / "manifest.json"


def build_or_load_manifest(api_key, user_agent):
    """Pin a stable manifest so every iteration scores the SAME restaurants. Built once
    from Google Places (food places with websites), then cached to disk."""
    path = _manifest_path()
    if path.exists():
        rows = json.loads(path.read_text(encoding="utf-8"))
        return [SimpleNamespace(**r) for r in rows]

    chosen = []
    for label, lat, lon, radius in AREAS:
        seen = set()
        picked = 0
        rows = fetch_nearby_restaurants(
            latitude=lat, longitude=lon, radius_meters=radius,
            limit=20, api_key=api_key, user_agent=user_agent,
        )
        for r in rows:
            site = (r.website_url or "").strip()
            if not site or not is_food_place(r.categories):
                continue
            if site.lower() in seen:
                continue
            seen.add(site.lower())
            chosen.append({
                "area": label, "name": r.name, "website_url": r.website_url,
                "address": r.address, "latitude": r.latitude,
                "longitude": r.longitude, "categories": list(r.categories or []),
            })
            picked += 1
            if picked >= N_PER_AREA:
                break
    path.write_text(json.dumps(chosen, indent=2), encoding="utf-8")
    return [SimpleNamespace(**r) for r in chosen]


def _allergen_term_count(items) -> int:
    return sum(1 for it in items if (getattr(it, "allergen_terms", None) or []))


def _score(row, items, allergy_signals, profile):
    signals = RestaurantSignals.from_allergy_signals(allergy_signals or [])
    try:
        from safeplate.allergy_registry import apply_registry
        apply_registry(signals, row.name, row.address, row.website_url)
    except Exception:
        pass
    record = SimpleNamespace(
        categories=row.categories, address=row.address, latitude=row.latitude,
        longitude=row.longitude, website_url=row.website_url,
    )
    cuisines = normalize_cuisine(row.categories)
    region = region_from_address(row.address, latitude=row.latitude, longitude=row.longitude)
    assessment = assess_restaurant_record(
        record, profile, menu_items=items, signals=signals,
        cuisines=cuisines, region=region,
    )
    return assessment.tier, round(assessment.overall_risk, 3)


def run_one(row, *, user_agent, api_key, model, profile):
    from safeplate.extraction2.discover import discover_and_extract
    t0 = time.monotonic()
    _candidates, res = discover_and_extract(
        row.website_url, user_agent=user_agent, restaurant_name=row.name,
        address=row.address, api_key=api_key, model=model, use_result_cache=False,
    )
    elapsed = time.monotonic() - t0
    tier, risk = _score(row, res.items, res.allergy_signals, profile)
    return {
        "area": row.area, "name": row.name, "url": row.website_url,
        "time_s": round(elapsed, 2), "items": len(res.items),
        "allergen_items": _allergen_term_count(res.items),
        "llm_calls": res.llm_calls, "tier": tier, "risk": risk,
    }


def _load(label):
    path = _bench_dir() / f"results_{label}.json"
    if path.exists():
        return {r["name"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--baseline", default="baseline",
                    help="label to diff against (quality must hold, time should drop)")
    args = ap.parse_args()

    ua = get_user_agent()
    gkey = get_gemini_api_key()
    gmodel = get_gemini_model()
    pkey = get_google_places_api_key()
    if not pkey:
        print("ERROR: GOOGLE_PLACES_API_KEY not set.")
        return

    manifest = build_or_load_manifest(pkey, ua)
    print(f"Manifest: {len(manifest)} restaurants across {len(AREAS)} areas "
          f"(cached at {_manifest_path()})")
    cleared = _clear_extraction_caches()
    print(f"Cleared {cleared} extraction cache dir(s) for a cold run.\n")

    profile = UserProfile.for_nuts(Severity.ALLERGY)
    results = []
    for row in manifest:
        try:
            rec = run_one(row, user_agent=ua, api_key=gkey, model=gmodel, profile=profile)
        except Exception as e:
            rec = {"area": row.area, "name": row.name, "url": row.website_url,
                   "error": repr(e), "time_s": 0.0, "items": 0,
                   "allergen_items": 0, "llm_calls": 0, "tier": "err", "risk": None}
        results.append(rec)
        print(f"  {rec['name'][:28]:28} {rec['time_s']:6.2f}s  items={rec['items']:<4} "
              f"allerg={rec['allergen_items']:<3} llm={rec['llm_calls']:<2} "
              f"tier={rec['tier']}" + (f"  ERR {rec.get('error')}" if rec.get('error') else ""))

    out = _bench_dir() / f"results_{args.label}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    total_t = sum(r["time_s"] for r in results)
    total_items = sum(r["items"] for r in results)
    total_alg = sum(r["allergen_items"] for r in results)
    total_llm = sum(r["llm_calls"] for r in results)
    print(f"\nTOTAL [{args.label}]: {total_t:.2f}s  items={total_items}  "
          f"allergen_items={total_alg}  llm_calls={total_llm}  (saved {out.name})")

    base = _load(args.baseline) if args.label != args.baseline else None
    if base:
        print(f"\n=== DIFF vs {args.baseline} (quality must HOLD, time should DROP) ===")
        bt = sum(b["time_s"] for b in base.values())
        print(f"  time:  {bt:.2f}s -> {total_t:.2f}s  ({(bt/total_t if total_t else 0):.2f}x)")
        regress, tier_flips, item_loss = [], [], []
        for r in results:
            b = base.get(r["name"])
            if not b:
                continue
            if r["allergen_items"] < b["allergen_items"]:
                regress.append(f"{r['name']} ({b['allergen_items']}->{r['allergen_items']})")
            if r["tier"] != b["tier"]:
                tier_flips.append(f"{r['name']} ({b['tier']}->{r['tier']})")
            if r["items"] < b["items"]:
                item_loss.append(f"{r['name']} ({b['items']}->{r['items']})")
        print(f"  allergen-recall regressions: {regress or 'NONE'}")
        print(f"  tier changes:                {tier_flips or 'NONE'}")
        print(f"  item-count drops:            {item_loss or 'NONE'}")
        verdict = "PASS (quality held)" if not (regress or tier_flips) else "FAIL (quality regressed)"
        print(f"  QUALITY GATE: {verdict}")


if __name__ == "__main__":
    main()
