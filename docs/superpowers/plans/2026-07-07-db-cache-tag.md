# Database Cache Tag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a quiet chip in the restaurant drawer reporting whether this restaurant's extraction was served from / saved to the Postgres cache (or fell back to disk).

**Architecture:** `cache_store` gains `load_with_origin()` and `save()` starts returning the backend that took the write; `discover.py` stamps `cache_origin`/`cache_saved_to` onto `MenuExtractionResult`; `menu_service` threads a `cache_info` dict into the drawer response as `"cache"`; `app_template.html` renders one `.pvchip`-style chip from it.

**Tech Stack:** Python 3.12, pytest; vanilla JS/CSS in the single-file app template.

**Spec:** `docs/superpowers/specs/2026-07-07-db-cache-tag-design.md`

## Global Constraints

- Truthfulness: origin/destination come only from `cache_store` (what actually happened), never inferred.
- Wording exactly: origin `postgres` → `From database`; origin `disk` → `From local cache`; savedTo `postgres` → `Saved to database`; savedTo `disk` → `Saved locally`. Origin wins if both present. No chip otherwise.
- Additive only: new dataclass fields default `None`; `save()`'s return value is ignored by existing callers; the `"cache"` response key is omitted when there is nothing to say — existing responses stay byte-identical.
- `load()` keeps returning a bare blob; `load_with_origin` must preserve current `load` semantics exactly (incl. non-dict PG payload → miss without disk fallthrough; PG error → disk).
- Tests: no live database (FakePool pattern already in `tests/test_cache_store.py`); run with `python -m pytest <file> -v`. Template JS has no test harness in this repo — Task 4 is verified by suite-green + the post-cutover manual check.
- Out of scope: community-signals tagging, search-card rendering, status pages.

---

### Task 1: `cache_store` origin reporting

**Files:**
- Modify: `safeplate/cache_store.py:46-77` (replace `load` and `save`, add `load_with_origin`)
- Test: `tests/test_cache_store.py` (append)

**Interfaces:**
- Consumes: existing module internals (`_get_pool`, `_disk_load`, `_disk_save`, `_pg_save`, `_warn`).
- Produces: `load_with_origin(namespace: str, key: str) -> tuple[dict | None, str | None]` (origin `"postgres"`/`"disk"`/`None`); `save(namespace: str, key: str, blob: dict) -> str` (returns `"postgres"` or `"disk"`); `load()` unchanged signature.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cache_store.py`:

```python
def test_load_with_origin_disk_mode(tmp_path):
    cache_store.save("diet_llm", "og1", {"at": 1.0})
    assert cache_store.load_with_origin("diet_llm", "og1") == ({"at": 1.0}, "disk")
    assert cache_store.load_with_origin("diet_llm", "missing") == (None, None)


def test_save_returns_disk_in_disk_mode():
    assert cache_store.save("diet_llm", "og2", {"at": 1.0}) == "disk"


def test_load_still_returns_bare_blob():
    cache_store.save("diet_llm", "og3", {"at": 2.0})
    assert cache_store.load("diet_llm", "og3") == {"at": 2.0}


def test_load_with_origin_pg_hit(monkeypatch, tmp_path):
    pool = FakePool()
    pool.rows[("extraction2_result", "og4")] = {"at": 1.0, "items": []}
    _use_fake_pool(monkeypatch, pool)
    assert cache_store.load_with_origin("extraction2_result", "og4") == (
        {"at": 1.0, "items": []}, "postgres"
    )


def test_load_with_origin_pg_miss_reports_disk_and_promotes(monkeypatch, tmp_path):
    pool = FakePool()
    _use_fake_pool(monkeypatch, pool)
    cache_store._disk_save("extraction2_result", "og5", {"at": 3.0})
    assert cache_store.load_with_origin("extraction2_result", "og5") == ({"at": 3.0}, "disk")
    assert pool.rows[("extraction2_result", "og5")] == {"at": 3.0}


def test_load_with_origin_pg_error_reports_disk(monkeypatch, tmp_path):
    pool = FakePool(fail=True)
    _use_fake_pool(monkeypatch, pool)
    cache_store._disk_save("extraction2_result", "og6", {"at": 4.0})
    assert cache_store.load_with_origin("extraction2_result", "og6") == ({"at": 4.0}, "disk")


def test_save_returns_postgres_on_pg_write(monkeypatch, tmp_path):
    pool = FakePool()
    _use_fake_pool(monkeypatch, pool)
    assert cache_store.save("extraction2_result", "og7", {"at": 5.0}) == "postgres"
    assert not list(tmp_path.rglob("*.json"))


def test_save_returns_disk_on_pg_error(monkeypatch, tmp_path):
    pool = FakePool(fail=True)
    _use_fake_pool(monkeypatch, pool)
    assert cache_store.save("extraction2_result", "og8", {"at": 6.0}) == "disk"
    assert cache_store._disk_load("extraction2_result", "og8") == {"at": 6.0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cache_store.py -v -k origin`
Expected: FAIL with `AttributeError: ... has no attribute 'load_with_origin'` (and the two save-return tests fail on `None != "disk"` / `None != "postgres"`; run those with `-k save_returns`).

- [ ] **Step 3: Implement** — in `safeplate/cache_store.py`, replace the current `load` and `save` (lines 46–77) with:

```python
def load(namespace: str, key: str) -> dict[str, Any] | None:
    """Cached blob or None -- provenance-free wrapper over load_with_origin."""
    return load_with_origin(namespace, key)[0]


def load_with_origin(namespace: str, key: str) -> tuple[dict[str, Any] | None, str | None]:
    """(blob, origin) where origin is "postgres", "disk", or None on a miss.
    Postgres first (when configured); a Postgres MISS falls through to disk --
    reported as "disk" (the promotion into Postgres is a side effect) -- and a
    Postgres ERROR degrades to disk."""
    pool = _get_pool()
    if pool is not None:
        try:
            with pool.connection() as conn:
                row = conn.execute(
                    "SELECT payload FROM cache_entries WHERE namespace = %s AND key = %s",
                    (namespace, key),
                ).fetchone()
        except Exception as exc:
            _warn(f"cache DB read failed ({exc!r}); serving from disk")
        else:
            if row is not None:
                blob = row[0]
                return (blob, "postgres") if isinstance(blob, dict) else (None, None)
            blob = _disk_load(namespace, key)
            if blob is not None:
                _pg_save(pool, namespace, key, blob)  # promote warm file entry
                return blob, "disk"
            return None, None
    blob = _disk_load(namespace, key)
    return blob, ("disk" if blob is not None else None)


def save(namespace: str, key: str, blob: dict[str, Any]) -> str:
    """Upsert into Postgres when configured; disk otherwise. A Postgres error
    writes to disk instead, so a paid result is never lost. Returns the backend
    that actually took the write ("postgres" or "disk")."""
    pool = _get_pool()
    if pool is not None and _pg_save(pool, namespace, key, blob):
        return "postgres"
    _disk_save(namespace, key, blob)
    return "disk"
```

- [ ] **Step 4: Run the file's tests**

Run: `python -m pytest tests/test_cache_store.py -v`
Expected: all previous tests + 8 new PASS (1 skip: live test).

- [ ] **Step 5: Commit**

```bash
git add safeplate/cache_store.py tests/test_cache_store.py
git commit -m "feat(cache): load_with_origin + save reports destination backend"
```

---

### Task 2: Stamp provenance on the extraction result

**Files:**
- Modify: `safeplate/extraction2/schema.py:88-100` (two fields on `MenuExtractionResult`)
- Modify: `safeplate/extraction2/discover.py:494-539` (`_load_result_cache`, `_save_result_cache`)
- Test: `tests/test_cache_store_call_sites.py` (append)

**Interfaces:**
- Consumes: Task 1's `cache_store.load_with_origin` and `save() -> str`.
- Produces: `MenuExtractionResult.cache_origin: str | None` and `.cache_saved_to: str | None` (both default `None`); `_save_result_cache(...)` now stamps `result.cache_saved_to` itself and returns the destination.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cache_store_call_sites.py`:

```python
def test_result_cache_hit_stamps_origin(monkeypatch):
    from safeplate.extraction2 import discover

    blob = {"at": time.time(), "items": [], "coverage": [], "signals": [], "diet_signals": []}
    monkeypatch.setattr(
        discover.cache_store, "load_with_origin",
        lambda ns, key: (blob, "postgres") if ns == "extraction2_result" else (None, None),
    )
    result = discover._load_result_cache("https://tag.example", "m")
    assert result is not None
    assert result.cache_origin == "postgres"


def test_result_cache_save_stamps_destination(monkeypatch):
    from safeplate.extraction2 import discover
    from safeplate.extraction2.schema import MenuExtractionResult

    monkeypatch.setattr(discover.cache_store, "save", lambda ns, key, blob: "postgres")
    result = MenuExtractionResult(items=[], coverage=[])
    assert result.cache_saved_to is None
    discover._save_result_cache("https://tag.example", "m", result)
    assert result.cache_saved_to == "postgres"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cache_store_call_sites.py -v -k stamps`
Expected: 2 FAILED — the first because `_load_result_cache` calls `cache_store.load` (patched `load_with_origin` unused ⇒ real load returns None ⇒ result is None); the second with `AttributeError: ... no attribute 'cache_saved_to'`.

- [ ] **Step 3: Add the schema fields** — in `safeplate/extraction2/schema.py`, inside `MenuExtractionResult` after `incomplete: bool = False`:

```python
    # Cache-store provenance for the drawer's verification chip (never persisted
    # in the cached blob): where a cached result was loaded from, and which
    # backend took the fresh-result write. "postgres" | "disk" | None.
    cache_origin: str | None = None
    cache_saved_to: str | None = None
```

- [ ] **Step 4: Stamp in `discover.py`** — in `_load_result_cache`, replace

```python
    blob = cache_store.load(
        "extraction2_result", _result_cache_key(website_url, model, discriminator)
    )
    if blob is None:
        return None
```

with

```python
    blob, origin = cache_store.load_with_origin(
        "extraction2_result", _result_cache_key(website_url, model, discriminator)
    )
    if blob is None:
        return None
```

and add `cache_origin=origin,` to the `MenuExtractionResult(...)` constructor call right after `llm_calls=0,`.

Then replace `_save_result_cache` (whole function) with:

```python
def _save_result_cache(website_url: str, model: str, result, discriminator: str = "") -> None:
    from dataclasses import asdict

    result.cache_saved_to = cache_store.save(
        "extraction2_result",
        _result_cache_key(website_url, model, discriminator),
        {
            "at": time.time(),
            "items": [asdict(i) for i in result.items],
            "coverage": [asdict(c) for c in result.coverage],
            "signals": [asdict(s) for s in result.allergy_signals],
            "diet_signals": [asdict(s) for s in result.diet_signals],
        },
    )
```

(The stamp lives on the in-memory result only — `asdict` runs on the blob dict before the stamp and the stamp fields are not in the blob, so cached bytes are unchanged.)

- [ ] **Step 5: Run the routing tests + full suite**

Run: `python -m pytest tests/test_cache_store_call_sites.py -v` — expected: all PASS (8).
Run: `python -m pytest -q` — expected: fully green (fields are default-None additive).

- [ ] **Step 6: Commit**

```bash
git add safeplate/extraction2/schema.py safeplate/extraction2/discover.py tests/test_cache_store_call_sites.py
git commit -m "feat(extraction2): stamp cache origin/destination on extraction results"
```

---

### Task 3: Thread `cache` into the drawer response

**Files:**
- Modify: `safeplate/menu_service.py:51-134` (`_extract_and_assess_structured` returns 7-tuple), `:257-360` (`_structured_menu_response` gains `cache_info`), `:398` and `:684` (unpack sites), `:490-502` and `:584-625` (pass-through)
- Test: `tests/test_menu_service_cache_tag.py` (new)

**Interfaces:**
- Consumes: Task 2's `result.cache_origin` / `result.cache_saved_to`.
- Produces: `_extract_and_assess_structured` returns `(assessment, menu_items, allergy_signals, coverage, errors, diet_signals, cache_info)` where `cache_info` is `{"origin": str|None, "savedTo": str|None} | None`; `_structured_menu_response(..., cache_info=None)` adds `"cache": cache_info` to its return dict only when `cache_info` is truthy; `_write_assessment_into_card(..., cache_info=None)` forwards it into the embedded `menuDetail`.

- [ ] **Step 1: Write the failing test** — create `tests/test_menu_service_cache_tag.py`:

```python
"""The drawer response carries cache provenance only when there is something to say."""
from types import SimpleNamespace


def _minimal_response(cache_info):
    from safeplate.menu_service import _structured_menu_response

    assessment = SimpleNamespace(
        overall_risk=0.2, overall_confidence=0.5, evidence_basis="cuisine_prior",
        rationale="r", tier="T5", per_allergen=[], handling=SimpleNamespace(
            allergy_aware=False, cross_contact_warning=False, ask_staff=False,
            nut_free_claim=False,
        ),
        evidence=[],
    )
    return _structured_menu_response(
        restaurant_name="Tag Test", website_url="https://t.example", address="",
        assessment=assessment, menu_items=[], allergy_signals=[], coverage=[],
        errors=[], scoring_engine="rules", personalized=False, diets=None,
        cache_info=cache_info,
    )


def test_cache_key_present_when_origin_set():
    resp = _minimal_response({"origin": "postgres", "savedTo": None})
    assert resp["cache"] == {"origin": "postgres", "savedTo": None}


def test_cache_key_absent_when_none():
    assert "cache" not in _minimal_response(None)
```

NOTE: before finalizing this test, read `_structured_menu_response`'s actual signature at `safeplate/menu_service.py:257` and mirror its required keyword arguments exactly — the SimpleNamespace above must satisfy every attribute the function touches (run the test; any AttributeError names the next attribute to add). If constructing the assessment stub proves brittle (more than ~10 attributes), fall back to calling the real `assess_restaurant_record` on an empty record to get a genuine assessment object, and assert only on the `"cache"` key behavior.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_menu_service_cache_tag.py -v`
Expected: FAIL with `TypeError: _structured_menu_response() got an unexpected keyword argument 'cache_info'`.

- [ ] **Step 3: Implement the threading** — four edits in `safeplate/menu_service.py`:

(a) `_extract_and_assess_structured`: after the `discover_and_extract` try/except (where `result` is in scope on success), capture provenance; initialize `cache_info = None` next to `errors: list = []` at the top, and inside the successful branch after `diet_signals = ...` add:

```python
            origin = getattr(result, "cache_origin", None)
            saved_to = getattr(result, "cache_saved_to", None)
            if origin or saved_to:
                cache_info = {"origin": origin, "savedTo": saved_to}
```

Append `cache_info` to the function's final return tuple (locate the `return assessment, ...` at the end of the function and add `, cache_info`). Update the docstring's "Returns (...)" line to include it.

(b) Both unpack sites gain the new element:
- line ~398: `assessment, menu_items, allergy_signals, coverage, errors, diet_signals, cache_info = _extract_and_assess_structured(`
- line ~684: same seven-name unpack (the search-list batch path; it forwards `cache_info` to `_write_assessment_into_card` below).

(c) `_structured_menu_response`: add keyword parameter `cache_info: dict[str, Any] | None = None`; convert the tail so the literal `return {...}` at line ~347 becomes `response = {...}` followed by:

```python
    if cache_info and (cache_info.get("origin") or cache_info.get("savedTo")):
        # Drawer verification chip: where this restaurant's cached extraction
        # came from / where the fresh result was written. Omitted otherwise so
        # untagged responses stay byte-identical.
        response["cache"] = cache_info
    return response
```

(d) Pass-throughs: at line ~490 add `cache_info=cache_info,` to the `_structured_menu_response(` call; `_write_assessment_into_card` gains parameter `cache_info: dict[str, Any] | None = None` and adds `cache_info=cache_info,` to its embedded `_structured_menu_response(` call at line ~619; its caller in the batch path (near the line-684 unpack) passes `cache_info=cache_info`.

- [ ] **Step 4: Run the new test + full suite**

Run: `python -m pytest tests/test_menu_service_cache_tag.py -v` — expected: 2 PASS.
Run: `python -m pytest -q` — expected: fully green.

- [ ] **Step 5: Commit**

```bash
git add safeplate/menu_service.py tests/test_menu_service_cache_tag.py
git commit -m "feat(api): drawer response carries cache provenance"
```

---

### Task 4: Drawer chip

**Files:**
- Modify: `safeplate/app_template.html:388` area (one CSS rule), `:1679` area (helper next to `pvchip()`), `:2113-2126` (`verdictHtml` signature + prov row), `:2311` (pass `m.cache`)

**Interfaces:**
- Consumes: Task 3's `"cache"` key on the /api/menu response (`m.cache` inside `renderMenu(m, r)`).
- Produces: UI only — no new JS API.

- [ ] **Step 1: CSS** — after the `.pvchip::before` rule (line ~387), add:

```css
    .pvchip.pv-cache { color: var(--tx3); }  /* operator's note: quietest ink */
```

- [ ] **Step 2: Helper** — next to `function pvchip(t)` (line ~1679), add:

```js
function cacheChipHtml(c) {
  /* Cache-provenance verification chip (see docs/superpowers/specs/2026-07-07-db-cache-tag-design.md).
     Origin wins over savedTo: a cache hit never also saves. Silent when absent. */
  if (!c) return "";
  const label = c.origin === "postgres" ? "From database"
              : c.origin === "disk"     ? "From local cache"
              : c.savedTo === "postgres" ? "Saved to database"
              : c.savedTo === "disk"     ? "Saved locally" : "";
  return label ? `<span class="pvchip pv-cache">${label}</span>` : "";
}
```

- [ ] **Step 3: Render** — change line 2113 to `function verdictHtml(risk, coverage, basis, perAllergen, cache) {`, and inside it change the prov row (line ~2126) to:

```js
      <div class="verdict-prov">${pvchip(prov)}<span class="pv-blurb">${esc(prov.blurb)}</span>${personalChip}${cacheChipHtml(cache)}</div>
```

Then at line ~2311 pass the response's field:

```js
    if (v) v.outerHTML = verdictHtml(menuRisk,r.coverageStatus,summary.evidenceBasis,summary.perAllergen,m.cache);
```

(The initial drawer render at line ~2253 intentionally passes nothing — the chip appears when the menu response lands, which is when provenance is known.)

- [ ] **Step 4: Verify**

Run: `python -m pytest -q` — expected: fully green (template is served verbatim; demo-mode tests don't emit `cache` so nothing changes for them).
Manual check documented for the user (not executable here without API keys): run the app locally, open a restaurant drawer twice — first open shows `Saved locally` (disk mode on the dev machine), second shows `From local cache`; on the EC2 with `DATABASE_URL` set, the same two opens show `Saved to database` / `From database`.

- [ ] **Step 5: Commit**

```bash
git add safeplate/app_template.html
git commit -m "feat(ui): drawer chip showing database/local cache provenance"
```
