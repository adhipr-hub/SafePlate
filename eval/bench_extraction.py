"""Reproducible worldwide menu-extraction benchmark.

`--collect` fetches restaurants in several world cities (different currencies),
discovers menu sources, and SNAPSHOTS the raw menu pages to disk once. The
default mode then re-runs the current extraction logic against those frozen
snapshots and prints performance stats, so each code iteration is directly
comparable (no re-fetching, no site drift, polite to the sites).

Usage:
  python scripts/bench_extraction.py --collect    # one-time, network
  python scripts/bench_extraction.py              # offline, re-run each iteration
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.config import get_user_agent
from safeplate.embedded_json import extract_items_from_embedded_json
from safeplate.menu_sources import discover_menu_sources_for_url, MenuSourceError
from safeplate.menu_text import (
    _extract_menu_items_from_html,
    _extract_menu_items_from_text,
    _extract_schema_org_menu_items_from_html,
)
from safeplate.page_fetch import fetch_html_page, PageFetchError
from safeplate.http_client import http_get, HttpError, HttpConnectionError

SNAP_DIR = ROOT / "data" / "bench_snapshots"
MANIFEST = SNAP_DIR / "manifest.json"

# (city, currency) — chosen for currency diversity
CITIES = [
    ("New York, NY", "USD $"),
    ("London, UK", "GBP £"),
    ("Paris, France", "EUR €"),
    ("Tokyo, Japan", "JPY ¥"),
    ("Mumbai, India", "INR ₹"),
    ("Bangkok, Thailand", "THB ฿"),
]
PER_CITY = 8
HTML_TYPES = {"website_link", "nutrition_or_allergen_page", "schema_org_menu", "ordering_page"}


# --------------------------------------------------------------------------- #
# Collection (network, run once)
# --------------------------------------------------------------------------- #
def collect() -> None:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    ua = get_user_agent()
    manifest = []
    for city, currency in CITIES:
        csv_path = _ensure_city_csv(city)
        if not csv_path:
            print(f"  {city}: no restaurant CSV, skipping")
            continue
        import csv as _csv
        rows = list(_csv.DictReader(open(csv_path, encoding="utf-8-sig")))
        rows = [r for r in rows if (r.get("website_url") or "").strip()][:PER_CITY]
        print(f"{city}: snapshotting {len(rows)} restaurants")
        for r in rows:
            try:
                sources = discover_menu_sources_for_url(
                    website_url=r["website_url"], restaurant_name=r.get("name"),
                    restaurant_source_id=r.get("source_id"), user_agent=ua,
                    crawl_depth=2, max_workers=4,
                )
            except MenuSourceError:
                continue
            for s in sources:
                snap = _snapshot_source(s.candidate_url, s.source_type, ua)
                if snap:
                    manifest.append({
                        "city": city, "currency": currency,
                        "restaurant": r.get("name"), "source_id": r.get("source_id"),
                        "url": s.candidate_url, "source_type": s.source_type, "file": snap,
                    })
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nSnapshot manifest: {MANIFEST} ({len(manifest)} sources)")


def _ensure_city_csv(city: str):
    import glob, os
    slug = re.sub(r"[^a-z0-9]+", "_", city.lower()).strip("_").split("_")[0]
    existing = sorted(glob.glob(str(ROOT / f"data/restaurants_{slug}*.csv")), key=os.path.getmtime)
    if existing:
        return existing[-1]
    print(f"  fetching {city} via Google Places...")
    subprocess.run([sys.executable, str(ROOT / "scripts/fetch_restaurants.py"),
                    "--location", city, "--radius", "1500", "--limit", "12",
                    "--provider", "google"], capture_output=True)
    existing = sorted(glob.glob(str(ROOT / f"data/restaurants_{slug}*.csv")), key=os.path.getmtime)
    return existing[-1] if existing else None


def _snapshot_source(url: str, source_type: str, ua: str):
    import hashlib
    digest = hashlib.sha1(url.encode()).hexdigest()[:16]
    try:
        if source_type in HTML_TYPES:
            html = fetch_html_page(url, user_agent=ua).html
            path = SNAP_DIR / f"{digest}.html"
            path.write_text(html, encoding="utf-8")
            return path.name
        if source_type == "pdf":
            resp = http_get(url, user_agent=ua, timeout=30)
            from pypdf import PdfReader
            text = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(resp.content)).pages)
            if not text.strip():
                return None
            path = SNAP_DIR / f"{digest}.pdf.txt"
            path.write_text(text, encoding="utf-8")
            return path.name
    except (PageFetchError, HttpError, HttpConnectionError, Exception):
        return None
    return None


# --------------------------------------------------------------------------- #
# Extraction + scoring (offline, re-run every iteration)
# --------------------------------------------------------------------------- #
_MODIFIERS = {"beef", "lamb", "chicken", "pork", "veggie", "vegan", "small", "large",
              "regular", "each", "single", "double", "side", "extra", "add"}
_UI_WORDS = ["cart", "checkout", "subtotal", "add to", "select", "choose", "quantity",
             "sign in", "log in", "your order", "view menu", "order online", "served until",
             "page ", "serves", "click", "©", "all rights", "copyright", "www.", "http"]
_CONNECTOR_END = ("with", "and", "&", "of", "the", "in", "on", "to", "de", "la", "or", ",", "-", "~")
_SIZE_RE = re.compile(r"^\s*\d+\s*(oz|ml|g|gm|kg|inch|in|pc|pcs|pieces|cl|l)\b|^\s*\d+\s*/", re.I)
_TIME_RE = re.compile(r"\b\d{1,2}\s*[:.]\s*\d{2}\b|\b\d{1,2}\s*[ap]\.?m\.?\b", re.I)
_CJK_PUNCT = "、。，％~〜・「」『』…"
_CURRENCY_SYMS = "$€£¥₹฿₩₫₪₴₦"
_CURRENCY_CODE_RE = re.compile(r"\b(usd|eur|gbp|jpy|inr|thb|krw|cny|rmb|aud|cad|chf|sgd|hkd|brl|mxn|rs|r\$)\b", re.I)


def looks_noise(name: str) -> bool:
    from safeplate.menu_text import _NON_DISH_EXACT, _NON_DISH_PHRASES
    n = (name or "").strip()
    if len(n) < 2 or len(n) > 60 or not re.search(r"[a-zÀ-ʸऀ-퟿]", n, re.I):
        return True
    if n.lower().strip(" .!*#|") in _NON_DISH_EXACT or any(p in n.lower() for p in _NON_DISH_PHRASES):
        return True
    w = n.split()
    if len(w) >= 4 and len(set(x.lower() for x in w)) <= len(w) // 2:
        return True
    if n[0].islower():
        return True
    if n.lower() in _MODIFIERS:
        return True
    low = n.lower()
    if low.endswith(_CONNECTOR_END) or any(w in low for w in _UI_WORDS):
        return True
    if _SIZE_RE.search(n) or _TIME_RE.search(n):
        return True
    if any(p in n for p in _CJK_PUNCT):           # run-on prose, not an item name
        return True
    if len(n.split()) > 8:                          # sentence-like run-on
        return True
    digits = sum(c.isdigit() for c in n)
    if digits and digits / len(n) > 0.4:           # mostly numbers
        return True
    return False


def price_ok(price: str) -> bool:
    """A *real* price: has a currency symbol/code, or a decimal amount.
    Bare integers are too ambiguous (year/time/quantity) to count as correct."""
    p = (price or "").strip()
    if not p:
        return False
    if any(c in p for c in _CURRENCY_SYMS) or _CURRENCY_CODE_RE.search(p):
        return True
    return bool(re.search(r"\d+[.,]\d{2}(?!\d)", p))


def extract_for_snapshot(entry: dict) -> list:
    text = (SNAP_DIR / entry["file"]).read_text(encoding="utf-8")
    if entry["file"].endswith(".pdf.txt"):
        return _extract_menu_items_from_text(text)
    items = _extract_schema_org_menu_items_from_html(text) + _extract_menu_items_from_html(text)
    if not items:
        items = extract_items_from_embedded_json(text)
    return items


def bench() -> None:
    if not MANIFEST.exists():
        print("No snapshots. Run: python scripts/bench_extraction.py --collect")
        return
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    per_city = defaultdict(lambda: {"items": [], "rest_with": set(), "rest_all": set()})
    methods = Counter()
    for entry in manifest:
        city = entry["city"]
        per_city[city]["rest_all"].add(entry["restaurant"])
        items = extract_for_snapshot(entry)
        if items:
            per_city[city]["rest_with"].add(entry["restaurant"])
        for it in items:
            methods[it.extraction_method] += 1
            per_city[city]["items"].append((entry["restaurant"], it.item_name, it.price))

    print("=" * 78)
    print(f"{'CITY':18} {'rest':>9} {'items':>6} {'uniq':>5} {'priced':>7} {'noise':>6} {'CORRECT':>8}")
    print("-" * 78)
    tot = Counter()
    tot_rest_with = tot_rest = 0
    for city, _cur in CITIES:
        d = per_city.get(city)
        if not d:
            continue
        items = d["items"]
        # Dedupe on (restaurant, name) — price is NOT part of identity, since a
        # price-less listing of the same dish is the same item.
        uniq = {}
        for r, n, p in items:
            uniq.setdefault((r, n.lower().strip()), (r, n, p))
        uniq = list(uniq.values())
        priced = [(r, n, p) for r, n, p in uniq if price_ok(p)]
        # CORRECT = a clean menu-item name. Price is secondary, not required.
        correct = [(r, n, p) for r, n, p in uniq if not looks_noise(n)]
        rest_with, rest_all = len(d["rest_with"]), len(d["rest_all"])
        noise = sum(1 for r, n, p in uniq if looks_noise(n))
        print(f"{city:18} {rest_with:>4}/{rest_all:<4} {len(items):>6} {len(uniq):>5} "
              f"{len(priced):>7} {noise:>6} {len(correct):>8}")
        tot["items"] += len(items); tot["uniq"] += len(uniq)
        tot["priced"] += len(priced); tot["correct"] += len(correct); tot["noise"] += noise
        tot_rest_with += rest_with; tot_rest += rest_all
    print("-" * 78)
    print(f"{'TOTAL':18} {tot_rest_with:>4}/{tot_rest:<4} {tot['items']:>6} {tot['uniq']:>5} "
          f"{tot['priced']:>7} {tot['noise']:>6} {tot['correct']:>8}")
    print("=" * 78)
    cov = tot_rest_with / tot_rest * 100 if tot_rest else 0
    prec = tot["correct"] / tot["uniq"] * 100 if tot["uniq"] else 0
    print(f"Coverage (restaurants with >=1 item): {cov:.0f}%")
    print(f"Precision (correct / unique):         {prec:.0f}%")
    print(f"** CORRECT ITEMS (the headline):      {tot['correct']} **")
    print(f"Methods: {dict(methods)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    args = ap.parse_args()
    if args.collect:
        collect()
    else:
        bench()


if __name__ == "__main__":
    main()
