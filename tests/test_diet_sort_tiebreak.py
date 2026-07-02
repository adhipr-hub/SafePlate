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
