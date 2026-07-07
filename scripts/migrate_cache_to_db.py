"""One-shot import of the on-disk JSON caches into the Postgres cache_entries
table -- so the warm cache built up on this machine keeps saving API money
after the cutover to RDS. Idempotent: re-running upserts the same entries.

Usage (after DATABASE_URL is set in the environment):
    python scripts/migrate_cache_to_db.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safeplate import cache_store
from safeplate.config import get_cache_dir, get_database_url

# The seven paid-API namespaces (spec: docs/superpowers/specs/
# 2026-07-07-rds-cache-store-design.md). http/robots stay on disk on purpose.
NAMESPACES = [
    "extraction2_result",
    "extraction2_llm",
    "extraction2_pdfmatrix",
    "extraction2_allergy",
    "community_signals",
    "diet_llm",
    "llm_menu",
]


def main() -> int:
    if not get_database_url():
        print("DATABASE_URL is not set -- nothing to migrate into.", file=sys.stderr)
        return 1
    if cache_store._get_pool() is None:
        print("Could not connect to the database (see warning above).", file=sys.stderr)
        return 1
    total = skipped = 0
    for namespace in NAMESPACES:
        count = 0
        for path in sorted((get_cache_dir() / namespace).glob("*.json")):
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                skipped += 1
                continue
            if not isinstance(blob, dict):
                skipped += 1
                continue
            cache_store.save(namespace, path.stem, blob)
            count += 1
        total += count
        print(f"{namespace}: {count} entries")
    print(f"imported {total} entries ({skipped} unreadable files skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
