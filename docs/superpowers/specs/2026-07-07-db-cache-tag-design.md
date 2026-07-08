# Database cache tag (drawer verification chip) — design

**Date:** 2026-07-07
**Status:** approved by user (conversation), pending spec review
**Context:** Phase-1 RDS cache store just shipped (spec:
2026-07-07-rds-cache-store-design.md). The user wants a visible confirmation in
the app that the Postgres cache is really being used: a small tag on a
restaurant's drawer saying the result was *served from* the database, or was
*just saved to* it. Purpose is **verification aid** (for the operator during/after
the EC2→RDS cutover), not a permanent diner-facing trust feature — so wording is
plain, styling is quiet, and honesty about fallback beats polish.

## Requirements

1. In the restaurant menu drawer, show one small chip reporting the result
   cache's provenance for THIS restaurant:
   - `from database` — extraction served from the Postgres result cache.
   - `saved to database` — extraction ran fresh and its result was written to
     Postgres.
   - `from local cache` / `saved locally` — same two states when the disk
     backend served/took the write (no DATABASE_URL, or Postgres errored and
     the store fell back). The wording contrast IS the diagnostic signal.
   - No chip when there is nothing truthful to say: the "raw / no-cache"
     toggle bypasses the result cache entirely, or extraction produced no
     cacheable result (timeout/partial — those are never cached).
2. The tag must report what actually happened, not an inference: only
   `cache_store` knows whether Postgres or disk served a read or took a write,
   so provenance is threaded from the store outward.
3. Default-mode behavior unchanged for existing consumers: new dataclass
   fields default to `None`; `save()` return value is ignored by all existing
   callers; the response's new `cache` key is additive.

## Design (Approach A — thread provenance through the store)

### cache_store (safeplate/cache_store.py)
- New: `load_with_origin(namespace: str, key: str) -> tuple[dict | None, str | None]`
  — same logic as `load`, but returns where the blob came from:
  `"postgres"` (PG hit) or `"disk"` (disk hit, including PG-miss fallthrough
  and PG-error fallback); `(None, None)` on a total miss. `load()` becomes a
  thin wrapper returning just the blob.
- Changed: `save(namespace, key, blob) -> str` now returns the backend that
  actually took the write: `"postgres"` or `"disk"` (disk on fallback or
  disk-only mode). Existing callers ignore the return value.
- Lazy promotion inside `load_with_origin` still reports `"disk"` (that's
  where the hit came from; the promotion is a side effect).

### Extraction result (safeplate/extraction2/schema.py + discover.py)
- `MenuExtractionResult` gains two optional fields, default `None`:
  `cache_origin: str | None` (set on result-cache hit) and
  `cache_saved_to: str | None` (set after a successful fresh-result save).
- `discover.py`: `_load_result_cache` uses `load_with_origin` and stamps
  `cache_origin` on the rehydrated result; the `_save_result_cache` call site
  captures `save()`'s return and stamps `cache_saved_to`. The cached blob
  format is unchanged (the stamp is on the in-memory object only).

### API (safeplate/menu_service.py)
- The drawer payload gains `"cache": {"origin": <str|null>, "savedTo": <str|null>}`
  copied from the extraction result; key omitted entirely when both are null.

### UI (safeplate/app_template.html)
- One `.pvchip`-style chip (muted ink, dot prefix, existing chip geometry) in
  the drawer's provenance/"how we know" row, rendered only when the `cache`
  field is present:
  - origin `postgres` → `From database`; origin `disk` → `From local cache`
  - savedTo `postgres` → `Saved to database`; savedTo `disk` → `Saved locally`
    (sentence case, matching neighboring chips like "Confirmed" / "Estimate")
  - origin wins if both are somehow present (a hit never also saves).
- No new colors; use existing muted chip tokens. No animation. Calm register
  per PRODUCT.md — it's an operator's note, not a feature flourish.

## Testing
- `tests/test_cache_store.py`: `load_with_origin` returns `("postgres" blob, "postgres")`
  on PG hit, `(blob, "disk")` on PG-miss→disk and PG-error→disk, `(None, None)`
  on total miss; `save` returns `"postgres"` on PG write, `"disk"` on fallback
  and in disk-only mode; `load()` still returns bare blobs.
- Call-site test: result-cache hit stamps `cache_origin`; fresh save stamps
  `cache_saved_to` (stub `cache_store`).
- API test: payload carries `cache` when stamps present; omits it when both
  null (raw/no-cache path).
- Existing suite green throughout (fields default None; save return ignored).

## Out of scope (YAGNI)
Community-signals cache tagging; search-list cards (drawer only); any status
page; persisting the stamp in the cached blob; diner-facing copy polish.
