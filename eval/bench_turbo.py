from __future__ import annotations

"""Cold benchmark: CURRENT extraction2 pipeline vs the TURBO core, on REAL
restaurants near a fixed location. Apples-to-apples: both item sets are scored
with the same deterministic Layer-#5 scorer (nuts / ALLERGY profile).

Run:  PYTHONPATH=<repo root> python eval/bench_turbo.py
"""

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

from safeplate.allergen_prior import (
    normalize_cuisine,
    region_from_address,
)
from safeplate.allergen_score import (
    RestaurantSignals,
    Severity,
    UserProfile,
    assess_restaurant_record,
)

# Restaurant-dense locations. Primary: Aker Brygge, Oslo (a packed waterfront
# dining district). Fallback: Lower Manhattan, NYC. Both queried with
# rankPreference=DISTANCE so we get the genuinely-nearest food places.
N_RESTAURANTS = 5
_LOCATIONS = (
    ("Aker Brygge, Oslo", 59.9106, 10.7275, 700),
    ("Lower Manhattan, NYC", 40.7128, -74.0060, 700),
)

_CACHE_SUBDIRS = (
    "extraction2_result",
    "extraction2_llm",
    "extraction2_pdfmatrix",
)


def _clear_caches() -> list[str]:
    """Delete the extraction caches so both pipelines run truly cold."""
    cleared: list[str] = []
    root = get_cache_dir()
    for sub in _CACHE_SUBDIRS:
        path = root / sub
        try:
            if path.exists():
                shutil.rmtree(path)
                cleared.append(str(path))
        except OSError:
            pass
    return cleared


def _build_manifest(api_key: str, user_agent: str):
    """Pick REAL food establishments with websites from dense dining districts.

    Filters each location's nearby results to rows that are food places
    (is_food_place) AND have a non-empty website_url. Merges across locations
    (dedup by website) until ~N_RESTAURANTS real restaurants are collected.
    """
    chosen: list = []
    seen: set[str] = set()
    for label, lat, lon, radius in _LOCATIONS:
        rows = fetch_nearby_restaurants(
            latitude=lat,
            longitude=lon,
            radius_meters=radius,
            limit=15,
            api_key=api_key,
            user_agent=user_agent,
        )
        for r in rows:
            site = (r.website_url or "").strip()
            if not site:
                continue
            if not is_food_place(r.categories):
                continue
            key = site.lower()
            if key in seen:
                continue
            seen.add(key)
            chosen.append(r)
            if len(chosen) >= N_RESTAURANTS:
                return chosen
        # Only fall through to the next location if we still don't have enough.
        if len(chosen) >= N_RESTAURANTS:
            break
    return chosen[:N_RESTAURANTS]


def _allergen_term_count(items) -> int:
    n = 0
    for it in items:
        terms = getattr(it, "allergen_terms", None) or []
        if terms:
            n += 1
    return n


def _score(row, items, allergy_signals, profile):
    """Mirror menu_service._extract_and_assess_structured's deterministic scoring."""
    signals = RestaurantSignals.from_allergy_signals(allergy_signals or [])
    try:
        from safeplate.allergy_registry import apply_registry

        apply_registry(signals, row.name, row.address, row.website_url)
    except Exception:
        pass
    record = SimpleNamespace(
        categories=row.categories,
        address=row.address,
        latitude=row.latitude,
        longitude=row.longitude,
        website_url=row.website_url,
    )
    cuisines = normalize_cuisine(row.categories)
    region = region_from_address(
        row.address, latitude=row.latitude, longitude=row.longitude
    )
    assessment = assess_restaurant_record(
        record,
        profile,
        menu_items=items,
        signals=signals,
        cuisines=cuisines,
        region=region,
    )
    return assessment.tier, assessment.overall_risk


def _run_current(row, *, user_agent, api_key, model):
    from safeplate.extraction2.discover import discover_and_extract

    _candidates, res = discover_and_extract(
        row.website_url,
        user_agent=user_agent,
        restaurant_name=row.name,
        address=row.address,
        api_key=api_key,
        model=model,
        use_result_cache=False,
    )
    return res.items, res.llm_calls, res.allergy_signals


def _run_turbo(row, *, user_agent, api_key, model):
    from safeplate.turbo import extract_restaurant

    tr = extract_restaurant(
        name=row.name,
        website_url=row.website_url,
        address=row.address,
        categories=row.categories,
        api_key=api_key,
        model=model,
        user_agent=user_agent,
    )
    return tr.items, tr.metrics


def main() -> None:
    user_agent = get_user_agent()
    gemini_key = get_gemini_api_key()
    gemini_model = get_gemini_model()
    places_key = get_google_places_api_key()

    if not places_key:
        print("ERROR: GOOGLE_PLACES_API_KEY not set; cannot build the manifest.")
        return

    locs = ", ".join(f"{lbl} (r={rad}m)" for lbl, _, _, rad in _LOCATIONS)
    print(f"Building manifest from Google Places near: {locs} ...")
    manifest = _build_manifest(places_key, user_agent)
    print(f"Manifest ({len(manifest)} real restaurants with websites):")
    for r in manifest:
        print(f"  - {r.name}  |  {r.website_url}")
    print()

    cleared = _clear_caches()
    print(f"Cleared {len(cleared)} cache dir(s) for a cold run.")
    for c in cleared:
        print(f"  rm {c}")
    print()

    profile = UserProfile.for_nuts(Severity.ALLERGY)

    results = []
    for row in manifest:
        rec = {
            "name": row.name,
            "website_url": row.website_url,
        }
        # CURRENT
        try:
            t0 = time.monotonic()
            c_items, c_llm, c_sig = _run_current(
                row, user_agent=user_agent, api_key=gemini_key, model=gemini_model
            )
            rec["current_s"] = time.monotonic() - t0
            rec["current_items"] = len(c_items)
            rec["current_llm_calls"] = c_llm
            rec["current_allergen_items"] = _allergen_term_count(c_items)
            try:
                tier, risk = _score(row, c_items, c_sig, profile)
                rec["current_tier"] = tier
                rec["current_risk"] = risk
            except Exception as exc:
                rec["current_tier"] = f"score_err:{exc}"
                rec["current_risk"] = None
        except Exception as exc:
            rec["current_error"] = str(exc)
            rec.setdefault("current_s", 0.0)
            rec.setdefault("current_items", 0)
            rec.setdefault("current_llm_calls", 0)
            rec.setdefault("current_tier", "")

        # TURBO
        try:
            t0 = time.monotonic()
            t_items, t_metrics = _run_turbo(
                row, user_agent=user_agent, api_key=gemini_key, model=gemini_model
            )
            rec["turbo_s"] = time.monotonic() - t0
            rec["turbo_items"] = len(t_items)
            rec["turbo_llm_calls"] = int(t_metrics.get("llm_calls", 0))
            rec["turbo_metrics"] = t_metrics
            rec["turbo_allergen_items"] = _allergen_term_count(t_items)
            try:
                # Turbo produces no allergy_signals; feed the scorer the same way the
                # app would for a turbo result (empty signals + name/address registry).
                tier, risk = _score(row, t_items, [], profile)
                rec["turbo_tier"] = tier
                rec["turbo_risk"] = risk
            except Exception as exc:
                rec["turbo_tier"] = f"score_err:{exc}"
                rec["turbo_risk"] = None
        except Exception as exc:
            rec["turbo_error"] = str(exc)
            rec.setdefault("turbo_s", 0.0)
            rec.setdefault("turbo_items", 0)
            rec.setdefault("turbo_llm_calls", 0)
            rec.setdefault("turbo_tier", "")

        cs = rec.get("current_s", 0.0) or 0.0
        ts = rec.get("turbo_s", 0.0) or 0.0
        rec["speedup"] = (cs / ts) if ts > 0 else 0.0
        rec["tier_match"] = (
            rec.get("current_tier") == rec.get("turbo_tier")
            and "err" not in str(rec.get("current_tier"))
            and "err" not in str(rec.get("turbo_tier"))
        )
        results.append(rec)

    # ---- report ----
    print("=" * 100)
    print("PER-RESTAURANT")
    print("=" * 100)
    for r in results:
        print(f"\n{r['name']}")
        print(f"  url: {r['website_url']}")
        if r.get("current_error"):
            print(f"  CURRENT  ERROR: {r['current_error']}")
        else:
            print(
                f"  CURRENT  {r['current_s']:.2f}s  items={r['current_items']}"
                f"  allergen_items={r.get('current_allergen_items', 0)}"
                f"  llm_calls={r['current_llm_calls']}"
                f"  tier={r.get('current_tier')}  risk={r.get('current_risk')}"
            )
        if r.get("turbo_error"):
            print(f"  TURBO    ERROR: {r['turbo_error']}")
        else:
            print(
                f"  TURBO    {r['turbo_s']:.2f}s  items={r['turbo_items']}"
                f"  allergen_items={r.get('turbo_allergen_items', 0)}"
                f"  llm_calls={r['turbo_llm_calls']}"
                f"  tier={r.get('turbo_tier')}  risk={r.get('turbo_risk')}"
                f"  metrics={r.get('turbo_metrics')}"
            )
        print(
            f"  speedup={r['speedup']:.2f}x  "
            f"tier_match={r['tier_match']}"
            + ("" if r["tier_match"] else "  <-- TIER MISMATCH")
        )

    cur_total = sum((r.get("current_s") or 0.0) for r in results)
    turbo_total = sum((r.get("turbo_s") or 0.0) for r in results)
    overall_speedup = (cur_total / turbo_total) if turbo_total > 0 else 0.0
    cur_llm = sum(r.get("current_llm_calls", 0) for r in results)
    turbo_llm = sum(r.get("turbo_llm_calls", 0) for r in results)

    print("\n" + "=" * 100)
    print("TOTALS")
    print("=" * 100)
    print(f"  CURRENT total: {cur_total:.2f}s   llm_calls={cur_llm}")
    print(f"  TURBO   total: {turbo_total:.2f}s   llm_calls={turbo_llm}")
    print(f"  overall speedup: {overall_speedup:.2f}x")
    mismatches = [r["name"] for r in results if not r["tier_match"]]
    print(f"  tier mismatches: {mismatches if mismatches else 'none'}")


if __name__ == "__main__":
    main()
