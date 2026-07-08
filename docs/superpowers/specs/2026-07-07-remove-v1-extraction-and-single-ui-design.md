# Remove v1 extraction & collapse to a single UI — design

**Date:** 2026-07-07
**Status:** Approved (scope + sub-decisions confirmed by user)

## Goal

Retire the legacy v1 menu-extraction engine so `extraction2` (v2) is the sole
extraction path, and retire the "classic" UI skin (plus two orphan templates) so
the "green" editorial design is the single, default, and only front-end.

Two independent axes, done together:

- **Extraction:** delete the v1 prose-heuristic item extractors and the eval
  scaffolding that exists only to run/compare v1; keep the shared primitives v2
  depends on; repoint the surviving worldwide benchmark to v2.
- **UI:** make the green template the only template, remove the theme
  toggle/cookie/query machinery, and delete the classic + orphan templates.

Non-goal: the `rules` vs `ai` **scoring** engines are out of scope — both stay.
"v1/v2" in this task refers to *extraction* only.

## Context / current state

- The live app path (`api_server` → `search_service`/`menu_service` →
  `extraction2.discover.discover_and_extract`) already runs v2 always. Config
  says "Extraction is always the structured pipeline now."
- v1 survives only as: (a) prose-heuristic item extractors inside
  `menu_text.py`, and (b) eval harnesses that run/compare them.
- `menu_text.py` is **shared**: it also owns primitives v2 imports
  (`MenuItemRecord`, `ALLERGEN_TERMS`, `_matched_terms`, `_pdf_text_from_bytes`).
  So v1 removal is surgical, not a file delete.
- `menu_sources.py` is **live** (imported by `brave_search.py` and extraction2
  for *discovery*). Discovery ≠ v1 item extraction — `menu_sources.py` stays.
- UI: `_APP_TEMPLATE_PATHS` maps `classic` → `app_template.html` and `green` →
  `app_template_green.html`; a `?theme=` param + `sp_theme` cookie + an in-page
  toggle pick between them. `app_template_alt.html` / `app_template_alt2.html`
  are orphans (not wired anywhere).
- `DESIGN.md`, `pages.py`, and the impeccable skill all treat
  `app_template.html` as the token source of truth.

## Part A — Extraction: v2 becomes the only engine

### What "v1" is here
The prose-heuristic item extractors and their per-source dispatch cluster in
`menu_text.py`:
- `_extract_menu_items_from_html`
- `_extract_menu_items_from_text`
- `_extract_schema_org_menu_items_from_html`
- internal callers that only exist to run the above: `extract_text_for_menu_source`,
  `_extract_pdf_items_from_bytes`, `_recover_html_items`, and their dispatcher

…plus the eval scaffolding that exists only to run/compare v1.

**Reachability rule (must verify per function during implementation):** delete a
function only if, after removing eval + v1 tests, its *only* remaining callers
were v1/eval. If a function is reachable from the live app path or from a
surviving v2/shared consumer, keep it. The pytest suite + the offline gate are
the safety net.

### Delete
- The v1 prose-heuristic item-extractor cluster in `menu_text.py` (verified
  external callers today: `eval/bench_extraction.py`, `eval/compare_engines.py`,
  `tests/test_menu_text.py` — all handled below).
- `eval/compare_engines.py` — its whole purpose is the v1-vs-v2 comparison;
  obsolete once v1 is gone.
- The v1-specific tests in `tests/test_menu_text.py` (the cases that call the
  deleted extractors). Keep tests covering surviving primitives such as
  `_matched_terms`.

### Preserve (v2 depends on these)
`MenuItemRecord`, `ALLERGEN_TERMS`, `_matched_terms`, `_pdf_text_from_bytes`,
`embedded_json.extract_items_from_embedded_json`, and all of `menu_sources.py`.

### Repoint (don't delete)
`eval/bench_extraction.py` is a quality-gate live signal (worldwide /
currency-diversity snapshot benchmark). Its `extract_for_snapshot` currently
calls the v1 extractors; switch it to run `extraction2.extract_menu` over the
same frozen snapshots (mirroring how `compare_engines.v2_extract` invoked v2:
`payload_from_html` / `payload_from_pdf_text` → `extract_menu`). This keeps the
benchmark meaningful against v2 and drops the v1 imports.

`eval/bench_cities.py` imports only `menu_sources` *discovery* — no change.

## Part B — UI: green becomes the only design

- **Rename** `app_template_green.html` → `app_template.html` (delete the old
  classic file first). Deliberate: keeps `DESIGN.md`, `pages.py`, and the
  impeccable skill's source-of-truth pointer correct with zero repointing.
- **Strip the theme toggle** from the renamed template: the two
  `.theme-switch` / `.nav-theme` toggle groups and their CSS (green source
  ~lines 145–168, 762–783, 1006–1023). Remove the classic/new-design links.
- **Collapse** `app_html(theme)` → `app_html()` serving the single template;
  keep the mtime-based hot-reload cache (simplified to one entry, no per-theme
  dict).
- **Remove** from `api_server.do_GET("/")`: the `?theme=` query parse, the
  `sp_theme` cookie read, the `set_cookie` write, and the classic/green branch.
  Serve `app_html()` directly.
- **Remove** `_APP_TEMPLATE_PATHS` / `_app_html_cache` per-theme structure
  (replace with a single path + single-slot cache), and the two-skin comment.
- **Delete** `app_template_alt.html` and `app_template_alt2.html`.

## Data flow / behavior impact

The served "/" page becomes the green content byte-for-byte (rename + toggle
markup removed). The extraction path already runs v2, so menu results are
unchanged. No API contract, JS/DOM contract, or scoring behavior changes.

## Error handling / edge cases

- A stale `sp_theme=classic` cookie in an existing browser must not break: after
  removal the handler ignores cookies entirely and always serves the one page.
- `app_html()` keeps the "serve last good copy on transient read error" behavior.
- Any remaining reference to `app_template_green.html` (e.g. recent commit
  touched "the green template") must be updated to `app_template.html` — grep
  the repo for `_green`, `app_template_alt`, `theme=`, `sp_theme`, and the
  deleted extractor names before finishing.

## Testing / verification (the gate must stay green)

- `pytest` (full suite; v1 tests removed, everything else green).
- `python eval/bench_pipeline.py` — v2 quality holds.
- `python eval/safety_eval.py` — safety metrics hold.
- `python eval/bench_multi_allergen.py` — nut-parity matches the committed
  baseline (byte-identical scoring).
- `python eval/bench_extraction.py` runs against v2 without error (repointed).
- Manual/preview smoke: GET "/" serves the green UI with no toggle; a
  `?theme=classic` URL and a stale `sp_theme` cookie both still serve the green
  page.

## Out of scope
- `rules` vs `ai` scoring engines (both retained).
- `menu_sources.py` discovery, `embedded_json.py`, and other shared modules
  (retained; only v1 *imports into* eval change).
- Any redesign of the green UI itself.
