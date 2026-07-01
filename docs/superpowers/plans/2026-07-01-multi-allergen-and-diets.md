# Multi-allergen + Diets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend SafePlate from nuts-only scoring to all EU-14 allergens plus vegetarian/vegan diet modes and a gluten-free mode, without regressing the existing nut behavior.

**Architecture:** A new canonical allergen registry reconciles the three allergen vocabularies in the codebase. New allergens + diets flow through a new registry-driven *generic* scorer path (Dispatch approach); the proven nut code stays byte-identical. Diets are a distinct compatibility concept (not risk) in their own evaluator. Hidden-ingredient knowledge bases are generated offline by a verified LLM fan-out and shipped as data.

**Tech Stack:** Python 3 (stdlib + existing deps: no new runtime deps), pytest, vanilla JS/HTML (`app_template.html`). Design spec: `docs/superpowers/specs/2026-07-01-multi-allergen-and-diets-design.md`.

## Global Constraints

- **Nut gate is byte-identical.** A legacy nuts-only request payload (only `severity`/`crossContact`/`nutTypes`, no `allergens`/`diets` keys) must produce an identical `UserAllergenAssessment` to pre-change `main`. Never edit the nut branch of `_score_one_allergen` or the nut tables/functions in `allergen_prior.py`.
- **Absence ≠ absence.** No layer emits a bare "safe" for allergens; grounded chart/menu presence always wins the precedence ladder over any clean signal; the precautionary floor stays.
- **Over-reporting is conservative** — for allergens (higher risk on ambiguity) and for diets (flag "not compatible"/"unknown" when unsure; never assert compatible without a label or clean evidence).
- **Generated KB is grounded + labeled** — every generated dish/term entry carries `confidence` and `source`; a wrong entry may only over-warn or fall back to cuisine-prior, never lower a real risk.
- **Canonical allergen keys** (registry): `peanut`, `tree_nut`, `milk`, `egg`, `soy`, `gluten`, `wheat`, `fish`, `shellfish`, `mollusc`, `sesame`, `mustard`, `celery`, `sulphites`, `lupin`. The `nuts` super-family (`{peanut, tree_nut}`) and its underscore-plural aliases (`peanuts`, `tree_nuts`) remain owned by the existing nut path.
- **No new runtime dependencies.** The KB-generation fan-out (Task 10) is offline tooling; its output is committed JSON.
- **Run the full suite** (`python -m pytest`) plus `python -m pyflakes safeplate` before every commit; both must be clean.

---

## File Structure

**New files:**
- `safeplate/allergens.py` — canonical allergen + diet registry and vocabulary reconciliation.
- `safeplate/diet_score.py` — diet compatibility evaluator.
- `data/allergen_kb/<allergen>.json` — per-allergen hidden-ingredient dish KB (generated).
- `data/allergen_kb/cuisine_baselines.json` — allergen × cuisine baseline floats (generated).
- `data/allergen_kb/meat_animal.json` — dish/ingredient → animal categories, for diets (generated).
- `tests/test_allergens.py`, `tests/test_allergen_generic_score.py`, `tests/test_diet_score.py`, `tests/test_profile_multi_allergen.py`.
- `eval/bench_multi_allergen.py` — offline multi-allergen + diet eval harness.
- `tools/generate_allergen_kb.workflow.js` — the KB-generation workflow script (offline).

**Modified files:**
- `safeplate/allergen_prior.py` — add generic KB loaders + `restaurant_allergen_risk` + `allergen_cuisine_baseline` (nut tables/functions untouched).
- `safeplate/allergen_score.py` — dispatch in `_score_one_allergen`; add `_score_generic_allergen`, `matrix_covers`, `_split_allergen_terms`; add `UserProfile.diets`.
- `safeplate/allergen_score_llm.py` — generalize the nut-worded prompt + bundle labels.
- `safeplate/common.py` — multi-allergen + diet profile parsing.
- `safeplate/menu_text.py` — expose non-nut term vocab via the registry.
- `safeplate/search_service.py`, `safeplate/menu_service.py` — attach per-allergen + diet results to responses.
- `safeplate/app_template.html` — allergen grid + diet toggles + per-allergen breakdown rendering.

---

## Task 1: Canonical allergen + diet registry

**Files:**
- Create: `safeplate/allergens.py`
- Test: `tests/test_allergens.py`

**Interfaces:**
- Consumes: nothing (leaf module; may import canonical nut constants from `allergen_prior` lazily inside functions to avoid an import cycle, but does not require it).
- Produces:
  - `ALLERGENS: dict[str, AllergenSpec]` keyed by canonical key.
  - `AllergenSpec` dataclass: `key: str`, `display: str`, `matrix_tokens: frozenset[str]`.
  - `DIETS: dict[str, DietSpec]` keyed by diet key (`"vegetarian"`, `"vegan"`).
  - `DietSpec` dataclass: `key: str`, `display: str`, `excluded_allergens: frozenset[str]`, `excluded_categories: frozenset[str]`.
  - `canonical(token: str) -> str | None` — maps any of the three vocab forms (matrix `"tree nut"`, prior `"tree_nuts"`, plain `"treenut"`) to a canonical key, else `None`.
  - `spec_for(key: str) -> AllergenSpec | None`.
  - `all_allergen_keys() -> tuple[str, ...]` — the 15 canonical tokens in stable display order.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_allergens.py
from safeplate import allergens


def test_registry_has_all_eu14_tokens():
    keys = set(allergens.all_allergen_keys())
    assert keys == {
        "peanut", "tree_nut", "milk", "egg", "soy", "gluten", "wheat",
        "fish", "shellfish", "mollusc", "sesame", "mustard", "celery",
        "sulphites", "lupin",
    }


def test_canonical_reconciles_three_vocabularies():
    # matrix space-form, prior underscore-plural, and bare
    assert allergens.canonical("tree nut") == "tree_nut"
    assert allergens.canonical("tree_nuts") == "tree_nut"
    assert allergens.canonical("peanut") == "peanut"
    assert allergens.canonical("peanuts") == "peanut"
    assert allergens.canonical("milk") == "milk"
    assert allergens.canonical("dairy") == "milk"
    assert allergens.canonical("not-an-allergen") is None


def test_spec_carries_matrix_tokens():
    spec = allergens.spec_for("milk")
    assert spec.display == "Milk"
    assert "milk" in spec.matrix_tokens


def test_diet_exclusion_sets():
    vegan = allergens.DIETS["vegan"]
    assert {"milk", "egg", "fish", "shellfish", "mollusc"} <= vegan.excluded_allergens
    assert "meat" in vegan.excluded_categories
    veg = allergens.DIETS["vegetarian"]
    assert {"fish", "shellfish", "mollusc"} <= veg.excluded_allergens
    assert "milk" not in veg.excluded_allergens  # lacto-veg keeps dairy/egg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_allergens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'safeplate.allergens'`.

- [ ] **Step 3: Write minimal implementation**

```python
# safeplate/allergens.py
"""Canonical allergen + diet registry. The single source of truth that reconciles
the three allergen vocabularies in the codebase: the chart parser's space/singular
tokens (allergen_matrix._ALLERGEN_COLUMN_ALIASES, e.g. "tree nut"), the prior
layer's underscore-plural keys (allergen_prior, e.g. "tree_nuts"), and the term
substrings in menu_text.ALLERGEN_TERMS. Downstream code reads keys from here rather
than hardcoding. Nuts keep their existing super-family handling in allergen_prior /
allergen_score; this registry treats peanut and tree_nut as the two atomic nut keys."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AllergenSpec:
    key: str
    display: str
    matrix_tokens: frozenset[str]  # tokens the chart parser may emit for this allergen


@dataclass(frozen=True)
class DietSpec:
    key: str
    display: str
    excluded_allergens: frozenset[str]   # canonical allergen keys whose presence disqualifies
    excluded_categories: frozenset[str]  # non-allergen animal categories (need meat/animal KB)


# canonical key -> (display, matrix tokens, extra alias forms that canonicalize to it)
_DEFS: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    ("peanut",    "Peanut",    ("peanut",),    ("peanuts", "groundnut")),
    ("tree_nut",  "Tree nut",  ("tree nut",),  ("tree_nuts", "treenut", "tree nuts")),
    ("milk",      "Milk",      ("milk",),      ("dairy", "lactose")),
    ("egg",       "Egg",       ("egg",),       ("eggs",)),
    ("soy",       "Soy",       ("soy",),       ("soya", "soybean")),
    ("gluten",    "Gluten",    ("gluten",),    ("cereals",)),
    ("wheat",     "Wheat",     ("wheat",),     ()),
    ("fish",      "Fish",      ("fish",),      ()),
    ("shellfish", "Shellfish", ("shellfish",), ("crustacean", "crustaceans")),
    ("mollusc",   "Mollusc",   ("mollusc",),   ("mollusk", "molluscs")),
    ("sesame",    "Sesame",    ("sesame",),    ()),
    ("mustard",   "Mustard",   ("mustard",),   ()),
    ("celery",    "Celery",    ("celery",),    ()),
    ("sulphites", "Sulphites", ("sulphites",), ("sulphite", "sulfites", "sulfite")),
    ("lupin",     "Lupin",     ("lupin",),     ("lupine",)),
]

ALLERGENS: dict[str, AllergenSpec] = {
    key: AllergenSpec(key=key, display=display, matrix_tokens=frozenset(tokens))
    for key, display, tokens, _aliases in _DEFS
}

# every accepted surface form -> canonical key
_ALIAS_TO_KEY: dict[str, str] = {}
for _key, _display, _tokens, _aliases in _DEFS:
    for _form in (_key, *_tokens, *_aliases):
        _ALIAS_TO_KEY[_form.replace("_", " ").strip().lower()] = _key


DIETS: dict[str, DietSpec] = {
    "vegetarian": DietSpec(
        key="vegetarian", display="Vegetarian",
        excluded_allergens=frozenset({"fish", "shellfish", "mollusc"}),
        excluded_categories=frozenset({"meat", "poultry", "gelatin"}),
    ),
    "vegan": DietSpec(
        key="vegan", display="Vegan",
        excluded_allergens=frozenset({"milk", "egg", "fish", "shellfish", "mollusc"}),
        excluded_categories=frozenset({"meat", "poultry", "gelatin", "honey"}),
    ),
}


def canonical(token: str) -> str | None:
    if not token:
        return None
    return _ALIAS_TO_KEY.get(token.replace("_", " ").strip().lower())


def spec_for(key: str) -> AllergenSpec | None:
    return ALLERGENS.get(key)


def all_allergen_keys() -> tuple[str, ...]:
    return tuple(k for k, *_ in _DEFS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_allergens.py -v && python -m pyflakes safeplate/allergens.py`
Expected: PASS (4 tests), pyflakes clean.

- [ ] **Step 5: Commit**

```bash
git add safeplate/allergens.py tests/test_allergens.py
git commit -m "feat(allergens): canonical allergen + diet registry"
```

---

## Task 2: Generic prior KB layer

**Files:**
- Modify: `safeplate/allergen_prior.py` (append new functions near `restaurant_nut_risk` at :1145; DO NOT edit nut tables/functions)
- Create: `data/allergen_kb/milk.json`, `data/allergen_kb/gluten.json`, `data/allergen_kb/cuisine_baselines.json` (small hand-written seeds; Task 10 grows them)
- Test: `tests/test_allergen_generic_score.py` (prior half)

**Interfaces:**
- Consumes: `RestaurantNutRisk`, `AllergenPrior`, `normalize_cuisine`, `region_from_address`, `labeling_trust_for_region`, `_apply_home_boost`, `clamp_risk` (all existing in `allergen_prior.py`); `safeplate.allergens.spec_for`.
- Produces:
  - `load_allergen_kb(allergen: str) -> list[tuple[str, float, str]]` — `(dish_pattern, risk, note)` entries, cached; `[]` if no file.
  - `allergen_cuisine_baseline(allergen: str, cuisines: list[str] | None, region: str) -> AllergenPrior` — the generic twin of the `CUISINE_NUT_BASELINE` lookup.
  - `restaurant_allergen_risk(*, allergen: str, cuisines: list[str] | None, region: str = "unknown", menu_items: list[dict[str, str]] | None = None, risky_threshold: float = 0.5, baseline: "AllergenPrior | None" = None) -> RestaurantNutRisk` — registry-driven twin of `restaurant_nut_risk`. Reuses the `RestaurantNutRisk` return type. `item_details` entries use `basis="suspected_<allergen>"`.

- [ ] **Step 1: Write the seed data files**

```json
// data/allergen_kb/cuisine_baselines.json
{
  "_default": 0.15,
  "milk":   { "_default": 0.30, "italian": 0.55, "french": 0.55, "indian": 0.50, "american": 0.45 },
  "gluten": { "_default": 0.45, "italian": 0.75, "chinese": 0.55, "american": 0.60, "japanese": 0.55 }
}
```

```json
// data/allergen_kb/milk.json
[
  ["butter chicken", 0.95, "cream/butter based"],
  ["alfredo", 0.95, "cream + parmesan sauce"],
  ["cheese", 0.9, "named dairy ingredient"],
  ["latte", 0.9, "milk-based drink"]
]
```

```json
// data/allergen_kb/gluten.json
[
  ["pasta", 0.9, "wheat pasta unless stated GF"],
  ["soy sauce", 0.85, "usually contains wheat"],
  ["tempura", 0.9, "wheat batter"],
  ["bread", 0.95, "wheat flour"]
]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_allergen_generic_score.py
from safeplate import allergen_prior as ap


def test_cuisine_baseline_reads_generic_table():
    prior = ap.allergen_cuisine_baseline("gluten", ["italian"], "US")
    assert prior.risk >= 0.7  # italian gluten baseline is high
    assert prior.allergen == "gluten"


def test_unknown_allergen_falls_back_to_default_baseline():
    prior = ap.allergen_cuisine_baseline("celery", ["thai"], "US")
    assert 0.0 < prior.risk < 0.3  # no table yet -> low default, never zero/"safe"


def test_restaurant_allergen_risk_flags_known_dish():
    risk = ap.restaurant_allergen_risk(
        allergen="milk",
        cuisines=["italian"],
        region="US",
        menu_items=[{"name": "Fettuccine Alfredo"}, {"name": "Garden Salad"}],
    )
    assert risk.risk >= 0.8
    names = [d["name"] for d in risk.item_details if d["risk"] >= 0.8]
    assert any("alfredo" in n.lower() for n in names)


def test_restaurant_allergen_risk_floor_never_zero():
    risk = ap.restaurant_allergen_risk(
        allergen="mustard", cuisines=["thai"], region="US", menu_items=[{"name": "Plain Rice"}]
    )
    assert risk.risk > 0.0  # absence != safe
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_allergen_generic_score.py -v`
Expected: FAIL with `AttributeError: module 'safeplate.allergen_prior' has no attribute 'allergen_cuisine_baseline'`.

- [ ] **Step 4: Write minimal implementation**

Append to `safeplate/allergen_prior.py` (do not modify existing symbols):

```python
import json as _json
from functools import lru_cache

from safeplate.common import DATA_DIR  # ROOT/data ; safe: common has no import cycle here

_ALLERGEN_KB_DIR = DATA_DIR / "allergen_kb"


@lru_cache(maxsize=None)
def _load_cuisine_baseline_table() -> dict:
    path = _ALLERGEN_KB_DIR / "cuisine_baselines.json"
    if not path.exists():
        return {}
    return _json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def load_allergen_kb(allergen: str) -> tuple[tuple[str, float, str], ...]:
    """(dish_pattern, risk, note) entries for a canonical allergen key; () if none."""
    path = _ALLERGEN_KB_DIR / f"{allergen}.json"
    if not path.exists():
        return ()
    raw = _json.loads(path.read_text(encoding="utf-8"))
    return tuple((str(p).lower(), float(r), str(n)) for p, r, n in raw)


def allergen_cuisine_baseline(
    allergen: str, cuisines: list[str] | None, region: str = "unknown"
) -> "AllergenPrior":
    """Cuisine x location baseline for an arbitrary allergen (generic twin of the
    CUISINE_NUT_BASELINE lookup). Unknown allergen/cuisine -> low, non-zero default."""
    table = _load_cuisine_baseline_table()
    per_allergen = table.get(allergen, {})
    global_default = float(table.get("_default", 0.15))
    base = float(per_allergen.get("_default", global_default))
    norm = normalize_cuisine(cuisines)
    for cuisine in norm:
        if cuisine in per_allergen:
            base = max(base, float(per_allergen[cuisine]))
    trust = labeling_trust_for_region(region)
    risk = _apply_home_boost(base, norm, region, weight=0.25)
    basis = "cuisine_baseline" if per_allergen else "default"
    return AllergenPrior(
        allergen=allergen, risk=clamp_risk(risk), confidence=0.5,
        basis=basis, note=f"{allergen} cuisine baseline", labeling_trust=trust,
    )


def restaurant_allergen_risk(
    *,
    allergen: str,
    cuisines: list[str] | None,
    region: str = "unknown",
    menu_items: list[dict[str, str]] | None = None,
    risky_threshold: float = 0.5,
    baseline: "AllergenPrior | None" = None,
) -> RestaurantNutRisk:
    """Combine the cuisine/location baseline (floor) with per-dish KB matches for an
    arbitrary allergen. Mirrors restaurant_nut_risk's contract + return type."""
    base = baseline or allergen_cuisine_baseline(allergen, cuisines, region)
    kb = load_allergen_kb(allergen)
    risk = base.risk
    rationale = [base.note]
    details: list[dict[str, Any]] = []
    riskiest: list[tuple[str, float]] = []
    for item in menu_items or []:
        name = str(item.get("name") or "")
        low = name.lower()
        best = 0.0
        note = ""
        for pattern, dish_risk, dish_note in kb:
            if pattern in low and dish_risk > best:
                best, note = dish_risk, dish_note
        if best > 0.0:
            boosted = clamp_risk(_apply_home_boost(best, normalize_cuisine(cuisines), region, weight=0.10))
            details.append({"name": name, "risk": boosted, "confidence": 0.6,
                            "basis": f"suspected_{allergen}", "note": note})
            riskiest.append((name, boosted))
            risk = max(risk, boosted)
    riskiest.sort(key=lambda t: t[1], reverse=True)
    confidence = 0.6 if details else base.confidence
    return RestaurantNutRisk(
        risk=clamp_risk(risk), confidence=confidence, rationale=rationale,
        labeling_trust=base.labeling_trust, riskiest_items=riskiest[:5], item_details=details,
    )
```

Note: if importing `DATA_DIR` from `safeplate.common` creates a cycle at run time, instead define `_ALLERGEN_KB_DIR` locally: `from pathlib import Path` + `Path(__file__).resolve().parents[1] / "data" / "allergen_kb"`. Verify with the test run.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_allergen_generic_score.py -v && python -m pyflakes safeplate/allergen_prior.py`
Expected: PASS (4 tests), pyflakes clean.

- [ ] **Step 6: Run the FULL suite (nut-gate check)**

Run: `python -m pytest`
Expected: PASS — no existing test regressed (nut tables untouched).

- [ ] **Step 7: Commit**

```bash
git add safeplate/allergen_prior.py data/allergen_kb tests/test_allergen_generic_score.py
git commit -m "feat(prior): generic per-allergen cuisine baseline + dish risk"
```

---

## Task 3: Generic scorer dispatch + nut regression guard

**Files:**
- Modify: `safeplate/allergen_score.py` — add `matrix_covers`, `_split_allergen_terms`, `_score_generic_allergen`; add the dispatch branch at the top of `_score_one_allergen` (:595). Add `diets: frozenset[str] = frozenset()` field to `UserProfile` (:109).
- Test: `tests/test_allergen_generic_score.py` (scorer half) + a nut-regression test.

**Interfaces:**
- Consumes: `safeplate.allergens.canonical`, `restaurant_allergen_risk` (Task 2), existing `_tier_for`, `_apply_community`, `_SEVERITY_TUNING`, `Tier`, `AllergenAssessment`, `MenuItemRecord.allergen_terms`.
- Produces:
  - `matrix_covers(allergen: str, terms: list[str]) -> bool` — any term canonicalizes to `allergen`.
  - `_split_allergen_terms(allergen: str, terms: list[str]) -> tuple[list[str], list[str]]` — `(contains, cross_contact)` for one canonical allergen (parallels `_split_nut_terms` but for non-nuts).
  - `_score_generic_allergen(pref, *, cuisines, region, menu_items, signals, community) -> AllergenAssessment` — same return type as the nut path.
  - `UserProfile.diets` field (unused by the scorer here; read by the diet evaluator in Task 4).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_allergen_generic_score.py  (append)
from safeplate.allergen_score import (
    AllergenPref, Severity, UserProfile, score_restaurant_for_user, matrix_covers,
)
from safeplate.menu_text import MenuItemRecord


def test_matrix_covers_canonicalizes():
    assert matrix_covers("milk", ["Milk", "Egg"]) is True
    assert matrix_covers("tree_nut", ["tree nut"]) is True
    assert matrix_covers("milk", ["Gluten"]) is False


def test_generic_allergen_chart_hit_avoids():
    profile = UserProfile(allergens=(AllergenPref(allergen="milk", severity=Severity.ALLERGY),))
    items = [MenuItemRecord(name="Cheese Pizza", allergen_terms=["milk", "gluten"],
                            extraction_method="allergen_matrix")]
    a = score_restaurant_for_user(profile, cuisines=["italian"], region="US", menu_items=items)
    assert a.tier == "avoid"
    assert a.per_allergen[0].allergen == "milk"


def test_generic_allergen_no_evidence_caps_at_caution():
    profile = UserProfile(allergens=(AllergenPref(allergen="milk", severity=Severity.ANAPHYLAXIS),))
    a = score_restaurant_for_user(profile, cuisines=["italian"], region="US", menu_items=[])
    assert a.tier in ("caution", "likely_ok")  # prior alone never grounds AVOID
    assert a.tier != "avoid"


def test_nut_profile_byte_identical(monkeypatch):
    # A nuts-only profile must route to the untouched nut path and score as before.
    profile = UserProfile.for_nuts(Severity.ANAPHYLAXIS)
    items = [MenuItemRecord(name="Pad Thai", allergen_terms=["peanut"],
                            extraction_method="allergen_matrix")]
    a = score_restaurant_for_user(profile, cuisines=["thai"], region="US", menu_items=items)
    assert a.tier == "avoid"
    assert a.per_allergen[0].allergen == "nuts"
```

(If `MenuItemRecord` constructor kwargs differ, match its real signature from `menu_text.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_allergen_generic_score.py -k "matrix_covers or generic or byte_identical" -v`
Expected: FAIL with `ImportError: cannot import name 'matrix_covers'`.

- [ ] **Step 3: Add the `UserProfile.diets` field**

In `allergen_score.py`, the `UserProfile` dataclass (:109):

```python
@dataclass(frozen=True)
class UserProfile:
    allergens: tuple[AllergenPref, ...] = ()
    diets: frozenset[str] = frozenset()   # NEW: canonical diet keys, e.g. {"vegan"}
    # ... existing for_nuts classmethod unchanged ...
```

- [ ] **Step 4: Add the generic recognition helpers + evaluator**

```python
# allergen_score.py
from safeplate.allergens import canonical as _canonical
from safeplate.allergen_prior import restaurant_allergen_risk


def matrix_covers(allergen: str, terms: list[str]) -> bool:
    return any(_canonical(t) == allergen for t in (terms or []))


def _split_allergen_terms(allergen: str, terms: list[str]) -> tuple[list[str], list[str]]:
    """(contains, cross_contact) for one canonical allergen. 'may contain'/'traces'
    prefixed terms are cross-contact; the rest that canonicalize to the allergen are
    contains."""
    contains, cross = [], []
    for raw in terms or []:
        low = str(raw).lower()
        is_cc = any(m in low for m in ("may contain", "traces", "trace of", "cross"))
        core = low.replace("may contain", "").replace("traces", "").strip(" :-")
        if _canonical(core) == allergen or _canonical(low) == allergen:
            (cross if is_cc else contains).append(raw)
    return contains, cross
```

`_score_generic_allergen` reuses the existing fusion helpers. Model it on the nut branch of `_score_one_allergen`, but: prior via `restaurant_allergen_risk(allergen=pref.allergen, ...)`; grounded presence via `matrix_covers`/`_split_allergen_terms` over each `MenuItemRecord.allergen_terms`; tier via the existing `_tier_for`; community via the existing `_apply_community`. Follow the same T1(matrix)→T2(text)→prior→community precedence and the same clean-signal floor rules. Return an `AllergenAssessment` with `allergen=pref.allergen`, `rationale` naming the allergen (no `" nuts"` strings).

- [ ] **Step 5: Add the dispatch branch**

At the very top of `_score_one_allergen` (:595), before the nut-specific body:

```python
def _score_one_allergen(pref, *, cuisines, region, menu_items, signals, community):
    from safeplate.allergen_prior import NUTS, PEANUTS, TREE_NUTS
    if pref.allergen not in (NUTS, PEANUTS, TREE_NUTS):
        return _score_generic_allergen(
            pref, cuisines=cuisines, region=region,
            menu_items=menu_items, signals=signals, community=community,
        )
    # ---- existing nut body unchanged below ----
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_allergen_generic_score.py -v`
Expected: PASS.

- [ ] **Step 7: Run the FULL suite + the dedicated nut-gate regression**

Run: `python -m pytest`
Expected: PASS — all pre-existing nut/score tests unchanged; `test_nut_profile_byte_identical` green.

- [ ] **Step 8: Commit**

```bash
git add safeplate/allergen_score.py tests/test_allergen_generic_score.py
git commit -m "feat(score): dispatch generic allergen path; nuts unchanged"
```

---

## Task 4: Diet compatibility evaluator

**Files:**
- Create: `safeplate/diet_score.py`
- Create: `data/allergen_kb/meat_animal.json` (seed)
- Test: `tests/test_diet_score.py`

**Interfaces:**
- Consumes: `safeplate.allergens.DIETS`, `safeplate.allergens.canonical`; `MenuItemRecord` (has `name`, `allergen_terms`).
- Produces:
  - `DietAssessment` dataclass: `diet: str`, `verdict: str` (`"not_compatible" | "limited" | "good_options" | "unknown"`), `support: float`, `rationale: list[str]`, `offending_items: list[str]`, `compatible_items: list[str]`.
  - `assess_diet(diet: str, *, menu_items: list, cuisines: list[str] | None = None) -> DietAssessment`.
  - `assess_diets(diets: frozenset[str], *, menu_items, cuisines=None) -> list[DietAssessment]`.

- [ ] **Step 1: Write the seed data**

```json
// data/allergen_kb/meat_animal.json
{
  "meat":    ["beef", "pork", "chicken", "lamb", "bacon", "steak", "sausage", "ham", "meatball"],
  "poultry": ["chicken", "turkey", "duck"],
  "gelatin": ["gelatin", "gelatine"],
  "honey":   ["honey"]
}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_diet_score.py
from safeplate.diet_score import assess_diet
from safeplate.menu_text import MenuItemRecord


def test_vegan_flags_dairy_chart_hit():
    items = [MenuItemRecord(name="Cheese Pizza", allergen_terms=["milk"],
                            extraction_method="allergen_matrix")]
    a = assess_diet("vegan", menu_items=items)
    assert a.verdict == "not_compatible"
    assert "Cheese Pizza" in a.offending_items


def test_vegan_flags_meat_by_name():
    items = [MenuItemRecord(name="Beef Burger", allergen_terms=[], extraction_method="listed")]
    a = assess_diet("vegan", menu_items=items)
    assert a.verdict == "not_compatible"


def test_vegetarian_allows_dairy():
    items = [MenuItemRecord(name="Margherita Pizza", allergen_terms=["milk", "gluten"],
                            extraction_method="allergen_matrix")]
    a = assess_diet("vegetarian", menu_items=items)
    assert a.verdict in ("good_options", "limited")  # dairy is fine for lacto-veg
    assert "Margherita Pizza" in a.compatible_items


def test_empty_menu_is_unknown_not_good():
    a = assess_diet("vegan", menu_items=[])
    assert a.verdict == "unknown"  # never assume compatible with no evidence
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_diet_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'safeplate.diet_score'`.

- [ ] **Step 4: Write minimal implementation**

```python
# safeplate/diet_score.py
"""Diet compatibility (vegetarian/vegan). A distinct concept from allergen RISK:
ingredient membership, no severity/cross-contact. Asymmetry: unknown/unlabeled
dishes are NOT assumed compatible; an empty/unknown menu yields 'unknown', never
'good_options'."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from safeplate.allergens import DIETS, canonical

_MEAT_KB_PATH = Path(__file__).resolve().parents[1] / "data" / "allergen_kb" / "meat_animal.json"


@lru_cache(maxsize=None)
def _meat_terms() -> dict[str, tuple[str, ...]]:
    if not _MEAT_KB_PATH.exists():
        return {}
    raw = json.loads(_MEAT_KB_PATH.read_text(encoding="utf-8"))
    return {cat: tuple(t.lower() for t in terms) for cat, terms in raw.items()}


@dataclass(frozen=True)
class DietAssessment:
    diet: str
    verdict: str            # not_compatible | limited | good_options | unknown
    support: float
    rationale: list[str] = field(default_factory=list)
    offending_items: list[str] = field(default_factory=list)
    compatible_items: list[str] = field(default_factory=list)


def _item_conflicts(spec, name_low: str, terms: list[str]) -> bool:
    if any(canonical(t) in spec.excluded_allergens for t in (terms or [])):
        return True
    meat = _meat_terms()
    for cat in spec.excluded_categories:
        if any(term in name_low for term in meat.get(cat, ())):
            return True
    return False


def assess_diet(diet: str, *, menu_items: list, cuisines: list[str] | None = None) -> DietAssessment:
    spec = DIETS.get(diet)
    if spec is None:
        return DietAssessment(diet=diet, verdict="unknown", support=0.0,
                              rationale=[f"unknown diet {diet!r}"])
    items = menu_items or []
    if not items:
        return DietAssessment(diet=diet, verdict="unknown", support=0.0,
                              rationale=["no menu evidence"])
    offending, compatible = [], []
    for it in items:
        name = str(getattr(it, "name", "") or "")
        terms = list(getattr(it, "allergen_terms", []) or [])
        if _item_conflicts(spec, name.lower(), terms):
            offending.append(name)
        else:
            compatible.append(name)
    share = len(compatible) / len(items)
    if not compatible:
        verdict = "not_compatible"
    elif share >= 0.4:
        verdict = "good_options"
    else:
        verdict = "limited"
    rationale = [f"{len(compatible)}/{len(items)} menu items appear {spec.display.lower()}-compatible"]
    if offending:
        rationale.append(f"{len(offending)} contain excluded ingredients (e.g. {offending[0]})")
    return DietAssessment(diet=diet, verdict=verdict, support=round(share, 2),
                          rationale=rationale, offending_items=offending[:10],
                          compatible_items=compatible[:10])


def assess_diets(diets, *, menu_items, cuisines=None) -> list[DietAssessment]:
    return [assess_diet(d, menu_items=menu_items, cuisines=cuisines) for d in sorted(diets)]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_diet_score.py -v && python -m pyflakes safeplate/diet_score.py`
Expected: PASS (4 tests), pyflakes clean.

- [ ] **Step 6: Commit**

```bash
git add safeplate/diet_score.py data/allergen_kb/meat_animal.json tests/test_diet_score.py
git commit -m "feat(diet): vegetarian/vegan compatibility evaluator"
```

---

## Task 5: Multi-allergen + diet profile parsing

**Files:**
- Modify: `safeplate/common.py` — `_user_profile_from_payload` (:71-84) + a new `_diets_from_payload`.
- Test: `tests/test_profile_multi_allergen.py`

**Interfaces:**
- Consumes: `UserProfile`, `AllergenPref`, `Severity` (existing); `safeplate.allergens.canonical`, `DIETS`; existing `_severity_from_str`, `_cross_contact_from_str`, `normalize_nut_types`.
- Produces: `_user_profile_from_payload(payload)` now returns a `UserProfile` with an `allergens` tuple built from `payload["allergens"]` (list of `{allergen, severity, crossContact}`) plus `diets` from `payload["diets"]`; **legacy fallback** to `for_nuts(...)` when `allergens` is absent. Gluten-free diet key expands to a `gluten` `AllergenPref`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profile_multi_allergen.py
from safeplate.common import _user_profile_from_payload
from safeplate.allergen_score import Severity


def test_legacy_payload_still_nuts():
    p = _user_profile_from_payload({"severity": "anaphylaxis", "nutTypes": []})
    assert len(p.allergens) == 1
    assert p.allergens[0].allergen == "nuts"
    assert p.allergens[0].severity == Severity.ANAPHYLAXIS
    assert p.diets == frozenset()


def test_multi_allergen_payload():
    p = _user_profile_from_payload({"allergens": [
        {"allergen": "milk", "severity": "allergy"},
        {"allergen": "gluten", "severity": "intolerance"},
    ]})
    keys = {a.allergen for a in p.allergens}
    assert keys == {"milk", "gluten"}


def test_gluten_free_diet_expands_to_gluten_allergen():
    p = _user_profile_from_payload({"diets": ["gluten_free", "vegan"]})
    assert any(a.allergen == "gluten" for a in p.allergens)
    assert p.diets == frozenset({"vegan"})  # gluten_free consumed into an allergen


def test_diet_flags_parsed():
    p = _user_profile_from_payload({"allergens": [{"allergen": "milk", "severity": "allergy"}],
                                    "diets": ["vegetarian"]})
    assert p.diets == frozenset({"vegetarian"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_profile_multi_allergen.py -v`
Expected: FAIL (`test_multi_allergen_payload` etc. — current code ignores `allergens`).

- [ ] **Step 3: Write the implementation**

Replace `_user_profile_from_payload` in `common.py`:

```python
def _diets_from_payload(payload: dict[str, Any]) -> tuple[frozenset, bool]:
    """Returns (real_diet_keys, gluten_free_requested). gluten_free is NOT a diet;
    it is consumed into a gluten allergen by the caller."""
    from safeplate.allergens import DIETS
    raw = payload.get("diets") or []
    diets = {str(d).lower() for d in raw}
    gf = "gluten_free" in diets
    return frozenset(d for d in diets if d in DIETS), gf


def _user_profile_from_payload(payload: dict[str, Any]):
    from safeplate.allergen_prior import normalize_nut_types
    from safeplate.allergen_score import AllergenPref, UserProfile
    from safeplate.allergens import canonical

    diets, gluten_free = _diets_from_payload(payload)
    raw_allergens = payload.get("allergens")

    if not raw_allergens and not diets and not gluten_free:
        # legacy nuts-only path -- byte-identical to before
        return UserProfile.for_nuts(
            _severity_from_str(payload.get("severity")),
            cross_contact=_cross_contact_from_str(payload.get("crossContact")),
            nut_types=normalize_nut_types(payload.get("nutTypes")),
        )

    prefs = []
    for entry in raw_allergens or []:
        key = canonical(str(entry.get("allergen", "")))
        if key is None:
            # allow "nuts" family key through untouched
            if str(entry.get("allergen", "")).lower() in ("nuts", "peanuts", "tree_nuts"):
                key = str(entry.get("allergen")).lower()
            else:
                continue
        prefs.append(AllergenPref(
            allergen=key,
            severity=_severity_from_str(entry.get("severity")),
            cross_contact=_cross_contact_from_str(entry.get("crossContact")),
            nut_types=normalize_nut_types(entry.get("nutTypes")) if key == "nuts" else None,
        ))
    if gluten_free and not any(p.allergen == "gluten" for p in prefs):
        from safeplate.allergen_score import Severity
        prefs.append(AllergenPref(allergen="gluten", severity=Severity.ALLERGY))
    return UserProfile(allergens=tuple(prefs), diets=diets)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_profile_multi_allergen.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the FULL suite**

Run: `python -m pytest && python -m pyflakes safeplate/common.py`
Expected: PASS — legacy profile tests unchanged.

- [ ] **Step 6: Commit**

```bash
git add safeplate/common.py tests/test_profile_multi_allergen.py
git commit -m "feat(profile): parse multi-allergen list + diet flags (legacy-compatible)"
```

---

## Task 6: Attach diet + per-allergen results to API responses

**Files:**
- Modify: `safeplate/menu_service.py` (near the profile use at ~:264) and `safeplate/search_service.py` (~:394) — call `assess_diets` with the profile's `diets` and the extracted menu items; add the result to the response payload under `summary.diets` (menu) and each row (search).
- Test: extend `tests/test_diet_score.py` or add a service-level test if the services already have one; otherwise a focused unit test on the response shaper.

**Interfaces:**
- Consumes: `safeplate.diet_score.assess_diets`, `_user_profile_from_payload`'s `UserProfile.diets`, the already-extracted `menu_items`.
- Produces: response JSON gains `summary.diets: [{diet, verdict, support, rationale, offendingItems}]` on `/api/menu`; each `/api/search` row gains `diet` summary if diets are selected (may be `unknown` before menu extraction — that's honest).

- [ ] **Step 1: Locate the menu response assembly**

Run: `python -m pytest tests/test_diet_score.py -v` (baseline green) then read `menu_service.py` around where `summary` is built and `_user_profile_from_payload` is called.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_diet_score.py  (append) -- shape test on a helper
from safeplate.menu_service import _diet_summary_payload  # new tiny helper
from safeplate.menu_text import MenuItemRecord


def test_diet_summary_payload_shape():
    items = [MenuItemRecord(name="Beef Burger", allergen_terms=[], extraction_method="listed")]
    out = _diet_summary_payload(frozenset({"vegan"}), items, cuisines=["american"])
    assert out[0]["diet"] == "vegan"
    assert out[0]["verdict"] == "not_compatible"
    assert "offendingItems" in out[0]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_diet_score.py::test_diet_summary_payload_shape -v`
Expected: FAIL with `ImportError: cannot import name '_diet_summary_payload'`.

- [ ] **Step 4: Add the helper + wire it in**

```python
# menu_service.py
def _diet_summary_payload(diets, menu_items, *, cuisines=None) -> list[dict]:
    from safeplate.diet_score import assess_diets
    out = []
    for a in assess_diets(diets, menu_items=menu_items, cuisines=cuisines):
        out.append({"diet": a.diet, "verdict": a.verdict, "support": a.support,
                    "rationale": a.rationale, "offendingItems": a.offending_items,
                    "compatibleItems": a.compatible_items})
    return out
```

Then, where the menu `summary` dict is assembled, add (only when the profile has diets):

```python
if profile.diets:
    summary["diets"] = _diet_summary_payload(profile.diets, menu_items, cuisines=cuisines)
```

Mirror a minimal version in `search_service.py` per-row (verdict + diet only), using the row's cuisines and whatever items are available (often none pre-extraction → `unknown`).

- [ ] **Step 5: Run tests + full suite**

Run: `python -m pytest && python -m pyflakes safeplate/menu_service.py safeplate/search_service.py`
Expected: PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add safeplate/menu_service.py safeplate/search_service.py tests/test_diet_score.py
git commit -m "feat(api): attach diet compatibility to menu/search responses"
```

---

## Task 7: Generalize the LLM scorer prompt

**Files:**
- Modify: `safeplate/allergen_score_llm.py` — the nut-worded system prompt (~:59-101, :127-133) and `_build_bundle` labels (`bundle["user"]["nuts"]` ~:442, chart-summary labels ~:456-463, handling label ~:427), and the `_apply_guardrails` fallback `allergen=NUTS` (~:542).
- Test: extend `tests/test_allergen_score_llm.py` (mocked LLM) with a non-nut allergen.

**Interfaces:**
- Consumes: the profile's per-allergen list (already available); `safeplate.allergens.spec_for` for display names.
- Produces: prompt + bundle name the user's *actual* allergens (e.g. "milk", "gluten") instead of "nuts"; guardrail fallback uses the first profile allergen key, not a hardcoded `NUTS`. Output shape unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_allergen_score_llm.py  (append)
def test_llm_prompt_names_actual_allergen(monkeypatch):
    from safeplate import allergen_score_llm as m
    captured = {}
    def fake_call(bundle, *, api_key, model):
        captured["bundle"] = bundle
        return {"risk": 0.5, "tier": "caution", "confidence": 0.6, "rationale": []}
    monkeypatch.setattr(m, "_call_llm_scorer", fake_call)
    from safeplate.allergen_score import AllergenPref, Severity, UserProfile
    profile = UserProfile(allergens=(AllergenPref(allergen="milk", severity=Severity.ALLERGY),))
    m.score_restaurant_with_llm(profile, cuisines=["italian"], region="US",
                                menu_items=[], api_key="x", model="y")
    assert "milk" in str(captured["bundle"]).lower()
    assert "nuts" not in str(captured["bundle"]["user"]).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_allergen_score_llm.py::test_llm_prompt_names_actual_allergen -v`
Expected: FAIL (bundle still says "nuts").

- [ ] **Step 3: Implement**

- Change `bundle["user"]["nuts"]` to `bundle["user"]["allergens"] = [p.allergen for p in profile.allergens]`.
- In the system prompt string, replace nut-specific wording ("are there any nuts?", "high-nut cuisine", the "korma/satay/pesto/baklava" examples, "nut-free claim") with allergen-neutral wording that references `{allergen_names}` interpolated from the profile (`", ".join(spec_for(k).display if spec_for(k) else k for k in keys)`).
- In `_apply_guardrails`, change the fallback `allergen=NUTS` to the first profile allergen key (thread it in).

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_allergen_score_llm.py -v && python -m pytest`
Expected: PASS — existing LLM-scorer tests still green (they use nut profiles, which still work).

- [ ] **Step 5: Commit**

```bash
git add safeplate/allergen_score_llm.py tests/test_allergen_score_llm.py
git commit -m "feat(llm-score): generalize prompt/bundle beyond nuts"
```

---

## Task 8: UI — allergen grid + diet toggles + state migration

**Files:**
- Modify: `safeplate/app_template.html` — onboarding modal `#onboard` (~:1167-1218): replace/augment the nut-chip block with a 14-allergen grid + diet-toggle row; `state` (:1239), `saveProfile`/`loadProfile` (:1344-1360), payload builders (`/api/search` ~:1535, `/api/menu` ~:1588/:1933).
- Test: manual (browser) + a JS-free assertion is not feasible; verify via the run skill after Task 9.

**Interfaces:**
- Consumes: the new payload contract (`allergens: [{allergen, severity, crossContact}]`, `diets: [...]`).
- Produces: `state.allergens` (array), `state.diets` (array); payloads send them; old localStorage migrates.

- [ ] **Step 1: Extend `state` + migration in `loadProfile`**

```javascript
// state (~:1239) add:
allergens: [],   // [{allergen, severity, crossContact}]
diets: [],       // ["vegan", ...]

// loadProfile (~:1344): after parsing stored JSON, migrate legacy shape
if (stored && !stored.allergens) {
  stored.allergens = [{ allergen: "nuts", severity: stored.severity || "allergy",
                        crossContact: stored.crossContact || "", nutTypes: stored.nutTypes || [] }];
  stored.diets = stored.diets || [];
}
```

- [ ] **Step 2: Build the allergen grid markup**

In `#onboard`, add a grid of chips for `all_allergen_keys()` (hardcode the 15 display labels + a "Nuts" umbrella that maps to the existing per-nut selector). Each chip toggles membership in `state.allergens`; an expanded severity selector (reuse the `.allergen-chip.sev` pattern) sets that allergen's severity. Add a diet row with three chips: `Gluten-free` (`gluten_free`), `Vegetarian` (`vegetarian`), `Vegan` (`vegan`) toggling `state.diets`. Unlock `.allergen-chip.locked`/`.soon` styles by simply not applying `locked`.

- [ ] **Step 3: Send the new keys in both payloads**

```javascript
// in the /api/search and /api/menu body objects, add:
allergens: state.allergens,
diets: state.diets,
```

Keep `severity`/`crossContact`/`nutTypes` for the nuts entry so the server's legacy fallback still applies when `allergens` is empty.

- [ ] **Step 4: Persist in `saveProfile`**

```javascript
localStorage.setItem("safeplate.profile", JSON.stringify({
  severity: state.severity, crossContact: state.crossContact, nutTypes: state.nutTypes,
  allergens: state.allergens, diets: state.diets,
}));
```

- [ ] **Step 5: Manual smoke (defer full check to Task 9)**

Run the app (`/run` skill or the documented dev command), open onboarding, select Milk + Gluten + Vegan, confirm the network request body carries `allergens` + `diets`.

- [ ] **Step 6: Commit**

```bash
git add safeplate/app_template.html
git commit -m "feat(ui): allergen grid + diet toggles + legacy profile migration"
```

---

## Task 9: UI — per-allergen breakdown + diet badge + de-hardcode "for nuts"

**Files:**
- Modify: `safeplate/app_template.html` — drawer/verdict rendering (`verdictHtml` ~:1875-1896, the `" for nuts"` strings at ~:1735/:1789/:1875), `renderMenu` (~:1963-2132), card markup (`cardMarkup` ~:1675-1706).

**Interfaces:**
- Consumes: response `summary.diets`, and `per_allergen` breakdown if surfaced (else the existing single-tier summary). If the backend does not yet expose `per_allergen` in the JSON, add it in `menu_service.py` alongside Task 6 (map `assessment.per_allergen` → `[{allergen, tier, risk, rationale}]`).

- [ ] **Step 1: Expose `per_allergen` in the menu response**

In `menu_service.py`, where `summary.menuBackedRisk` is built, add:
```python
summary["perAllergen"] = [
    {"allergen": pa.allergen, "tier": pa.tier, "risk": round(pa.risk, 2),
     "rationale": pa.rationale} for pa in assessment.per_allergen
]
```

- [ ] **Step 2: Replace hardcoded `" for nuts"`**

Find each `" for nuts"` occurrence and interpolate the allergen label(s) from the profile/response. For the single-allergen case use its display; for multi, use "for your allergens".

- [ ] **Step 3: Render the per-allergen breakdown**

In the "Why this score" drawer, iterate `summary.perAllergen` and render one row per allergen (display name + tier badge + rationale), reusing the existing `.why-list`/badge components.

- [ ] **Step 4: Render the diet badge**

For each entry in `summary.diets`, render a badge on the card/drawer with diet-appropriate wording: `not_compatible`→"Not vegan-friendly", `limited`→"Few vegan options", `good_options`→"Vegan options", `unknown`→"Vegan: ask staff". Never color-only — include text + icon (WCAG safety rule per PRODUCT.md).

- [ ] **Step 5: Manual verification via the run skill**

Launch the app, search a chain with an allergen matrix (e.g. Wagamama), select Milk + Gluten + Vegan, open a restaurant, and confirm: per-allergen rows appear, diet badge appears, and no "for nuts" text remains for a non-nut profile.

- [ ] **Step 6: Commit**

```bash
git add safeplate/app_template.html safeplate/menu_service.py
git commit -m "feat(ui): per-allergen breakdown + diet badge; drop nut-only copy"
```

---

## Task 10: Generate the hidden-ingredient KBs (verified LLM fan-out)

**Files:**
- Create: `tools/generate_allergen_kb.workflow.js` (offline Workflow script)
- Modify (data): `data/allergen_kb/<allergen>.json` (all 15), `data/allergen_kb/cuisine_baselines.json`, `data/allergen_kb/meat_animal.json`, plus multilingual term additions.

**Interfaces:**
- Consumes: `safeplate.allergens.all_allergen_keys`, the existing cuisine list from `allergen_prior.CUISINE_ALIASES`.
- Produces: expanded, verified JSON KBs in the same shapes Task 2/4 already read (`[[pattern, risk, note], ...]`, baseline dict, meat dict). Each entry additionally carries `confidence` + `source` (the loaders ignore unknown trailing fields, or extend them to read 4-tuples — keep the 3-tuple public contract, store provenance in a sibling `*.meta.json` if needed to avoid changing the loader).

- [ ] **Step 1: Write the workflow script** (run with the Workflow tool, offline)

Draft stage: one agent per (allergen × cuisine-family) drafts `(dish_pattern, risk 0-1, note)` hidden-ingredient entries — dishes where the allergen is present but not obvious from the name. Verify stage: a separate adversarial agent per candidate confirms it is a real, well-known culinary fact (not a guess), assigns confidence, and drops rejects. Pipeline the two stages. Aggregate by allergen, dedupe by pattern (keep max risk), and emit JSON.

- [ ] **Step 2: Run the workflow** (opt-in, offline; requires model budget)

The output is data only. Review a sample of entries by hand before committing (spot-check 10 per allergen for plausibility).

- [ ] **Step 3: Validate the generated data loads**

Run: `python -c "from safeplate import allergen_prior as ap; print({k: len(ap.load_allergen_kb(k)) for k in __import__('safeplate.allergens', fromlist=['x']).all_allergen_keys()})"`
Expected: non-zero counts for the major allergens; loaders don't raise.

- [ ] **Step 4: Run the full suite** (data-driven tests still pass; add a couple asserting a known generated dish flags)

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/generate_allergen_kb.workflow.js data/allergen_kb
git commit -m "data(kb): seed verified hidden-ingredient KBs for all allergens + diets"
```

---

## Task 11: Multi-allergen + diet eval harness (quality gate)

**Files:**
- Create: `eval/bench_multi_allergen.py`

**Interfaces:**
- Consumes: frozen chart snapshots (reuse the `data/bench_snapshots/` pattern from `scripts/bench_extraction.py` if present), `score_restaurant_for_user`, `assess_diet`.
- Produces: an offline, re-runnable report of per-allergen tier distributions + diet verdicts over the snapshots, plus a nut-parity check (nuts scores unchanged vs a saved baseline).

- [ ] **Step 1: Write the harness**

Load frozen menu/chart snapshots, score each for a matrix of allergens (milk, gluten, egg, sesame) + diets (vegan, vegetarian), print a summary table (how many AVOID/CAUTION/LIKELY_OK per allergen; diet verdict counts). Assert nuts output equals a stored baseline JSON (regenerate baseline with `--update-baseline`).

- [ ] **Step 2: Run it**

Run: `python eval/bench_multi_allergen.py`
Expected: prints the table; nut-parity assertion passes.

- [ ] **Step 3: Commit**

```bash
git add eval/bench_multi_allergen.py
git commit -m "test(eval): multi-allergen + diet bench with nut-parity guard"
```

---

## Self-Review notes

- **Spec coverage:** §1 registry→T1; §2 generic prior→T2; §3 generic scorer→T3; §4 diet evaluator→T4; §5 profile/payload→T5; API attach→T6; LLM prompt→T7; §6 UI→T8+T9; §7 content fan-out→T10; §8 gate→T3 (nut regression), T11 (bench), plus live smoke in T9. Gluten-free-as-allergen (decision 5) handled in T5. All spec sections have a task.
- **Nut-gate protection:** T2/T3 never edit nut tables/functions; the dispatch branch (T3 Step 5) routes nuts to the untouched body; `test_nut_profile_byte_identical` (T3) + `bench_multi_allergen` parity (T11) assert it.
- **Type consistency:** `restaurant_allergen_risk` returns the existing `RestaurantNutRisk` type (T2) consumed by the scorer (T3); `DietAssessment` fields (T4) match `_diet_summary_payload` keys (T6) and the UI badge reads (T9); `UserProfile.diets` added in T3, read in T5/T6.
- **Known follow-up (not a blocker):** `_split_allergen_terms` cross-contact detection (T3) is a simple prefix check; the multilingual term expansion for non-nut allergens lands with the fan-out data (T10), not code.
