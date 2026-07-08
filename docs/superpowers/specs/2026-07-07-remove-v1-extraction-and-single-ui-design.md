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

### What "v1" actually is (revised after tracing)
A deeper trace showed the removable "v1" is the **orchestration layer** in
`menu_text.py` — the `*_from_sources` public API and its per-source
fetch/parse/dispatch cluster — plus the prose text parser. The low-level *soup*
schema/allergen parsers are **shared with v2** and stay.

**v1 orchestration + prose parser (delete, subject to the reachability rule):**
- `extract_menu_text_from_sources`, `extract_menu_items_from_sources`,
  `extract_menu_from_sources` (top-level v1 API)
- `_extract_source_once`, `_items_from_candidates`, `_build_text_record`,
  `_stage_workers`, `_eligible_source_rows`, `_should_extract_row`
- `extract_text_for_menu_source`, `extract_visible_text`, `extract_pdf_text`
- `_extract_pdf_items_from_bytes`, `_recover_html_items`
- html-string wrappers `_extract_menu_items_from_html`,
  `_extract_schema_org_menu_items_from_html`
- prose text parser `_extract_menu_items_from_text` and its private helpers
  (`_records_from_price_lines`, `_price_segments`, `_price_matches`,
  `_price_count`, `_is_plausible_bare_price`, `_dedupe_*`, etc.) **iff** grep
  proves zero remaining non-def callers after the above go
- the CLI writer helpers used only by the deleted script:
  `build_menu_text_output_paths`, `write_menu_text_csv`, `write_menu_text_json`
  (and `write_menu_items_*` / `build_menu_item_output_paths` iff orphaned)

**v1-exclusive external consumers (delete):**
- `scripts/extract_menu_text.py` — the documented "backbone" CLI, built entirely
  on the v1 `*_from_sources` API. (User decision: delete the tooling too.)
- `eval/bench_cities.py` — city-coverage harness on the v1 API.
- `eval/compare_engines.py` — v1-vs-v2 comparison; obsolete once v1 is gone.
- v1-specific cases in `tests/test_menu_text.py` (those calling the deleted
  extractors). Keep cases covering surviving primitives (`_matched_terms`, etc.).
- README sections describing the `extract_menu_text.py` pipeline
  (README.md ~lines 326, 376, 410, 454).

**Reachability rule (verify per function):** delete a function only when grep
across `safeplate/`, `eval/`, `tests/`, `scripts/` shows zero remaining non-`def`
callers after its consumers are removed. If anything live/v2/shared still calls
it, keep it. The pytest suite + offline gate are the safety net after every
removal.

### Preserve (v2 / shared consumers depend on these — DO NOT delete)
- `MenuItemRecord`, `MenuTextRecord` (used by `demo_fixtures.py`,
  `test_local_app_demo.py`)
- `ALLERGEN_TERMS`, `_matched_terms`, `_matched_terms_in`, `_term_present`,
  `_enclosing_word`, `_dietary_and_allergen_terms`
- `_pdf_text_from_bytes` / `_pdf_text_from_bytes_inner` (used by
  `extraction2/acquire.py`)
- `_extract_schema_org_menu_items_from_soup` + its `_schema_*` / `_microdata_*`
  helper web (used by `extraction2/interpret_structured.py`)
- `_looks_like_item_name`, `_classlist_text`, `_clean_text` (used by
  `allergen_matrix.py`, a live/v2 module)
- `read_csv_rows` (used by `scripts/extract_menu_evidence_gemini.py`)
- `embedded_json.extract_items_from_embedded_json`, and all of
  `menu_sources.py` (live discovery via `brave_search.py` + extraction2)

`scripts/extract_menu_evidence_gemini.py` is **kept** (not v1 orchestration; it
reads a generic CSV). Its README workflow referenced the deleted CLI as an input
producer — update that prose so the docs stay honest, but leave the script.

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

Rename safety: `tests/test_diet_sort_tiebreak.py` extracts a `@sort-core`
sentinel block from `app_template.html`. The green template already contains an
equivalent `@sort-core:start … @sort-core:end` block, so after the rename this
test exercises green's sort logic and should stay green. `.impeccable/live/
config.json` and `.claude/settings.local.json` already point at
`app_template.html`, so the rename keeps them valid with no edits.

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
