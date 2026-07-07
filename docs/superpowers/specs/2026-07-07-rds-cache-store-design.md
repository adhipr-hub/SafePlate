# RDS cache store — phase 1 of Postgres integration (design)

**Date:** 2026-07-07
**Status:** approved by user (conversation), pending spec review
**Phase context:** phase 1 of 3 for the RDS Postgres integration. Phase 2 (user
accounts + allergy profiles) and phase 3 (community reports) will reuse the
connection plumbing built here. Hosting is AWS: the app runs on EC2, the
database is RDS PostgreSQL (`safeplatedb`, us-east-2, db.t4g.micro). EC2↔RDS
networking is already configured via the RDS console's automated EC2 connection
(security groups `rds-ec2-1` / `ec2-rds-1`).

## Problem

Every expensive result SafePlate computes (Gemini menu extractions, community
signals, diet/allergy LLM calls) is cached as JSON files under
`data/.cache/<namespace>/<hash>.json` on the local disk. That cache is
per-machine and dies with the instance. Moving it to RDS Postgres makes it
durable across redeploys/instance replacement and shared across any future
second instance — and every cache hit is real API money saved.

## Approach (chosen: shared cache-store layer)

One new module, `safeplate/cache_store.py`, exposing:

- `load(namespace: str, key: str) -> dict | None`
- `save(namespace: str, key: str, blob: dict) -> None`

Two backends behind that interface:

- **Disk** — byte-identical to today's behavior (`get_cache_dir()/<ns>/<key>.json`).
  Used when no database is configured. This keeps dev machines, tests, and the
  quality gate unchanged.
- **Postgres** — used automatically when `DATABASE_URL` is set (standard
  `postgresql://user:pass@host:5432/dbname` form). Driver: `psycopg[binary]>=3`
  with `psycopg_pool` (small pool, ~4 connections; db.t4g.micro is fine with
  that). TLS: default to `sslmode=require` if the URL doesn't specify one.

Rejected alternatives: (B) Postgres hardcoded into only the result cache —
duplicates work for the other six namespaces and gives phases 2–3 nothing;
(C) full relational schema for restaurants/menus — rewrites how the pipeline
stores data rather than where, big risk for no phase-1 benefit.

## Scope: which caches move

**In scope — all seven paid-API (money-saver) namespaces:**

| namespace | call site |
| --- | --- |
| `extraction2_result` | `safeplate/extraction2/discover.py` (`_load_result_cache` / `_save_result_cache`) |
| `extraction2_llm` | `safeplate/extraction2/interpret_llm.py` (~line 228) |
| `extraction2_pdfmatrix` | `safeplate/extraction2/interpret_llm.py` (~line 156) |
| `extraction2_allergy` | `safeplate/extraction2/allergy_signals.py` (~line 179) |
| `community_signals` | `safeplate/community_signals.py` (`_cache_path` et al.) |
| `diet_llm` | `safeplate/diet_llm.py` (~line 126) |
| `llm_menu` | `safeplate/menu_fetch_llm.py` (~line 442, v1 pipeline) |

**Out of scope — stay on disk:** `http` (raw fetched pages: bulky, free to
refetch) and `robots` (robots.txt cache). Also out of scope: any schema for
phases 2–3, Secrets Manager integration (the user sets `DATABASE_URL` manually;
live secret fetch is a possible later upgrade), moving off `http.server`.

## Schema

```sql
CREATE TABLE IF NOT EXISTS cache_entries (
    namespace  TEXT        NOT NULL,
    key        TEXT        NOT NULL,
    payload    JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key)
);
```

Created idempotently on first connection (`CREATE TABLE IF NOT EXISTS` at pool
init). `payload` is the exact JSON blob that is written to the file today —
including the caller's `{"at": <timestamp>, ...}` shape — so **all TTL,
version-bump, and negative-caching logic stays in the callers, unchanged**.
Saves are upserts (`INSERT ... ON CONFLICT (namespace, key) DO UPDATE`).
`created_at` is bookkeeping only (future pruning), not consulted for expiry.

## Behavior rules

1. **Backend selection:** `DATABASE_URL` set → Postgres; unset → disk. Read via
   a `safeplate/config.py` getter (`get_database_url()`), consistent with every
   other env knob.
2. **Read path (Postgres mode):** try Postgres → on miss, try disk → if disk
   hits, return it **and promote it into Postgres** (lazy migration of the warm
   cache). On Postgres *error*, log a warning and serve from disk.
3. **Write path (Postgres mode):** upsert into Postgres. On error, log and
   write to disk instead (never lose a paid result).
4. **Failure semantics:** a database problem must never fail a request or crash
   the app — degrade to disk silently (one rate-limited warning per incident,
   not one per call). Connection attempts use a short timeout (~5s connect) so
   a dead DB can't stall searches.
5. **Default equivalence:** with `DATABASE_URL` unset, behavior is
   byte-identical to today (protects the existing quality gate).
6. **`no_cache` / "raw" toggle:** unchanged — callers already skip load/save;
   the store doesn't need to know.

## Call-site change pattern

Each of the seven sites currently does some variant of
`path.read_text()` / `path.write_text()` on `get_cache_dir()/<ns>/<hash>.json`.
Each becomes `cache_store.load("<ns>", <hash>)` / `cache_store.save(...)`, with
the JSON parse/serialize moving into the store. Hash-key computation, TTL
checks, and "don't cache failures/partials" guards stay at the call site.

## Migration of the existing warm cache

Two mechanisms, both included:

- **Lazy (automatic):** the read-path promotion above migrates entries as they
  are next requested.
- **Bulk (one-shot):** `scripts/migrate_cache_to_db.py` walks
  `data/.cache/<ns>/*.json` for the seven in-scope namespaces and upserts them
  into `cache_entries`. Idempotent; safe to re-run. Run once on the EC2 at
  cutover so no Gemini spend is repeated.

## Dependencies

`requirements.txt` gains `psycopg[binary]>=3.1` (and `psycopg-pool`). Import of
psycopg is deferred/guarded so machines without it installed still run in
disk mode (same graceful-degradation pattern as playwright).

## Testing (TDD)

- **Store unit tests (no live DB):** disk backend round-trip; backend selection
  by env var; Postgres backend against a stubbed/fake connection — upsert
  semantics, miss → disk fallback + promotion, error → disk fallback + single
  warning.
- **Call-site tests:** each migrated namespace round-trips through the store in
  disk mode (proves the mechanical swap didn't change behavior).
- **Default-equivalence test:** with `DATABASE_URL` unset, the store writes the
  same file paths/contents as the old code (existing suite must pass untouched).
- **Optional integration test:** full round-trip against a real database, runs
  only when `SAFEPLATE_TEST_DATABASE_URL` is set (skipped otherwise, incl. CI).
- **Migration script test:** temp dir with fake cache files → stubbed store
  receives the right upserts.

## EC2 cutover checklist (ops, after code ships)

1. `pip install -r requirements.txt` on the EC2 (pulls psycopg).
2. Get the master password from Secrets Manager (RDS console → the
   `safeplatedb` secret) once.
3. Set `DATABASE_URL=postgresql://safeplate:<password>@safeplatedb.cfu68uauyqn4.us-east-2.rds.amazonaws.com:5432/postgres?sslmode=require`
   in the app's environment (systemd unit / `.env` — never committed to git).
4. `python scripts/migrate_cache_to_db.py` to bulk-import the warm cache.
5. Restart the app; verify a repeat search is a cache hit and
   `SELECT count(*) FROM cache_entries` grows.

## Success criteria

- Repeat searches hit Postgres (zero Gemini calls) on the EC2.
- Existing test suite green with no `DATABASE_URL` set.
- Killing the DB connection mid-run degrades to disk with a warning, no
  failed requests.
- The warm disk cache is fully importable without re-spending API calls.
