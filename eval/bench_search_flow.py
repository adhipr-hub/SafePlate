"""End-to-end performance harness for the REAL app flow (extraction is the
bottleneck, not the scorer). Hits a running SafePlate server and reports:
  - the current tuning knobs (so a run is self-documenting),
  - cold vs warm search latency (first run is cold; the result cache warms the rest),
  - list <-> drawer consistency (a menu-backed card's score must match its drawer).

    # start the app first (python scripts/start_safeplate_app.py --no-browser), then:
    python eval/bench_search_flow.py --location "Cupertino, CA" --runs 3

Needs a running server + live providers/Gemini (like the other eval/ scripts), so it
isn't a unit test. Tune _LIST_MENU_BACKED_TOP_N / SAFEPLATE_GEMINI_CONCURRENCY /
_MAX_SOURCES / the list budget, re-run, and compare.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safeplate.config import get_gemini_concurrency  # noqa: E402


def _knobs() -> dict:
    import safeplate.search_service as la
    from safeplate.extraction2 import discover

    return {
        "SAFEPLATE_GEMINI_CONCURRENCY": get_gemini_concurrency(),
        "_LIST_MENU_BACKED_TOP_N": la._LIST_MENU_BACKED_TOP_N,
        "_LIST_ASSESS_WORKERS": la._LIST_ASSESS_WORKERS,
        "_LIST_ASSESS_BUDGET_S": la._LIST_ASSESS_BUDGET_S,
        "_MAX_SOURCES (in discover)": getattr(discover, "_MAX_SOURCES_DOC", "see discover.py (4)"),
        "_RESULT_CACHE_TTL_days": discover._RESULT_CACHE_TTL / 86400,
    }


def _post(url: str, path: str, body: dict, timeout: float = 180) -> tuple[float, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8"))
    return time.perf_counter() - t0, payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--location", default="Cupertino, CA")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--engine", default="ai", help="ai | rules")
    ap.add_argument("--severity", default="allergy")
    args = ap.parse_args()

    print("Tuning knobs:")
    for k, v in _knobs().items():
        print(f"  {k:32s} {v}")
    print()

    body = {"provider": "auto", "radius": 1800, "limit": 12, "severity": args.severity,
            "crossContact": "moderate", "scoringEngine": args.engine, "location": args.location}

    print(f"SEARCH '{args.location}' (engine={args.engine}) x{args.runs} "
          "(run 1 = cold; cache warms the rest):")
    rows = None
    for i in range(args.runs):
        dt, payload = _post(args.url, "/api/search", body)
        rows = payload.get("rows", [])
        menu_backed = sum(1 for r in rows if r.get("coverageStatus") == "menu_backed")
        print(f"  run {i+1}: {dt:6.1f}s  ({len(rows)} rows, {menu_backed} menu-backed)")

    # list <-> drawer consistency on a menu-backed card
    card = next((r for r in (rows or []) if r.get("coverageStatus") == "menu_backed"), None)
    if not card:
        print("\n(no menu-backed card to check list<->drawer consistency)")
        return
    cap = card.get("menuBackedRisk") or card.get("allergenPrior") or {}
    dt, menu = _post(args.url, "/api/menu", {
        "name": card.get("name"), "sourceId": card.get("source_id"),
        "websiteUrl": card.get("website_url"), "address": card.get("address"),
        "categories": card.get("categories"), "severity": args.severity,
        "crossContact": "moderate", "scoringEngine": args.engine})
    drawer = (menu.get("summary", {}) or {}).get("menuBackedRisk", {}) or {}
    print(f"\nlist<->drawer consistency for {card.get('name','?')[:30]!r} (drawer fetch {dt:.1f}s):")
    print(f"  list:   tier={cap.get('tier')} risk={cap.get('risk')}")
    print(f"  drawer: tier={drawer.get('tier')} risk={drawer.get('risk')}")
    print("  MATCH" if cap.get("tier") == drawer.get("tier") else "  *** MISMATCH ***")


if __name__ == "__main__":
    main()
