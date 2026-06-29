"""Benchmark: how effective is SafePlate's extraction on major chains worldwide?

For each chain: resolve a real location+website via Google Places Text Search (in the
chain's HOME region), then run the production extraction (discover_and_extract) and
record compact per-chain effectiveness metrics to a JSONL. Single process with bounded
internal concurrency so the shared Brave/Gemini rate-limit buckets actually apply (no
cross-process 429 contamination that would corrupt the measurement).

Usage:  python eval/bench_chains.py [--limit N] [--only SUBSTR] [--out PATH]
"""
from __future__ import annotations
import argparse, json, os, sys, time, traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# self-sufficient import (so `python eval/bench_chains.py` works without PYTHONPATH)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from safeplate.config import (get_user_agent, get_google_places_api_key,
    get_gemini_api_key, get_brave_search_api_key, get_gemini_model)
from safeplate.extraction2.discover import discover_and_extract
from safeplate.textutil import registrable_domain

import urllib.request

# (chain, Places query in HOME region, home-country code)
CHAINS = [
    ("McDonald's", "McDonald's San Jose CA", "US"),
    ("Burger King", "Burger King San Jose CA", "US"),
    ("Starbucks", "Starbucks Seattle WA", "US"),
    ("Subway", "Subway Chicago IL", "US"),
    ("KFC", "KFC Louisville KY", "US"),
    ("Taco Bell", "Taco Bell Irvine CA", "US"),
    ("Chick-fil-A", "Chick-fil-A Atlanta GA", "US"),
    ("Chipotle Mexican Grill", "Chipotle Denver CO", "US"),
    ("Domino's Pizza", "Domino's Pizza Ann Arbor MI", "US"),
    ("Dunkin'", "Dunkin Boston MA", "US"),
    ("Wendy's", "Wendy's Columbus OH", "US"),
    ("Tim Hortons", "Tim Hortons Toronto Ontario", "CA"),
    ("Nando's", "Nando's London UK", "GB"),
    ("Pret a Manger", "Pret a Manger London UK", "GB"),
    ("Greggs", "Greggs London UK", "GB"),
    ("Costa Coffee", "Costa Coffee London UK", "GB"),
    ("Vapiano", "Vapiano Berlin Germany", "DE"),
    ("Jollibee", "Jollibee Manila Philippines", "PH"),
    ("MOS Burger", "MOS Burger Tokyo Japan", "JP"),
    ("Yoshinoya", "Yoshinoya Tokyo Japan", "JP"),
    ("Din Tai Fung", "Din Tai Fung Taipei Taiwan", "TW"),
    ("Haidilao Hot Pot", "Haidilao Singapore", "SG"),
    ("Guzman y Gomez", "Guzman y Gomez Sydney Australia", "AU"),
]

UA = get_user_agent(); GKEY = get_google_places_api_key()
AKEY = get_gemini_api_key(); BKEY = get_brave_search_api_key(); MODEL = get_gemini_model()
PER_CHAIN_TIMEOUT = 80.0


def places_text_search(query: str) -> dict:
    body = json.dumps({"textQuery": query}).encode()
    req = urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchText", data=body,
        headers={"Content-Type": "application/json", "X-Goog-Api-Key": GKEY,
                 "X-Goog-FieldMask": "places.displayName,places.websiteUri,places.formattedAddress"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def run_one(entry) -> dict:
    chain, query, home = entry
    rec = {"chain": chain, "query": query, "home_country": home, "ok": False, "error": None}
    t0 = time.monotonic()
    try:
        data = places_text_search(query)
        places = data.get("places", [])
        if not places:
            rec["error"] = "no place found"; return rec
        p = places[0]
        name = p.get("displayName", {}).get("text") or chain
        site = p.get("websiteUri") or ""
        addr = p.get("formattedAddress") or ""
        host = urlparse(site).netloc.lower()
        rec.update(resolved_name=name, website=site, address=addr, host=host,
                   regdomain=registrable_domain(host), tld=host.rsplit(".", 1)[-1] if host else "")
        if not site:
            rec["error"] = "no website_url"; return rec

        cands, result = discover_and_extract(
            site, user_agent=UA, restaurant_name=name, address=addr,
            api_key=AKEY, model=MODEL, brave_api_key=BKEY,
            use_result_cache=False, use_cache=True)

        items = result.items
        src_hosts = Counter(urlparse(i.menu_source_url).netloc.lower() for i in items if i.menu_source_url)
        rec.update(
            ok=True,
            n_candidates=len(cands),
            cand_kinds=dict(Counter(c.kind for c in cands)),
            cand_sources=dict(Counter(c.source for c in cands)),
            item_count=len(items),
            allergen_item_count=sum(1 for i in items if i.allergen_terms),
            methods=dict(Counter(i.extraction_method for i in items)),
            llm_calls=result.llm_calls,
            incomplete=result.incomplete,
            allergy_signals=len(result.allergy_signals),
            coverage=[{"found": c.found, "kind": c.payload_kind, "interp": c.interpreter,
                       "items": c.item_count, "conf": round(c.confidence, 2),
                       "reason": c.reason, "host": urlparse(c.url).netloc.lower()}
                      for c in result.coverage],
            source_hosts=dict(src_hosts.most_common(6)),
            sample_items=[i.item_name for i in items[:6]],
        )
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["trace"] = traceback.format_exc()[-600:]
    finally:
        rec["elapsed_s"] = round(time.monotonic() - t0, 1)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=str,
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "datasets", "chain_bench_results.jsonl"))
    args = ap.parse_args()
    chains = CHAINS
    if args.only:
        chains = [c for c in chains if args.only.lower() in c[0].lower()]
    if args.limit:
        chains = chains[:args.limit]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    # Resumable: skip chains already recorded (the background runner keeps getting
    # killed mid-run, so accumulate across invocations instead of overwriting).
    done = set()
    if os.path.exists(args.out):
        for line in open(args.out, encoding="utf-8"):
            try:
                done.add(json.loads(line).get("chain"))
            except ValueError:
                pass
    chains = [c for c in chains if c[0] not in done]
    print(f"benchmarking {len(chains)} chains ({len(done)} already done) -> {args.out}", flush=True)
    if not chains:
        print("all chains already done.", flush=True); return

    results = []
    with open(args.out, "a", encoding="utf-8") as fh, \
         ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, c): c for c in chains}
        for fut in as_completed(futs):
            chain = futs[fut][0]
            try:
                rec = fut.result(timeout=PER_CHAIN_TIMEOUT + 30)
            except Exception as exc:
                rec = {"chain": chain, "ok": False, "error": f"outer:{type(exc).__name__}:{exc}"}
            results.append(rec)
            fh.write(json.dumps(rec) + "\n"); fh.flush()
            print(f"  [{len(results):2}/{len(chains)}] {chain:24} "
                  f"items={rec.get('item_count','-'):>4} allergen={rec.get('allergen_item_count','-'):>4} "
                  f"cands={rec.get('n_candidates','-'):>3} {rec.get('error') or ''}", flush=True)

    ok = [r for r in results if r.get("ok")]
    print("\n=== SUMMARY ===", flush=True)
    print(f"chains={len(results)} resolved+ran={len(ok)} errors={len(results)-len(ok)}", flush=True)
    if ok:
        any_items = sum(1 for r in ok if r.get("item_count", 0) > 0)
        any_allerg = sum(1 for r in ok if r.get("allergen_item_count", 0) > 0)
        print(f"with >=1 item: {any_items}/{len(ok)} | with allergen data: {any_allerg}/{len(ok)}", flush=True)


if __name__ == "__main__":
    main()
