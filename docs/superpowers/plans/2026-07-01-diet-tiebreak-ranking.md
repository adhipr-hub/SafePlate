# Diet-as-Tie-Breaker Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make diet compatibility break ties in the "Safest first" list — ordering dishes of *exactly equal* allergen risk by how well they fit the selected diet(s) — without ever perturbing the allergen safety ordering.

**Architecture:** Refactor the client-side comparator in `safeplate/app_template.html` into two pure, self-contained helpers (`dietSortKey`, `compareByRisk`) delimited by `/* @sort-core:start */ … /* @sort-core:end */` sentinels. `sortRows()` calls `compareByRisk` for the risk sort. The helpers read the already-present `row.menuDetail.summary.diets` payload; no backend change. Behavioral coverage comes from a pytest that extracts the sentinel block and exercises it under `node`.

**Tech Stack:** Vanilla client JS (in the HTML template), Python + pytest, Node (v26, on PATH) as the JS test runner via `subprocess`.

## Global Constraints

- **Allergen safety ordering is inviolate.** Diet is a strict secondary key: it may only reorder rows whose `allergenPrior.risk` is *exactly equal*, and only when `state.diets` is non-empty. Copied from spec.
- **Default-equivalence invariant.** With `state.diets` empty, `sortRows` output must be byte-identical to today (protects the quality gate). Verbatim from spec.
- **No backend change.** `summary.diets` already carries `verdict` and `basis` (`safeplate/menu_service.py:191-207`; value vocab in `safeplate/diet_score.py:45-47`).
- **Verdict rank:** `good_options(3) > limited(2) > unknown(1) > not_compatible(0)`.
- **Basis rank:** `labeled(4) > ai_assessed(3) > mixed(2) > estimated(1) > none(0)`.
- **Multi-diet:** weakest-fit-wins (minimum diet key across all selected diets).
- **Higher diet key sorts earlier** (better fit first). A row with no `summary.diets` at all gets the lowest key (`-1`), below every assessed verdict. A row that HAS `summary.diets` but no entry for a selected diet treats that diet as verdict `unknown` + basis `none`.

---

### Task 1: Diet tie-breaker comparator

**Files:**
- Modify: `safeplate/app_template.html:1798-1805` (the `sortRows` function)
- Test: `tests/test_diet_sort_tiebreak.py` (create)

**Interfaces:**
- Produces (inside the `@sort-core` sentinel block, so the test can extract them):
  - `dietSortKey(row, diets)` → `number`. Worst diet fit across `diets` (array of diet-id strings) for `row`, as `verdictRank*10 + basisRank`. Returns `-1` when `row` has no `menuDetail.summary.diets` array or `diets` is empty.
  - `compareByRisk(a, b, diets)` → `number`. Ascending allergen risk; on an exact risk tie with non-empty `diets`, orders by descending `dietSortKey`.
- Consumes: `row.allergenPrior.risk` (float | undefined) and `row.menuDetail.summary.diets` (array of `{diet, verdict, basis}` | absent), both already produced by the upgrade path at `app_template.html:1772` and `menu_service.py:191-207`. `state.diets` (array of diet-id strings) already exists (`app_template.html:1291`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_diet_sort_tiebreak.py`:

```python
"""Behavioral guard for the client-side diet tie-breaker in the risk sort.

The sort logic is pure JS living inside a sentinel-delimited block in
``safeplate/app_template.html``. We extract that block and exercise it under
node so the ranking rules (and the default-equivalence invariant) are covered
without a browser. Skips if node is unavailable."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "safeplate" / "app_template.html"


def _extract_sort_core() -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    m = re.search(
        r"/\* @sort-core:start.*?\*/(.*?)/\* @sort-core:end \*/", html, re.S
    )
    assert m, "sort-core sentinel block not found in app_template.html"
    return m.group(1)


_DRIVER = r"""
import assert from 'node:assert/strict';
__CORE__

const row = (risk, diets) => ({
  allergenPrior: { risk },
  menuDetail: diets ? { summary: { diets } } : undefined,
});
const veg = (verdict, basis) => ({ diet: 'vegan', verdict, basis });
const gf  = (verdict, basis) => ({ diet: 'gluten_free', verdict, basis });

// 1) default-equivalence: no diets -> pure risk diff, and 0 on an exact tie
assert.equal(compareByRisk(row(0.2), row(0.5), []), 0.2 - 0.5);
assert.equal(
  compareByRisk(row(0.4, [veg('good_options', 'labeled')]),
               row(0.4, [veg('not_compatible', 'none')]), []),
  0);

// 2) unequal risk: diet never reorders (safer dish always first)
assert.ok(compareByRisk(
  row(0.2, [veg('not_compatible', 'none')]),
  row(0.3, [veg('good_options', 'labeled')]), ['vegan']) < 0);

// 3) tie + single diet: better verdict wins; verdict tie broken by basis;
//    any assessed verdict beats a no-data row
assert.ok(compareByRisk(
  row(0.4, [veg('good_options', 'labeled')]),
  row(0.4, [veg('limited', 'estimated')]), ['vegan']) < 0);
assert.ok(compareByRisk(
  row(0.4, [veg('good_options', 'labeled')]),
  row(0.4, [veg('good_options', 'estimated')]), ['vegan']) < 0);
assert.ok(compareByRisk(
  row(0.4, [veg('good_options', 'labeled')]),
  row(0.4, null), ['vegan']) < 0);

// 4) multi-diet weakest-fit-wins: great-vegan/no-gf loses to limited-both
const a4 = row(0.4, [veg('good_options', 'labeled'), gf('not_compatible', 'none')]);
const b4 = row(0.4, [veg('limited', 'estimated'), gf('limited', 'estimated')]);
assert.ok(compareByRisk(a4, b4, ['vegan', 'gluten_free']) > 0);  // a sorts after b

// 5) key rules: no-data row = -1; missing entry for a selected diet = unknown/none (10)
assert.equal(dietSortKey(row(0.4, null), ['vegan']), -1);
assert.ok(dietSortKey(row(0.4, [veg('good_options', 'labeled')]), ['vegan']) > -1);
assert.equal(
  dietSortKey(row(0.4, [veg('good_options', 'labeled')]), ['vegan', 'gluten_free']),
  10);

console.log('sort-core OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_diet_tiebreak_sort_core(tmp_path):
    driver = _DRIVER.replace("__CORE__", _extract_sort_core())
    f = tmp_path / "sort_core_test.mjs"
    f.write_text(driver, encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_diet_sort_tiebreak.py -v`
Expected: FAIL — `AssertionError: sort-core sentinel block not found in app_template.html` (the block does not exist yet).

- [ ] **Step 3: Implement the sort-core helpers and rewire `sortRows`**

In `safeplate/app_template.html`, replace the current function (lines 1798-1805):

```javascript
/* ── sort & render cards ── */
function sortRows(rows) {
  return [...rows].sort((a,b)=>{
    if (state.sort==="risk")     return (a.allergenPrior?.risk??1)-(b.allergenPrior?.risk??1);
    if (state.sort==="distance") return (a.distance_meters??9e9)-(b.distance_meters??9e9);
    if (state.sort==="rating")   return (b.rating??0)-(a.rating??0);
    return 0;
  });
}
```

with:

```javascript
/* ── sort & render cards ── */
/* @sort-core:start — PURE sort logic, unit-tested in tests/test_diet_sort_tiebreak.py.
   Keep self-contained: no globals (state is passed in), so the test can extract and
   run just this block under node. Diet only breaks EXACT allergen-risk ties. */
const _DIET_VERDICT_RANK = { good_options:3, limited:2, unknown:1, not_compatible:0 };
const _DIET_BASIS_RANK   = { labeled:4, ai_assessed:3, mixed:2, estimated:1, none:0 };

// Worst diet fit across the selected diets, as a comparable number (higher = better fit).
// verdict dominates (×10); basis (max 4) breaks verdict ties. Rows with no diet data
// return -1 so a confirmed verdict never loses its slot to an un-assessed card.
function dietSortKey(row, diets) {
  const entries = row?.menuDetail?.summary?.diets;
  if (!Array.isArray(entries) || !diets?.length) return -1;
  let worst = Infinity;
  for (const d of diets) {
    const e = entries.find(x => x?.diet === d);            // missing -> unknown/none
    const v = _DIET_VERDICT_RANK[e?.verdict] ?? 1;
    const b = _DIET_BASIS_RANK[e?.basis] ?? 0;
    worst = Math.min(worst, v*10 + b);
  }
  return worst === Infinity ? -1 : worst;
}

// Allergen risk is primary; diet fit breaks EXACT ties only when diets are selected.
function compareByRisk(a, b, diets) {
  const ra = a.allergenPrior?.risk ?? 1, rb = b.allergenPrior?.risk ?? 1;
  if (ra !== rb) return ra - rb;
  if (!diets?.length) return 0;                            // default-equivalence
  return dietSortKey(b, diets) - dietSortKey(a, diets);    // higher key sorts earlier
}
/* @sort-core:end */

function sortRows(rows) {
  return [...rows].sort((a,b)=>{
    if (state.sort==="risk")     return compareByRisk(a, b, state.diets);
    if (state.sort==="distance") return (a.distance_meters??9e9)-(b.distance_meters??9e9);
    if (state.sort==="rating")   return (b.rating??0)-(a.rating??0);
    return 0;
  });
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_diet_sort_tiebreak.py -v`
Expected: PASS (`sort-core OK` printed inside node; test green).

- [ ] **Step 5: Syntax-check the whole template's JS**

Extract the `<script>` body and run `node --check` per the repo convention:

Run:
```bash
python -c "import re,pathlib,subprocess,tempfile,os; \
h=pathlib.Path('safeplate/app_template.html').read_text(encoding='utf-8'); \
m=re.search(r'<script>(.*)</script>', h, re.S); \
f=tempfile.NamedTemporaryFile('w',suffix='.js',delete=False,encoding='utf-8'); \
f.write(m.group(1)); f.close(); \
r=subprocess.run(['node','--check',f.name]); os.unlink(f.name); \
raise SystemExit(r.returncode)"
```
Expected: exit 0, no syntax error.

- [ ] **Step 6: Run the full suite (regression / no backend touched)**

Run: `python -m pytest -q`
Expected: all tests pass, including `tests/test_allergen_generic_score.py::test_nut_profile_byte_identical` (the nut/allergen path is untouched — no Python changed).

- [ ] **Step 7: Commit**

```bash
git add safeplate/app_template.html tests/test_diet_sort_tiebreak.py
git commit -m "feat(diet): diet compatibility breaks exact allergen-risk ties in Safest-first sort"
```

---

### Task 2: Manual browser smoke (documented, non-blocking)

**Files:** none (verification only).

**Interfaces:**
- Consumes: the shipped `compareByRisk`/`sortRows` from Task 1 and the live `/api/menu` diet payload.

- [ ] **Step 1: Launch the app**

Run: `python scripts/start_safeplate_app.py --demo`
Expected: server starts and prints the local URL.

- [ ] **Step 2: Verify the tie-break in the browser (record the result in the commit/PR notes)**

Manual checks (default-equivalence first — the safety-critical one):
1. With **no diet selected**, note the "Safest first" order of the result list. This is the baseline; the next checks must not change it except where diet applies.
2. Select **Vegan**. Where two cards show the *same* risk (same tier chip + same risk read), confirm the one with the stronger diet badge (e.g. "good options" / labeled) now sits above the weaker/"limited"/un-assessed one. Cards with *different* risk must keep their risk order.
3. Select **Vegan + Gluten-free** together. Confirm a card that's great for one but incompatible with the other does **not** jump above a card that's moderately compatible with both (weakest-fit-wins).
4. Confirm no card with higher risk ever rises above a safer card because of diet.

Expected: ties reorder by diet fit; non-tied and no-diet ordering is unchanged. Document what you observed (a one-line note is enough; this step does not block the Task 1 commit).

- [ ] **Step 3: Stop the app**

Stop the dev server (Ctrl-C / kill the process). No leftover process should hold the port.

---

## Self-Review

**Spec coverage:**
- Primary risk sort unchanged → Task 1 Step 3 (`compareByRisk` risk branch) + test case 2.
- Diet fires only on exact tie + diet selected → `compareByRisk` guards + test cases 1, 2, 3.
- Default-equivalence → Global Constraints + test case 1 + Task 2 Step 2.1.
- Verdict-then-basis key → `dietSortKey` (`v*10 + b`) + test case 3.
- No-data = lowest; missing-entry = unknown/none → Global Constraints + test case 5.
- Multi-diet weakest-fit-wins → `Math.min` over diets + test case 4.
- Client-only, no backend change → File list (only `app_template.html` + new test).
- Re-sort triggers already gate on `state.sort==="risk"` (`app_template.html:1779` + render path) → no change needed; covered by Task 2 live smoke.

**Placeholder scan:** none — every code and command step is concrete.

**Type consistency:** `dietSortKey(row, diets)` and `compareByRisk(a, b, diets)` signatures are identical across the Interfaces block, the implementation (Step 3), and the test driver (Step 1). Verdict/basis rank tables match the Global Constraints values.
