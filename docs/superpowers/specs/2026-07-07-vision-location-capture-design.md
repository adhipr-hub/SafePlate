# Vision location capture for allergen-matrix PDFs — design

**Date:** 2026-07-07
**Status:** approved by user (conversation), pending spec review
**Context:** Each extraction source carries a per-source region stamp
(`CoverageReport.region`, ISO2 or ""), computed deterministically by
`extraction2.region.detect_source_region(visible_text, url)` at
`pipeline.py` coverage-build time. It powers the safety-critical
"Allergen data is from <country>" notice. Allergen-matrix PDFs read by
Gemini vision (`interpret_pdf_matrix` → `extract_allergen_matrix_via_gemini_pdf`)
often have a thin/useless text layer, so their stamp is frequently "" (or
URL-only) even when the rendered pages show an address footer — which only
the vision model ever sees. This feature has the vision call transcribe
those location clues so the existing detector can judge them.

## Requirements

1. The vision matrix read also returns `visible_location_text`: verbatim
   short text snippets visible in the document that hint at location —
   address lines, footer URLs, phone numbers with country codes, country
   names. At most 8 snippets, each ≤120 chars, transcription only.
2. The pdf-matrix source's region stamp is computed from the page text
   PLUS those snippets: `detect_source_region((payload.text or "") + " " +
   " ".join(snippets), payload.url)`. All other sources unchanged.
3. **The LLM never asserts a region.** Only the existing deterministic
   detector (country names + domain tells, the post-v6 visible-text rules)
   turns snippets into an ISO2 code. No detector changes.
4. Snippets are provenance hints ONLY: they must never enter menu items,
   allergy/diet signals, or grounding checks, and are not persisted in the
   result-cache blob beyond the coverage region stamp they produce.
5. No-signal behavior identical to today: missing/empty field, old cache
   blobs without the field, or snippets the detector can't match → region
   "" (undetectable). The UI notice logic is untouched.
6. **Cache clear (user decision):** all previously cached extractions must
   re-extract with the new logic. Mechanism: bump
   `discover._RESULT_CACHE_VERSION` "6" → "7" (with a comment line noting
   why, following the file's existing changelog convention), and version
   the pdfmatrix cache key (`"pdfmatrix:"` → `"pdfmatrix2:"` in the sha1
   input) so old vision reads (which lack snippets) are never served.
   Text-LLM chunk caches stay valid (keyed by content) so re-extraction
   reuses them. Old rows in `cache_entries` become unreferenced; the ops
   note below includes an optional cleanup SQL.

## Design

### 1. Vision call (`safeplate/menu_fetch_llm.py`)
`extract_allergen_matrix_via_gemini_pdf`'s response JSON schema gains an
optional array property `visible_location_text` (strings), and the system
prompt gains one instruction: transcribe, verbatim, any text visible in
the document that indicates where the restaurant/menu is from (addresses,
footer URLs, phone numbers, country names); do not guess or infer; omit
the field if none is visible. Return type widens from
`list[MenuItemRecord]` to `tuple[list[MenuItemRecord], list[str]]`
(snippets sanitized: strings only, stripped, capped at 8 × 120 chars).

### 2. Vision cache + plumbing (`safeplate/extraction2/interpret_llm.py`)
`interpret_pdf_matrix` cache blob becomes `{"at", "items",
"location_texts"}` under the re-versioned key; loads tolerate a missing
`location_texts` (default `[]`). Return type widens to
`tuple[list[MenuItemRecord], list[str]]`; its only caller adapts.

### 3. Region stamp (`safeplate/extraction2/pipeline.py`)
`_interpret_one` returns a sixth element `region_text_extra: str` (""
everywhere except the pdf-matrix branches, which pass
`" ".join(location_texts)`; the matrix+text union path passes the same).
The coverage-build site appends it to the text handed to
`detect_source_region`. `CoverageReport` schema is unchanged.

### 4. Out of scope
Detector rule changes; asking the text-LLM paths for location (their
sources already expose visible text); UI changes; persisting snippets
anywhere except the vision cache blob; other document types (HTML,
text-parsed PDFs).

## Testing (TDD)
- Vision-call unit test (stubbed Gemini response): schema accepts the
  field; snippets sanitized/capped; absent field → `[]`.
- interpret_pdf_matrix: caches and returns snippets; old-format cached
  blob (no `location_texts`) loads with `[]`; new key prefix means old
  key's entry is not served (assert different cache key vs "pdfmatrix:").
- Pipeline: stubbed matrix read returning a footer snippet
  ("Shake Shack Australia Pty Ltd, Sydney NSW") → that source's
  `CoverageReport.region == "AU"`; no snippets → stamp identical to
  today's; a non-matrix source's stamp unaffected.
- Grounding guard: snippets never appear in items/signals (assert
  item names unchanged when snippets contain dish-like text).
- `_RESULT_CACHE_VERSION == "7"` asserted (protects the user's cache-clear
  decision); full suite green.

## Ops note (after deploy)
Old cache rows are unreferenced, not harmful. Optional tidy-up:
`DELETE FROM cache_entries WHERE namespace IN ('extraction2_result','extraction2_pdfmatrix');`
(next searches repopulate under the new version). Re-extraction reuses
still-valid text-LLM chunk caches, so the re-spend is mostly vision reads.

## Success criteria
- A vision-read allergen matrix whose footer shows a foreign address gets
  a non-empty, correct region stamp, and the drawer shows the existing
  "data is from <country>" notice when it mismatches the diner's region.
  **Scope truth-up (post-implementation):** the stamp lands only when the
  transcribed footer text carries a detector tell (a ccTLD-bearing URL such
  as `shakeshack.com.au`, or a corroborated multiword country name); a bare
  street address with no such tell yields no stamp today, by the detector's
  low-false-positive design. Widening this (a curated-snippet-only
  country-name channel in `detect_source_region`, gated on the frozen
  benchmarks) is an agreed fast-follow candidate, not part of this feature.
- Zero behavior change for HTML/text sources and for matrices with no
  visible location text.
- All cached restaurants re-extract with the new logic (version bumps).
