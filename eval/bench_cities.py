"""Live multi-city extraction benchmark with website-grounding verification.

For each city: Google Places (food-only) -> discover menu sources -> extract
items. Records per-restaurant item counts, then VERIFIES realness by re-fetching
a menu page and checking how many extracted item names actually appear on it.

Usage: python scripts/bench_cities.py "New York, NY" "Burlington, VT" ...
Writes data/city_bench_<stamp>.json and prints a summary.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.config import get_user_agent
from safeplate.geo import geocode_location
from safeplate.menu_sources import discover_menu_sources_for_url, MenuSourceError
from safeplate.menu_text import extract_menu_items_from_sources
from safeplate.places import is_food_place
from safeplate.providers.google_places import fetch_nearby_restaurants as fetch_google
from safeplate.page_fetch import fetch_html_page, PageFetchError
from safeplate.soup import make_soup

UA = get_user_agent()
KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def grounded_fraction(item_names, page_text):
    hay = norm(page_text)
    if not hay or not item_names:
        return None, 0
    hits = sum(1 for n in item_names if len(norm(n)) >= 5 and norm(n) in hay)
    return hits / len(item_names), hits


def run_city(city: str, limit: int = 12) -> dict:
    coord = geocode_location(city, user_agent=UA)
    # Widen the radius if the centroid yields too few food places — robust to
    # geocode imprecision in small towns (e.g. Ithaca's centroid is ~4 km west
    # of downtown).
    food = []
    for radius in (2000, 5000, 9000):
        rows = fetch_google(latitude=coord.latitude, longitude=coord.longitude,
                            radius_meters=radius, limit=limit, api_key=KEY, user_agent=UA)
        food = [r for r in rows if is_food_place(r.categories)]
        if len(food) >= 5:
            break
    with_site = [r for r in food if (r.website_url or "").strip()]

    restaurants = []
    for r in with_site:
        try:
            srcs = discover_menu_sources_for_url(
                website_url=r.website_url, restaurant_name=r.name,
                restaurant_source_id=r.source_id, user_agent=UA, crawl_depth=1, max_workers=4)
        except MenuSourceError:
            srcs = []
        items = []
        if srcs:
            rowdicts = [{"restaurant_name": r.name, "restaurant_source_id": r.source_id,
                         "candidate_url": s.candidate_url, "source_type": s.source_type,
                         "validation_status": s.validation_status,
                         "is_primary_menu_candidate": str(s.is_primary_menu_candidate)} for s in srcs]
            try:
                items = extract_menu_items_from_sources(menu_source_rows=rowdicts, user_agent=UA, max_workers=4)
            except Exception:
                items = []
        uniq = list(dict.fromkeys(i.item_name.strip() for i in items if i.item_name.strip()))
        restaurants.append({
            "name": r.name, "website": r.website_url,
            "menu_source": srcs[0].candidate_url if srcs else "",
            "n_sources": len(srcs), "n_items": len(uniq),
            "sample_items": uniq[:12],
        })

    return {
        "city": city,
        "total_food": len(food),
        "with_website": len(with_site),
        "with_sources": sum(1 for x in restaurants if x["n_sources"]),
        "with_items": sum(1 for x in restaurants if x["n_items"]),
        "total_items": sum(x["n_items"] for x in restaurants),
        "restaurants": restaurants,
    }


def verify_realness(city_result: dict, sample: int = 4) -> list:
    """Re-fetch menu pages and check how many extracted names appear on them."""
    checks = []
    cands = [x for x in city_result["restaurants"] if x["n_items"] >= 3 and x["menu_source"]][:sample]
    for x in cands:
        try:
            html = fetch_html_page(x["menu_source"], user_agent=UA).html
            text = make_soup(html).get_text(" ", strip=True)
        except (PageFetchError, Exception):
            checks.append({"name": x["name"], "status": "page_unreachable", "grounded": None})
            continue
        frac, hits = grounded_fraction(x["sample_items"], text)
        checks.append({"name": x["name"], "url": x["menu_source"],
                       "grounded_frac": round(frac, 2) if frac is not None else None,
                       "hits": hits, "checked": len(x["sample_items"]),
                       "examples": x["sample_items"][:6]})
    return checks


def main():
    cities = sys.argv[1:] or ["Burlington, VT"]
    out = {"generated": datetime.now(timezone.utc).isoformat(), "cities": []}
    for city in cities:
        t = time.time()
        try:
            res = run_city(city)
            res["verification"] = verify_realness(res)
            res["seconds"] = round(time.time() - t)
            out["cities"].append(res)
            cov = res["with_items"] / res["with_website"] * 100 if res["with_website"] else 0
            print(f"{city:22} food={res['total_food']:2} site={res['with_website']:2} "
                  f"sources={res['with_sources']:2} items={res['with_items']:2} "
                  f"({cov:.0f}% of sites) totalItems={res['total_items']:4}  [{res['seconds']}s]", flush=True)
        except Exception as exc:
            print(f"{city:22} ERROR {exc}", flush=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = ROOT / "data" / f"city_bench_{stamp}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nSaved {path}")


if __name__ == "__main__":
    main()
