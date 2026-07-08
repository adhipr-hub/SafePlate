# Remove v1 extraction & collapse to a single UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `extraction2` the sole menu-extraction engine and the green template the sole UI, removing all v1 extraction code + tooling and the classic/orphan templates + theme toggle.

**Architecture:** Two independent workstreams. Part 1 (UI) renames the green template to `app_template.html`, strips the theme toggle, and collapses the server's per-theme machinery to a single page. Part 2 (extraction) deletes the v1 `*_from_sources` orchestration in `menu_text.py` and its exclusive consumers (a CLI + two eval harnesses), repoints the worldwide benchmark to v2, and prunes now-orphaned helpers — keeping every primitive that live/v2 code still imports.

**Tech Stack:** Python 3 stdlib `http.server`, BeautifulSoup/lxml, pytest, PyMuPDF/pypdf. No new dependencies.

## Global Constraints

- **Do not change scoring.** The `rules` vs `ai` scoring engines both stay. "v1/v2" = extraction only.
- **Preserve these shared symbols in `safeplate/menu_text.py`** (live/v2 consumers import them; deleting any breaks the app): `MenuItemRecord`, `MenuTextRecord`, `ALLERGEN_TERMS`, `_matched_terms`, `_matched_terms_in`, `_term_present`, `_enclosing_word`, `_dietary_and_allergen_terms`, `_pdf_text_from_bytes`, `_pdf_text_from_bytes_inner`, `_extract_schema_org_menu_items_from_soup` and its `_schema_*`/`_microdata_*` helper web, `_looks_like_item_name`, `_classlist_text`, `_clean_text`, `read_csv_rows`.
- **Do not touch** `safeplate/menu_sources.py`, `safeplate/embedded_json.py`, `safeplate/allergen_matrix.py`, or `scripts/extract_menu_evidence_gemini.py` (kept — not v1 orchestration).
- **Reachability rule for every deletion:** delete a function only after `git grep -nE "\bNAME\b" -- safeplate eval tests scripts` shows zero non-`def` references. If anything still calls it, keep it.
- **Safety net after every task:** `python -m pytest -q` must pass. The served "/" page and menu-scoring results must not change behavior.
- Branch already exists: `remove-v1-extraction-single-ui`. Commit after every task.
- Line numbers below are anchors from the current tree; if drifted, locate by the quoted content/grep, not the raw number.

---

## Part 1 — UI: green becomes the only design

### Task 1: Rename green → `app_template.html`, delete classic + orphans, strip the theme toggle

**Files:**
- Delete: `safeplate/app_template.html` (classic), `safeplate/app_template_alt.html`, `safeplate/app_template_alt2.html`
- Rename: `safeplate/app_template_green.html` → `safeplate/app_template.html`
- Modify: `safeplate/app_template.html` (the renamed file — remove toggle markup + CSS)

**Interfaces:**
- Produces: a single `safeplate/app_template.html` with no `.theme-switch` / `.nav-theme` toggle and no `/?theme=` links. Retains the `@sort-core:start … @sort-core:end` block unchanged (consumed by `tests/test_diet_sort_tiebreak.py`).

- [ ] **Step 1: Delete classic + orphan templates and rename green (preserves git history)**

```bash
git rm safeplate/app_template.html safeplate/app_template_alt.html safeplate/app_template_alt2.html
git mv safeplate/app_template_green.html safeplate/app_template.html
```

- [ ] **Step 2: Remove the two toggle markup blocks from `safeplate/app_template.html`**

Delete the `nav-theme` block (the whole `<div class="nav-theme" …>…</div>` including its `<!-- On small screens … -->` comment) and the `theme-switch` block (the whole `<div class="theme-switch" …>…</div>`):

```html
      <!-- On small screens the bar can't fit the theme switch; it lives here instead. -->
      <div class="nav-theme" role="group" aria-label="Design version">
        <span class="ts-lab" aria-hidden="true">View</span>
        <a class="ts-opt" href="/?theme=classic">Classic</a>
        <a class="ts-opt is-on" href="/?theme=green" aria-current="true">New design</a>
      </div>
```

```html
    <div class="theme-switch" role="group" aria-label="Design version">
      <span class="ts-lab" aria-hidden="true">View</span>
      <a class="ts-opt" href="/?theme=classic">Classic</a>
      <a class="ts-opt is-on" href="/?theme=green" aria-current="true">New design</a>
    </div>
```

- [ ] **Step 3: Remove the toggle CSS from `safeplate/app_template.html`**

Delete the CSS rules for the toggle. There are two regions:
1. The block starting `/* Theme switch -- flip between the classic and green skins (backend-served) */` through the `@media (max-width: 760px)` block that ends with the `.theme-switch .ts-opt { … min-height: 44px … }` rule — i.e. the rules `.theme-switch`, `.ts-lab`, `.ts-opt`, `.ts-opt:hover`, `.ts-opt.is-on`, `.nav-theme { display: none; }`, and the 760px media block's `.theme-switch` / `.nav-theme` lines.
2. The responsive block further down (around the `@media (max-width: 680px)` area) that references `.theme-switch { display: none; }` and `.nav-theme` / `.nav-links.open .nav-theme` / `.nav-theme .ts-opt`.

Find every remaining occurrence to remove with:

```bash
git grep -nE "theme-switch|nav-theme|ts-opt|ts-lab|\?theme=" -- safeplate/app_template.html
```

Remove each matched CSS rule and any now-empty media query. Re-run the grep; expected: **no output**.

- [ ] **Step 4: Verify no toggle/theme remnants remain in the template**

Run:
```bash
git grep -nE "theme-switch|nav-theme|ts-opt|ts-lab|\?theme=|Design version" -- safeplate/app_template.html
```
Expected: no output. And confirm the sort-core sentinel survived:
```bash
git grep -nE "@sort-core:(start|end)" -- safeplate/app_template.html
```
Expected: two lines (start + end).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(ui): make the green template the sole app_template.html; drop theme toggle"
```

---

### Task 2: Collapse the server's theme machinery to a single page

**Files:**
- Modify: `safeplate/api_server.py` (the `do_GET("/")` branch; `app_html`; `_APP_TEMPLATE_PATHS`/`_app_html_cache`)
- Test: `tests/test_api_server_theme.py` (new)

**Interfaces:**
- Consumes: `safeplate/app_template.html` (single template from Task 1).
- Produces: `app_html() -> str` (no `theme` parameter). `GET /` always serves it; no `?theme=` parse, no `sp_theme` cookie read/write.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_server_theme.py`:

```python
from safeplate import api_server


def test_app_html_takes_no_theme_and_has_no_toggle():
    html = api_server.app_html()  # must accept zero args now
    assert "@sort-core:start" in html
    assert "theme-switch" not in html
    assert "?theme=" not in html


def test_no_theme_cookie_or_param_machinery():
    import inspect
    src = inspect.getsource(api_server)
    assert "sp_theme" not in src
    assert "_APP_TEMPLATE_PATHS" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_server_theme.py -q`
Expected: FAIL — `app_html()` currently requires no arg but `sp_theme`/`_APP_TEMPLATE_PATHS` still exist, so `test_no_theme_cookie_or_param_machinery` fails.

- [ ] **Step 3: Replace the per-theme template structure**

In `safeplate/api_server.py`, replace the two-skin block (the comment "Two skins share the same JS/DOM contract…", `_APP_TEMPLATE_PATHS`, and `_app_html_cache`) with a single template + single-slot cache:

```python
# The app page. Served from "/" for every request. Re-read on mtime change so
# edits show on a plain browser refresh without a server restart.
_APP_TEMPLATE_PATH = Path(__file__).resolve().parent / "app_template.html"
_app_html_cache: dict[str, Any] = {"mtime": None, "html": ""}
_app_html_lock = threading.Lock()
```

- [ ] **Step 4: Collapse `app_html`**

Replace the `def app_html(theme: str = "classic") -> str:` function body with:

```python
def app_html() -> str:
    """Serve the single app template, re-reading it when the file changes so edits
    show on a plain browser refresh -- no server restart needed. Only re-reads when
    the file's mtime changes (a cheap stat per request); on a transient read error
    (e.g. the file caught mid-save) it keeps serving the last good copy. The lock
    makes the stat/read/return atomic under ThreadingHTTPServer."""
    with _app_html_lock:
        try:
            mtime = _APP_TEMPLATE_PATH.stat().st_mtime
            if mtime != _app_html_cache["mtime"]:
                _app_html_cache["html"] = _APP_TEMPLATE_PATH.read_text(encoding="utf-8")
                _app_html_cache["mtime"] = mtime
        except OSError:
            pass  # keep serving the last good copy
        return _app_html_cache["html"]
```

- [ ] **Step 5: Simplify the `GET /` handler**

In `do_GET`, replace the `if path == "/":` block (the `?theme=` parse, `sp_theme` cookie read, `set_cookie` computation, and `self._send_html(app_html(theme), set_cookie=set_cookie)`) with:

```python
            if path == "/":
                self._send_html(app_html())
                return
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_server_theme.py -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Confirm nothing else references the removed theme machinery**

Run: `git grep -nE "app_html\(|sp_theme|_APP_TEMPLATE_PATHS|\?theme=" -- safeplate tests`
Expected: only the new single-arg `app_html()` call sites (and this plan's own strings if grepped in docs). No `sp_theme`, `_APP_TEMPLATE_PATHS`, or `?theme=` in `safeplate/`.

- [ ] **Step 8: Full suite + commit**

Run: `python -m pytest -q`
Expected: PASS (including `tests/test_diet_sort_tiebreak.py`, now reading green's sort-core block).

```bash
git add -A
git commit -m "feat(ui): serve one app template; remove theme param, cookie, and per-skin cache"
```

---

## Part 2 — Extraction: v2 becomes the only engine

### Task 3: Delete the v1-exclusive external consumers (CLI + eval harnesses) and fix docs

**Files:**
- Delete: `scripts/extract_menu_text.py`, `eval/bench_cities.py`, `eval/compare_engines.py`
- Modify: `README.md` (remove the `extract_menu_text.py` pipeline sections)

**Interfaces:**
- Produces: no code outside `menu_text.py` calls the v1 `*_from_sources` API after this task (except `eval/bench_extraction.py`, handled in Task 4). Enables Task 6's orchestration deletion.

- [ ] **Step 1: Confirm these three files are the v1-API consumers (besides bench_extraction)**

Run: `git grep -nE "extract_menu_text_from_sources|extract_menu_items_from_sources|extract_menu_from_sources" -- safeplate eval tests scripts | grep -v "menu_text.py:"`
Expected: matches only in `eval/bench_cities.py`, `scripts/extract_menu_text.py` (and none in tests). If anything else appears, stop and reassess.

- [ ] **Step 2: Delete the three files**

```bash
git rm scripts/extract_menu_text.py eval/bench_cities.py eval/compare_engines.py
```

- [ ] **Step 3: Remove the `extract_menu_text.py` sections from `README.md`**

Find the references and remove the surrounding prose/code blocks that document the v1 CLI pipeline:

```bash
git grep -nE "extract_menu_text\.py|extract_menu_items_from_sources|bench_cities" -- README.md
```

For each hit (~lines 326, 376, 410, 454): delete the code block / list item / sentence that instructs running `extract_menu_text.py` or describes it as the "backbone". Where `scripts/extract_menu_evidence_gemini.py` is documented as consuming that CLI's CSV, reword it to say its input CSV must supply the documented columns (the CLI that produced it has been removed) rather than deleting the evidence-script docs. Re-run the grep; expected: only any remaining mention is the reworded evidence-script note, with no instruction to run the deleted CLI.

- [ ] **Step 4: Verify the deletions import-clean**

Run: `python -c "import safeplate.menu_text, safeplate.api_server, safeplate.menu_service"`
Expected: no error (the deleted files were entrypoints, not imported by the package).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore(extraction): delete v1 CLI + city/compare eval harnesses; update README"
```

---

### Task 4: Repoint `eval/bench_extraction.py` to the v2 engine

**Files:**
- Modify: `eval/bench_extraction.py` (imports + `extract_for_snapshot`)

**Interfaces:**
- Consumes: `safeplate.extraction2.extract_menu`, `safeplate.extraction2.Policy`, `safeplate.extraction2.acquire.payload_from_html`, `payload_from_pdf_text`.
- Produces: `extract_for_snapshot(entry) -> list[MenuItemRecord]` running v2 over the frozen snapshot, keeping the same return type the rest of `bench()` consumes (`.extraction_method`, `.item_name`, `.price`).

- [ ] **Step 1: Replace the v1 imports**

In `eval/bench_extraction.py`, remove the v1 extractor imports:

```python
from safeplate.menu_text import (
    _extract_menu_items_from_html,
    _extract_menu_items_from_text,
    _extract_schema_org_menu_items_from_html,
)
```

and the now-unused `from safeplate.embedded_json import extract_items_from_embedded_json` (only if grep shows it is unused after Step 2). Add:

```python
from safeplate.extraction2 import Policy, extract_menu
from safeplate.extraction2.acquire import payload_from_html, payload_from_pdf_text
```

Keep `from safeplate.menu_sources import discover_menu_sources_for_url, MenuSourceError` (used by `--collect`).

- [ ] **Step 2: Rewrite `extract_for_snapshot` to run v2 offline (no LLM)**

```python
def extract_for_snapshot(entry: dict) -> list:
    """Run the v2 structured engine over one frozen snapshot (offline, no LLM),
    so each iteration is directly comparable and costs nothing."""
    text = (SNAP_DIR / entry["file"]).read_text(encoding="utf-8")
    url = entry.get("url", "")
    if entry["file"].endswith(".pdf.txt"):
        payload = payload_from_pdf_text(url, text)
    else:
        payload = payload_from_html(url, text)
    result = extract_menu([payload], policy=Policy.CHEAP, llm_enabled=False)
    return result.items
```

Note: confirm the `Policy` member name by reading `safeplate/extraction2/schema.py` (use whatever the cheap/deterministic policy is actually called; `compare_engines.py` passed a `policy=` built the same way before deletion — if unsure, `python -c "from safeplate.extraction2 import Policy; print(list(Policy))"`).

- [ ] **Step 3: Run the benchmark against existing snapshots (if present)**

Run: `python eval/bench_extraction.py`
Expected: either prints the per-city stats table (snapshots present) or the "No snapshots" message — **not** an ImportError or TypeError. If snapshots are absent, run `python -c "import eval.bench_extraction"` is not importable as a module; instead verify with:
`python -c "import ast,sys; ast.parse(open('eval/bench_extraction.py').read()); print('parse-ok')"`
Expected: `parse-ok`, and a manual read confirms no remaining reference to the deleted v1 functions.

- [ ] **Step 4: Confirm no v1 extractor references remain in the file**

Run: `git grep -nE "_extract_menu_items_from_html|_extract_menu_items_from_text|_extract_schema_org_menu_items_from_html" -- eval/bench_extraction.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add eval/bench_extraction.py
git commit -m "test(eval): run bench_extraction on the v2 engine over frozen snapshots"
```

---

### Task 5: Remove v1-specific cases from `tests/test_menu_text.py`

**Files:**
- Modify: `tests/test_menu_text.py`

**Interfaces:**
- Produces: a test module that imports no soon-to-be-deleted v1 extractor, while retaining coverage of surviving primitives (`_matched_terms`, `ALLERGEN_TERMS`, etc.).

- [ ] **Step 1: Identify the v1 cases**

Run: `git grep -nE "_extract_menu_items_from_html|_extract_menu_items_from_text|_extract_schema_org_menu_items_from_html" -- tests/test_menu_text.py`
Expected: the import line (~8-10) and the test methods at ~62, 78, 91, 106, 113, 142, 178, 216, 230.

- [ ] **Step 2: Delete those imports and the test methods that call them**

Remove the three names from the `from safeplate.menu_text import (...)` block and delete each test method whose body calls one of them. Keep any test that only touches surviving symbols (e.g. the `_matched_terms("Classic eggnog", ALLERGEN_TERMS)` case at ~262). If a whole `class` becomes empty, delete the class.

- [ ] **Step 3: Run the trimmed module**

Run: `python -m pytest tests/test_menu_text.py -q`
Expected: PASS, with the surviving primitive tests still collected (not zero tests).

- [ ] **Step 4: Confirm the file no longer imports the v1 extractors**

Run: `git grep -nE "_extract_menu_items_from_html|_extract_menu_items_from_text|_extract_schema_org_menu_items_from_html" -- tests/test_menu_text.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add tests/test_menu_text.py
git commit -m "test: drop v1 prose-extractor cases from test_menu_text"
```

---

### Task 6: Delete the v1 orchestration entrypoints in `menu_text.py`

**Files:**
- Modify: `safeplate/menu_text.py`

**Interfaces:**
- Produces: `menu_text.py` with no `*_from_sources` API and no per-source dispatch. All symbols in Global Constraints' "preserve" list remain.

- [ ] **Step 1: Delete the top-level v1 API + per-source dispatch functions**

Delete these function definitions (whole bodies) from `safeplate/menu_text.py`:
`extract_menu_text_from_sources`, `extract_menu_items_from_sources`, `extract_menu_from_sources`, `_extract_source_once`, `_items_from_candidates`, `_build_text_record`, `_stage_workers`, `_eligible_source_rows`, `extract_text_for_menu_source`, `extract_visible_text`, `extract_pdf_text`, `_extract_pdf_items_from_bytes`, `_recover_html_items`, and the html-string wrappers `_extract_menu_items_from_html`, `_extract_schema_org_menu_items_from_html`.

Do **not** delete `_extract_schema_org_menu_items_from_soup`, `_pdf_text_from_bytes`, `_looks_like_item_name`, `_classlist_text`, `_clean_text`, `MenuTextRecord`, or any `_schema_*`/`_microdata_*` helper.

- [ ] **Step 2: Import-check the package**

Run: `python -c "import safeplate.menu_text, safeplate.api_server, safeplate.menu_service, safeplate.allergen_matrix; import safeplate.extraction2.interpret_structured, safeplate.extraction2.acquire; print('import-ok')"`
Expected: `import-ok`. If an `ImportError`/`NameError` names a still-referenced symbol, that symbol is shared — restore it and add it to the preserve list.

- [ ] **Step 3: Full suite**

Run: `python -m pytest -q`
Expected: PASS. A failure here points at a helper that was still needed — restore it.

- [ ] **Step 4: Commit**

```bash
git add safeplate/menu_text.py
git commit -m "refactor(extraction): remove v1 from_sources orchestration from menu_text"
```

---

### Task 7: Prune now-orphaned v1-only helpers and run the full quality gate

**Files:**
- Modify: `safeplate/menu_text.py`

**Interfaces:**
- Produces: `menu_text.py` free of dead v1-only helpers, with every preserved symbol intact and the offline quality gate green.

- [ ] **Step 1: Find candidate orphans**

For each helper that Task 6 may have orphaned — start with `_extract_menu_items_from_text`, `_records_from_price_lines`, `_price_segments`, `_price_matches`, `_price_count`, `_is_plausible_bare_price`, `_dedupe_item_key`, `_dedupe_text`, `_dedupe_price`, `_extract_menu_items_from_soup`, `_listed_items_from_soup`, `_container_item_name`, `_has_menu_ancestor`, `_in_navigation`, `_build_html_item_record`, `build_menu_text_output_paths`, `write_menu_text_csv`, `write_menu_text_json`, `_join_terms_transform`, `_should_extract_row`, `_visible_lines_from_soup`, `_price_text_blocks_from_soup`, `_looks_like_category`, `_split_item_name_and_description`, `_title_prefix_word_count`, `_looks_like_title_word`, `_is_negative_item_text`, `_item_confidence`, `write_menu_items_csv`, `write_menu_items_json`, `build_menu_item_output_paths` — check for callers:

```bash
for f in _extract_menu_items_from_text _records_from_price_lines _price_segments _price_matches _price_count _is_plausible_bare_price _extract_menu_items_from_soup _listed_items_from_soup _container_item_name _has_menu_ancestor _in_navigation _build_html_item_record build_menu_text_output_paths write_menu_text_csv write_menu_text_json _should_extract_row _visible_lines_from_soup _price_text_blocks_from_soup _looks_like_category _split_item_name_and_description _title_prefix_word_count _looks_like_title_word _is_negative_item_text _item_confidence; do
  n=$(git grep -nE "\\b$f\\b" -- safeplate eval tests scripts | grep -vE "def $f\\b" | wc -l);
  echo "$n  $f";
done
```

Any line showing `0` is orphaned and safe to delete. **Iterate:** deleting one orphan may orphan another, so re-run this loop after each removal round until every surviving helper has ≥1 caller.

- [ ] **Step 2: Delete confirmed-orphan functions**

Remove each function whose count is `0`. After each deletion round, re-run Step 1's loop. Never delete a helper still called by a preserved symbol (e.g. if `_looks_like_item_name` shows a caller from `allergen_matrix.py`, keep it and everything it transitively needs).

- [ ] **Step 3: Import-check + full suite after pruning**

Run:
```bash
python -c "import safeplate.menu_text, safeplate.api_server, safeplate.menu_service, safeplate.allergen_matrix, safeplate.extraction2; print('import-ok')"
python -m pytest -q
```
Expected: `import-ok` and all tests pass.

- [ ] **Step 4: Run the offline quality gate (prove v2 quality + scoring parity held)**

Run each (skip only if its required snapshot/fixture data is genuinely absent, and note that in the commit body):
```bash
python eval/bench_multi_allergen.py          # nut-parity vs committed baseline -> "NUT-PARITY OK"
python eval/safety_eval.py                    # safety metrics hold
python eval/bench_pipeline.py --label postv1  # v2 quality holds vs baseline
python eval/bench_extraction.py               # v2 benchmark runs (from Task 4)
```
Expected: `bench_multi_allergen.py` prints `NUT-PARITY OK`; the others complete without error and show no quality regression.

- [ ] **Step 5: Final repo-wide sweep for v1 remnants**

Run: `git grep -nE "app_template_green|app_template_alt|from_sources|compare_engines|bench_cities|extract_menu_text\.py|sp_theme|\?theme=" -- safeplate eval tests scripts README.md`
Expected: no output (docs/plans under `docs/` may still mention them — that's fine).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(extraction): prune orphaned v1 helpers; offline gate green"
```

---

## Self-Review Notes (author checklist, already applied)

- **Spec coverage:** Part 1 tasks 1-2 cover the whole UI section (rename, toggle strip, server collapse, orphan deletion, rename-safety test). Part 2 tasks 3-7 cover the extraction section (consumer deletion, README, bench repoint, v1 tests, orchestration deletion, helper prune, gate). ✅
- **Preserve list** is stated in Global Constraints and re-asserted in Tasks 6-7 so no shared symbol is deleted. ✅
- **Reachability** deletions are grep-gated (zero non-`def` callers) rather than guessed — the honest method for an intertwined module. ✅
- **Type consistency:** `app_html()` is zero-arg everywhere after Task 2; `extract_for_snapshot` keeps returning records with `.extraction_method/.item_name/.price`; `Policy` member confirmed at implementation time against `schema.py`. ✅
