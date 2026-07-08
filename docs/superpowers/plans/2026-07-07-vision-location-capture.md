# Vision Location Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Gemini-vision allergen-matrix read also transcribes visible location text (footer addresses, URLs, phone numbers, country names) so the existing deterministic detector can region-stamp image-only PDFs.

**Architecture:** The vision response schema gains an optional `visible_location_text` array (transcription only — the LLM never asserts a region). Snippets flow `menu_fetch_llm` → `interpret_pdf_matrix` (cached under a re-versioned key) → `_interpret_one`'s new sixth return element → the one coverage-build line that calls `detect_source_region`. Result-cache version bumps to "7" (user-decided cache clear).

**Tech Stack:** Python 3.12, pytest; Gemini vision via the existing `_post_gemini_generate_content` plumbing.

**Spec:** `docs/superpowers/specs/2026-07-07-vision-location-capture-design.md`

## Global Constraints

- The LLM only TRANSCRIBES visible text; the region verdict comes exclusively from the unchanged `extraction2.region.detect_source_region`.
- Snippets: strings only, stripped, deduped, max 8, each truncated to 120 chars. They must never enter menu items, allergy/diet signals, or grounding.
- Missing/empty `visible_location_text` (including old cache blobs) → behavior byte-identical to today (region stamp computed from `payload.text` + URL exactly as before).
- Cache clear mechanism exactly: `_RESULT_CACHE_VERSION` `"6"` → `"7"` (with changelog comment, matching the file's convention) and pdfmatrix key prefix `b"pdfmatrix:"` → `b"pdfmatrix2:"`.
- Sanctioned pre-existing-test updates (return-type widening ONLY — assertions keep their meaning): `tests/test_menu_fallbacks.py:118` (expects `[]`, becomes `([], [])`) and `tests/test_matrix_vision_pages.py` (unpacks the widened return). Anything beyond mechanical widening → stop and escalate.
- Run tests with `python -m pytest <file> -v`; full suite baseline 752 passed, 1 skipped, 11 subtests.

---

### Task 1: Vision call returns location snippets (`menu_fetch_llm.py`)

**Files:**
- Modify: `safeplate/menu_fetch_llm.py` (`ALLERGEN_MATRIX_SCHEMA` ~144, `ALLERGEN_MATRIX_SYSTEM` ~173, `_matrix_call` ~277, `_render_matrix_pages` ~240, `extract_allergen_matrix_via_gemini_pdf` ~202)
- Modify: `safeplate/menu_text.py:631` (v1 caller widens its unpack)
- Modify: `tests/test_menu_fallbacks.py:118`, `tests/test_matrix_vision_pages.py` (sanctioned widening)
- Test: `tests/test_matrix_vision_location.py` (new)

**Interfaces:**
- Produces: `extract_allergen_matrix_via_gemini_pdf(...) -> tuple[list[MenuItemRecord], list[str]]` — `(items, location_texts)`, both empty on any failure; `_sanitize_location_texts(values) -> list[str]` module helper. Task 2 consumes the tuple.

- [ ] **Step 1: Write the failing tests** — create `tests/test_matrix_vision_location.py`:

```python
"""Vision matrix read also transcribes visible location text (spec:
docs/superpowers/specs/2026-07-07-vision-location-capture-design.md)."""
import unittest
from unittest import mock

from safeplate import menu_fetch_llm


class SanitizeLocationTextsTests(unittest.TestCase):
    def test_caps_dedupes_and_cleans(self):
        raw = ["  12 Foo St, Sydney NSW  ", "", 42, "12 Foo St, Sydney NSW",
               "x" * 500] + [f"snippet {i}" for i in range(10)]
        out = menu_fetch_llm._sanitize_location_texts(raw)
        self.assertEqual(out[0], "12 Foo St, Sydney NSW")   # stripped
        self.assertEqual(len(out), 8)                        # capped at 8
        self.assertEqual(len(out[1]), 120)                   # each capped at 120
        self.assertEqual(len(set(out)), len(out))            # deduped
        self.assertTrue(all(isinstance(s, str) for s in out))

    def test_non_list_is_empty(self):
        self.assertEqual(menu_fetch_llm._sanitize_location_texts(None), [])
        self.assertEqual(menu_fetch_llm._sanitize_location_texts("Sydney"), [])


class MatrixCallLocationTests(unittest.TestCase):
    def _response(self, parsed):
        return {"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": __import__("json").dumps(parsed)}]}}]}

    def test_matrix_call_returns_location_texts(self):
        parsed = {"rows": [{"dish": "Burger", "allergens": ["milk"]}],
                  "columns": ["milk"],
                  "visible_location_text": ["Shake Shack Australia Pty Ltd, Sydney NSW"]}
        with mock.patch.object(menu_fetch_llm, "_post_gemini_generate_content",
                               return_value=self._response(parsed)):
            rows, columns, truncated, texts = menu_fetch_llm._matrix_call(
                {"contents": []}, "key", "model")
        self.assertEqual(texts, ["Shake Shack Australia Pty Ltd, Sydney NSW"])
        self.assertEqual([r["dish"] for r in rows], ["Burger"])

    def test_matrix_call_tolerates_missing_field(self):
        parsed = {"rows": [], "columns": []}
        with mock.patch.object(menu_fetch_llm, "_post_gemini_generate_content",
                               return_value=self._response(parsed)):
            rows, columns, truncated, texts = menu_fetch_llm._matrix_call(
                {"contents": []}, "key", "model")
        self.assertEqual(texts, [])


class ExtractReturnShapeTests(unittest.TestCase):
    def test_no_key_returns_empty_tuple(self):
        items, texts = menu_fetch_llm.extract_allergen_matrix_via_gemini_pdf(
            b"%PDF-1.4", api_key=None)
        self.assertEqual(items, [])
        self.assertEqual(texts, [])

    def test_schema_declares_field_optional(self):
        props = menu_fetch_llm.ALLERGEN_MATRIX_SCHEMA["properties"]
        self.assertIn("visible_location_text", props)
        self.assertNotIn("visible_location_text",
                         menu_fetch_llm.ALLERGEN_MATRIX_SCHEMA["required"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_matrix_vision_location.py -v`
Expected: FAIL — `AttributeError: ... no attribute '_sanitize_location_texts'`; `test_no_key_returns_empty_tuple` fails unpacking (`[]` is not a 2-tuple); `_matrix_call` tests fail unpacking 3-tuple into 4 names.

- [ ] **Step 3: Implement in `safeplate/menu_fetch_llm.py`:**

(a) Schema — inside `ALLERGEN_MATRIX_SCHEMA["properties"]`, after the `"rows"` entry:

```python
        # Verbatim location clues visible in the image (address lines, footer
        # URLs, phone numbers, country names). Transcription ONLY -- the region
        # verdict stays with extraction2.region.detect_source_region.
        "visible_location_text": {"type": "array", "items": {"type": "string"}},
```

(`"required": ["rows"]` stays as-is.)

(b) Prompt — append to the end of the `ALLERGEN_MATRIX_SYSTEM` string (inside the final parenthesis, as a new trailing string segment):

```python
    "\nAlso output `visible_location_text`: up to 8 short verbatim snippets of any "
    "text visible in the image that indicates WHERE this restaurant or menu is from "
    "-- street address lines, footer website URLs, phone numbers, country names. "
    "Transcribe exactly what is printed; never guess, infer, or normalize a "
    "location; omit the field when no such text is visible."
```

(c) `_matrix_call` — return 4-tuple; replace the last line and update the docstring's first line accordingly:

```python
    return (parsed.get("rows", []), parsed.get("columns", []), truncated,
            _sanitize_location_texts(parsed.get("visible_location_text")))
```

(d) New helper directly below `_matrix_call`:

```python
def _sanitize_location_texts(values) -> list[str]:
    """Clean the model's location snippets: strings only, stripped, deduped
    (order-preserving), max 8 snippets of <=120 chars. Anything else -> []."""
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()[:120]
        if text and text not in out:
            out.append(text)
        if len(out) == 8:
            break
    return out
```

(e) `_render_matrix_pages` — change signature end to `-> list[str]` and collect snippets. The batched branch becomes:

```python
    if len(images) > 1:
        try:
            rows, columns, truncated, texts = _matrix_call(_matrix_images_payload(images), api_key, model)
            if rows and not truncated:
                _absorb_matrix_rows(rows, records, seen, restaurant_name,
                                    restaurant_source_id, columns)
                return texts
        except Exception:
            pass

    location_texts: list[str] = []
    for image_bytes in images:
        try:
            rows, columns, _truncated, texts = _matrix_call(_matrix_image_payload(image_bytes), api_key, model)
        except Exception:
            continue
        location_texts.extend(t for t in texts if t not in location_texts)
        _absorb_matrix_rows(rows, records, seen, restaurant_name, restaurant_source_id, columns)
    return location_texts[:8]
```

(also change the early `return` after `if not images:` to `return []`).

(f) `extract_allergen_matrix_via_gemini_pdf` — return type `tuple[list[MenuItemRecord], list[str]]`; the three early `return []` lines become `return [], []`; the body's tail becomes:

```python
    records: list[MenuItemRecord] = []
    seen: set[str] = set()
    location_texts: list[str] = []
    try:
        location_texts = _render_matrix_pages(pdf, max_pages, api_key, model, records, seen,
                                              restaurant_name, restaurant_source_id)
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return records, location_texts
```

Update the docstring's "Returns [] on missing key/renderer/failure." to "Returns ([], []) on missing key/renderer/failure."

- [ ] **Step 4: Widen the two other callers (sanctioned):**
- `safeplate/menu_text.py:631`: `vision_items, _vision_location_texts = extract_allergen_matrix_via_gemini_pdf(` (v1 path deliberately ignores the snippets — out of the spec's scope).
- `tests/test_menu_fallbacks.py:118`: `self.assertEqual(extract_allergen_matrix_via_gemini_pdf(b"%PDF-1.4", api_key=None), ([], []))`
- `tests/test_matrix_vision_pages.py:37` area: widen the call's unpack the same way (read the surrounding assertion first; only the unpack may change).

- [ ] **Step 5: Run new tests + the two touched test files**

Run: `python -m pytest tests/test_matrix_vision_location.py tests/test_menu_fallbacks.py tests/test_matrix_vision_pages.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add safeplate/menu_fetch_llm.py safeplate/menu_text.py tests/test_matrix_vision_location.py tests/test_menu_fallbacks.py tests/test_matrix_vision_pages.py
git commit -m "feat(vision): matrix read transcribes visible location text"
```

---

### Task 2: Cache + plumbing in `interpret_pdf_matrix` (`extraction2/interpret_llm.py`)

**Files:**
- Modify: `safeplate/extraction2/interpret_llm.py:136-180` (`interpret_pdf_matrix`)
- Test: `tests/test_matrix_vision_location.py` (append)

**Interfaces:**
- Consumes: Task 1's `extract_allergen_matrix_via_gemini_pdf(...) -> (items, location_texts)`.
- Produces: `interpret_pdf_matrix(...) -> tuple[list[MenuItemRecord], list[str]]`; cache blob `{"at", "items", "location_texts"}` under key prefix `b"pdfmatrix2:"`. Task 3 consumes the tuple.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_matrix_vision_location.py`:

```python
import time

import pytest

from safeplate import cache_store
from safeplate.extraction2 import interpret_llm


def _pdf_payload():
    # Mirror how existing tests build a PDF Payload (see tests/test_pdfplumber_gating.py
    # for the constructor convention); adjust field names to the real Payload dataclass.
    from safeplate.extraction2.schema import Payload, PayloadKind
    return Payload(url="https://x.example/allergens.pdf", kind=PayloadKind.TEXTUAL,
                   source_type="pdf", text="allergen chart", content=b"%PDF-fake")


def test_pdf_matrix_key_uses_v2_prefix(monkeypatch):
    seen = {}
    monkeypatch.setattr(interpret_llm.cache_store, "load",
                        lambda ns, key: seen.setdefault("key", key))
    with pytest.raises(Exception):
        # load returns a str (not a blob) -> downstream will fail; we only
        # care that the KEY was computed with the new prefix before that.
        interpret_llm.interpret_pdf_matrix(_pdf_payload(), api_key="k", model="m")
    import hashlib
    expected = hashlib.sha1(b"pdfmatrix2:" + b"m" + b":" + b"%PDF-fake").hexdigest()
    assert seen["key"] == expected


def test_pdf_matrix_cache_hit_returns_location_texts(monkeypatch):
    blob = {"at": time.time(), "items": [], "location_texts": ["Sydney NSW"]}
    monkeypatch.setattr(interpret_llm.cache_store, "load", lambda ns, key: blob)
    items, texts = interpret_llm.interpret_pdf_matrix(_pdf_payload(), api_key="k", model="m")
    assert texts == ["Sydney NSW"]


def test_pdf_matrix_old_blob_without_field(monkeypatch):
    blob = {"at": time.time(), "items": []}
    monkeypatch.setattr(interpret_llm.cache_store, "load", lambda ns, key: blob)
    items, texts = interpret_llm.interpret_pdf_matrix(_pdf_payload(), api_key="k", model="m")
    assert items == [] and texts == []


def test_pdf_matrix_saves_location_texts(monkeypatch, tmp_path):
    from safeplate.menu_text import MenuItemRecord

    monkeypatch.setattr(interpret_llm.cache_store, "load", lambda ns, key: None)
    saved = {}
    monkeypatch.setattr(interpret_llm.cache_store, "save",
                        lambda ns, key, blob: saved.update(blob=blob))
    fake_item = MenuItemRecord(item_name="Burger")
    monkeypatch.setattr(
        "safeplate.menu_fetch_llm.extract_allergen_matrix_via_gemini_pdf",
        lambda *a, **k: ([fake_item], ["12 Foo St, Sydney"]),
    )
    items, texts = interpret_llm.interpret_pdf_matrix(_pdf_payload(), api_key="k", model="m")
    assert texts == ["12 Foo St, Sydney"]
    assert saved["blob"]["location_texts"] == ["12 Foo St, Sydney"]
```

NOTE for the implementer: `_pdf_payload()` and `MenuItemRecord(item_name=...)` must match the real constructors — check `safeplate/extraction2/schema.py` (Payload, PayloadKind) and `safeplate/menu_text.py` (MenuItemRecord defaults) and adjust required kwargs; keep the assertions unchanged. If `MenuItemRecord` requires more fields, fill them with obvious defaults (`""`/`[]`/`0.0`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_matrix_vision_location.py -v -k pdf_matrix`
Expected: prefix test FAILS (old `pdfmatrix:` prefix); hit/old-blob/save tests FAIL unpacking a list into 2 names.

- [ ] **Step 3: Implement** — in `interpret_pdf_matrix`:
- Return annotation → `tuple[list[MenuItemRecord], list[str]]`; the `if not payload.content:` early return → `return [], []`.
- Key line → `key = hashlib.sha1(b"pdfmatrix2:" + model.encode("utf-8") + b":" + payload.content).hexdigest()` with a comment: `# pdfmatrix2: v2 blobs carry location_texts; old v1 entries must not be served (user-decided cache clear, spec 2026-07-07-vision-location-capture).`
- Cache-hit block →

```python
    if use_cache:
        blob = cache_store.load("extraction2_pdfmatrix", key)
        try:
            if blob is not None and time.time() - blob.get("at", 0) <= _CACHE_TTL:
                return (
                    [MenuItemRecord(**item) for item in blob["items"]],
                    list(blob.get("location_texts", [])),
                )
        except (KeyError, TypeError):
            pass
```

- Fresh-call tail →

```python
    items, location_texts = extract_allergen_matrix_via_gemini_pdf(
        payload.content,
        restaurant_name=payload.restaurant_name or "",
        restaurant_source_id=payload.restaurant_source_id or "",
        api_key=api_key,
        model=model,
    )
    if items:  # only cache real results; never cache a quota/transient failure
        cache_store.save(
            "extraction2_pdfmatrix",
            key,
            {"at": time.time(), "items": [asdict(i) for i in items],
             "location_texts": location_texts},
        )
    return items, location_texts
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_matrix_vision_location.py -v`
Expected: all PASS. (Task 3 hasn't updated pipeline yet — do NOT run the full suite; `pipeline.py`'s caller still expects a bare list and its tests would fail. That's expected mid-stack; Task 3 restores it.)

- [ ] **Step 5: Commit**

```bash
git add safeplate/extraction2/interpret_llm.py tests/test_matrix_vision_location.py
git commit -m "feat(extraction2): pdfmatrix cache v2 carries location snippets"
```

---

### Task 3: Region stamp threading (`extraction2/pipeline.py`)

**Files:**
- Modify: `safeplate/extraction2/pipeline.py` (`_interpret_one` ~112-260, coverage build ~44-77)
- Test: `tests/test_matrix_vision_location.py` (append)

**Interfaces:**
- Consumes: Task 2's `interpret_pdf_matrix -> (items, location_texts)`.
- Produces: `_interpret_one(...) -> tuple[list[MenuItemRecord], str, str, int, bool, str]` — sixth element `region_text_extra` ("" everywhere except matrix-success paths).

- [ ] **Step 1: Write the failing test** — append to `tests/test_matrix_vision_location.py`:

```python
def test_matrix_location_text_stamps_coverage_region(monkeypatch):
    from safeplate.extraction2 import pipeline
    from safeplate.menu_text import MenuItemRecord

    fake_item = MenuItemRecord(item_name="Burger", allergen_terms=["milk"],
                               extraction_method="gemini_pdf_matrix")
    monkeypatch.setattr(
        pipeline.interpret_llm, "interpret_pdf_matrix",
        lambda p, **k: ([fake_item], ["Shake Shack Australia Pty Ltd, Sydney NSW"]),
    )
    # Text LLM finds nothing net-new -> the matrix-only return path is taken.
    monkeypatch.setattr(pipeline.interpret_llm, "interpret_text",
                        lambda p, **k: ([], False, 0))
    payload = _pdf_payload()  # pdf + allergen-y text -> matrix branch fires
    result = pipeline.extract_menu(
        [payload], policy=_default_policy(), llm_enabled=True,
        gemini_api_key="k", gemini_model="m",
    )
    assert result.coverage[0].region == "AU"


def test_no_location_text_keeps_todays_stamp(monkeypatch):
    from safeplate.extraction2 import pipeline
    from safeplate.menu_text import MenuItemRecord

    fake_item = MenuItemRecord(item_name="Burger", allergen_terms=["milk"],
                               extraction_method="gemini_pdf_matrix")
    monkeypatch.setattr(pipeline.interpret_llm, "interpret_pdf_matrix",
                        lambda p, **k: ([fake_item], []))
    monkeypatch.setattr(pipeline.interpret_llm, "interpret_text",
                        lambda p, **k: ([], False, 0))
    result = pipeline.extract_menu(
        [_pdf_payload()], policy=_default_policy(), llm_enabled=True,
        gemini_api_key="k", gemini_model="m",
    )
    assert result.coverage[0].region == ""  # "allergen chart" text has no region tell


def test_location_snippets_never_become_items(monkeypatch):
    # Spec req 4: snippets are provenance hints only. Even dish-like snippet
    # text must not appear among extracted items.
    from safeplate.extraction2 import pipeline
    from safeplate.menu_text import MenuItemRecord

    fake_item = MenuItemRecord(item_name="Burger", allergen_terms=["milk"],
                               extraction_method="gemini_pdf_matrix")
    monkeypatch.setattr(
        pipeline.interpret_llm, "interpret_pdf_matrix",
        lambda p, **k: ([fake_item], ["Peanut Chicken Special, 5 Sydney Rd"]),
    )
    monkeypatch.setattr(pipeline.interpret_llm, "interpret_text",
                        lambda p, **k: ([], False, 0))
    result = pipeline.extract_menu(
        [_pdf_payload()], policy=_default_policy(), llm_enabled=True,
        gemini_api_key="k", gemini_model="m",
    )
    assert [i.item_name for i in result.items] == ["Burger"]
```

NOTE for the implementer: `extract_menu`' exact signature/`_default_policy()` — mirror an existing pipeline test (e.g. `tests/test_llm_call_accounting.py` or `tests/test_pipeline_inferred_allergens.py`) for how Policy and the call are constructed, and add a `_default_policy()` helper accordingly; keep the region assertions unchanged. The `_looks_allergen` gate must pass — the payload's `text` contains "allergen".

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_matrix_vision_location.py -v -k stamps_coverage`
Expected: FAIL — `ValueError: too many values to unpack`/`not enough values` inside `extract_menu` (5-name unpack vs the code paths), or region assert fails, depending on order of edits.

- [ ] **Step 3: Implement:**
(a) `_interpret_one`: change the return annotation to `tuple[list[MenuItemRecord], str, str, int, bool, str]` and the docstring's first line to include `region_text_extra` ("visible-location text transcribed by the vision matrix read; '' for every other path"). In the matrix branch, unpack `matrix, matrix_location_texts = interpret_llm.interpret_pdf_matrix(...)` (the `except LLMNotEnabled:` arm sets `matrix, matrix_location_texts = [], []`), define `matrix_region_text = " ".join(matrix_location_texts)`, and append it to BOTH matrix-success returns (`"gemini_pdf_matrix+text"` and the vision-only return below it). Then grep every other `return` statement inside `_interpret_one` and append `, ""` as the sixth element — including the VISUAL branch, the LLMNotEnabled arms, and all fallthrough paths.
(b) `extract_menu` (~line 45): widen the unpack —

```python
        items, interpreter, reason, llm_used, payload_incomplete, region_extra = _interpret_one(
```

and change the region line inside the `CoverageReport(` construction to:

```python
                region=(detect_source_region(
                    (payload.text or "")
                    + ((" " + region_extra) if region_extra else ""),
                    payload.url,
                ) or "")
                if items else "",
```

(c) Grep the repo for any OTHER caller of `_interpret_one` (tests included) and widen those unpacks the same sanctioned way.

- [ ] **Step 4: Run the new tests, then the full suite**

Run: `python -m pytest tests/test_matrix_vision_location.py -v` — all PASS.
Run: `python -m pytest -q` — fully green (this proves Task 2's mid-stack break is healed and no other consumer regressed).

- [ ] **Step 5: Commit**

```bash
git add safeplate/extraction2/pipeline.py tests/test_matrix_vision_location.py
git commit -m "feat(extraction2): matrix location snippets feed the region stamp"
```

---

### Task 4: Result-cache v7 bump + final verification

**Files:**
- Modify: `safeplate/extraction2/discover.py:438-449` (`_RESULT_CACHE_VERSION` + changelog comment)
- Test: `tests/test_matrix_vision_location.py` (append)

**Interfaces:**
- Consumes: nothing new. Produces: `_RESULT_CACHE_VERSION == "7"`.

- [ ] **Step 1: Write the failing test** — append:

```python
def test_result_cache_version_bumped_for_location_capture():
    # Protects the user's cache-clear decision (spec §Requirements 6): all
    # pre-location-capture results must re-extract.
    from safeplate.extraction2 import discover
    assert discover._RESULT_CACHE_VERSION == "7"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_matrix_vision_location.py::test_result_cache_version_bumped_for_location_capture -v`
Expected: FAIL — version is "6".

- [ ] **Step 3: Implement** — in `discover.py`, extend the changelog comment block above the constant and bump:

```python
# v7: vision location capture -- matrix-PDF sources gain region stamps from
# footer text the vision read transcribes; invalidate so every cached result
# re-extracts with location capture (user-decided cache clear).
_RESULT_CACHE_VERSION = "7"
```

Also grep tests for a hardcoded `"6"` version assertion (e.g. in cache-key tests) and update it to `"7"` if one exists (sanctioned: version-literal only).

- [ ] **Step 4: Full suite**

Run: `python -m pytest -q`
Expected: fully green.

- [ ] **Step 5: Commit**

```bash
git add safeplate/extraction2/discover.py tests/test_matrix_vision_location.py
git commit -m "feat(cache): result-cache v7 -- re-extract all with location capture"
```

---

## Post-implementation (ops, hand to the user)

After deploy (git pull + docker build + docker run on the EC2), old cache rows are unreferenced. Optional RDS tidy-up:
`DELETE FROM cache_entries WHERE namespace IN ('extraction2_result', 'extraction2_pdfmatrix');`
Re-extraction reuses still-valid text-LLM chunk caches; re-spend is mostly the vision reads. Verification: search a chain whose allergen matrix is an image-only PDF from another country and confirm the drawer's "Allergen data is from <country>" notice fires with the correct country.
