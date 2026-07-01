# Multi-allergen + diets — design

**Date:** 2026-07-01
**Status:** Approved (design), pending implementation plan
**Scope:** Extend SafePlate from nuts-only scoring to all EU-14 allergens plus
gluten-free / vegetarian / vegan diet modes.

## Goal

Today SafePlate ranks nearby restaurants by **nut** risk end-to-end. The evidence
layer (allergen-chart parser) already reads the full EU-14 token set, and the
scoring math is allergen-agnostic in shape, but the prior knowledge bases,
evidence recognition, user profile, and UI are all hard-wired to nuts. Diets have
no representation at all.

This project makes the product multi-allergen: the EU-14 major allergens — as the
chart tokens the parser emits: peanut, tree nut, milk, egg, soy, gluten, wheat,
fish, shellfish/crustacean, mollusc, sesame, mustard, celery, sulphites, lupin
(gluten + wheat are two chart columns of the one EU "cereals containing gluten"
category) — plus vegetarian and vegan diet modes and a gluten-free mode. Per
decision 5, gluten-free is implemented as the gluten/wheat *allergen* surfaced as
a friendly toggle; only vegetarian/vegan are true *diets*.

## Locked decisions

1. **Scope** — full EU-14 allergens + gluten-free + vegetarian + vegan.
2. **Diet model** — diets are a *distinct concept* from allergens: ingredient
   membership, not risk. No severity / cross-contact. They reuse the evidence
   pipeline and a unified card, but produce a compatibility verdict, not a risk
   tier.
3. **Content depth** — seed hidden-ingredient dish knowledge for every allergen
   (and a meat/animal taxonomy for diets) via an **offline, verified LLM
   fan-out**. Safe by construction: a KB entry only feeds the prior *floor*, so a
   wrong entry either over-warns (the conservative direction) or falls back to the
   cuisine baseline; it can never talk a real chart-hit down to "safe".
4. **Refactor strategy — Dispatch (Approach A)** — new allergens + diets flow
   through a new generic, registry-driven path. The proven nut code stays
   **byte-identical** so its quality gate cannot regress. Nuts may fold into the
   generic path later, once that path has its own gate.
5. **Gluten-free is modeled as the gluten/wheat *allergen*** (full risk ladder +
   chart evidence + cross-contact), surfaced in the UI as a friendly
   "Gluten-free" toggle. Only vegetarian/vegan are true *diets*.
6. **Diet verdict is its own vocabulary** — `not_compatible / limited /
   good_options / unknown` — rendered in the unified card, not coerced into risk
   tiers.

## Safety invariants (must hold)

- **Nut quality gate is sacred.** A profile that selects only nuts (with the
  existing legacy payload shape) must produce a **byte-identical** assessment to
  today. Dispatch guarantees the nut code is untouched; a regression test asserts
  it.
- **Absence ≠ absence.** No layer emits a bare "safe" for allergens. The fused
  risk keeps its precautionary floor; grounded chart/menu presence always wins the
  precedence ladder over any clean signal.
- **Over-reporting is the conservative direction** for allergens (higher risk on
  ambiguity) and, symmetrically, for diets (flag "not compatible" when unsure;
  never assert "vegan" without a label or clean evidence).
- **Generated KB data is grounded + labeled.** Every generated entry carries a
  confidence and provenance; coverage stays honestly labeled; the verify pass can
  reject an entry down to the cheaper cuisine-prior fallback.

## Architecture

### 1. Canonical allergen registry — `safeplate/allergens.py` (new)

The single source of truth that reconciles the three vocabularies in the codebase
today (`allergen_prior.py` underscore keys like `tree_nuts`; `allergen_matrix.py`
space/singular tokens like `tree nut`; `menu_text.ALLERGEN_TERMS` substrings).

For each of the 14 allergens, an `AllergenSpec`:
- `key` — canonical string (e.g. `"milk"`, `"tree_nut"`, `"gluten"`).
- `display` — human label (e.g. `"Milk"`).
- `matrix_tokens` — the chart token(s) it maps to (reconciliation map).
- `term_vocab` — pointer to its ingredient/term vocabulary (multilingual;
  generated for non-nut allergens, existing rich set for nuts).
- `dish_kb` — pointer to its generated hidden-ingredient dish KB.
- family relationships preserved: `nuts` remains a super-family over
  `{peanut, tree_nut}` (nut path keeps its existing behavior).

Diets declared here too, as `DietSpec`:
- `key` — `vegetarian` / `vegan`.
- `excluded_allergens` — chart tokens whose presence disqualifies (vegan →
  `{milk, egg, fish, shellfish, mollusc}`; vegetarian → `{fish, shellfish,
  mollusc}`).
- `excluded_categories` — non-allergen animal categories needing the meat/animal
  KB (meat, poultry, gelatin, honey for vegan; meat, poultry, gelatin for
  vegetarian).
- `label`.

Reconciliation helpers: `canonical(token) -> key`, `matrix_tokens_for(key)`,
`spec_for(key)`, iteration over all specs/diets.

### 2. Generic prior KB layer — `allergen_prior.py` (+ `data/allergen_kb/`)

Generated data (offline, see §7):
- `data/allergen_kb/<allergen>.json` — dish-pattern → `{allergens, risk, note,
  confidence, source}` entries, mirroring `DISH_NUT_KNOWLEDGE`'s
  `(pattern, allergens, risk, note)` shape.
- `data/allergen_kb/cuisine_baselines.json` — allergen × cuisine baseline floats
  (the generic twin of `CUISINE_NUT_BASELINE`).
- `data/allergen_kb/meat_animal.json` — dish/ingredient → animal categories, for
  the diet evaluator.
- Multilingual term additions for non-nut allergens, merged into the term vocab
  the registry exposes (extends today's thin `ALLERGEN_TERMS`).

New generic functions (nut functions stay as-is):
- `restaurant_allergen_risk(*, allergen, cuisines, region, menu_items, ...)` —
  registry-driven twin of `restaurant_nut_risk`, reading the generated KBs.
- `allergen_cuisine_baseline(allergen, cuisines, region)` — twin of the
  `CUISINE_NUT_BASELINE` lookup inside `score_restaurant_prior`.

Shared, unchanged, allergen-agnostic: `normalize_cuisine`, `region_from_address`,
`labeling_trust_for_region`, `absence_inference_factor`, the home-region boost.

### 3. Generic scorer path — `allergen_score.py`

`_score_one_allergen` dispatches on the allergen key:
- **nuts family (`nuts`/`peanuts`/`tree_nuts`)** → existing tuned block,
  untouched.
- **any other allergen** → new `_score_generic_allergen`, which:
  - recognizes grounded evidence via registry-driven `matrix_covers(allergen,
    terms)` (generalizes `matrix_covers_nuts`) and `_split_allergen_terms(allergen,
    terms, ...)` (generalizes `_split_nut_terms`, which currently early-returns
    empty for non-nuts);
  - gets its prior from `restaurant_allergen_risk`;
  - reuses the **same** fusion arithmetic, tier thresholds (`_SEVERITY_TUNING`,
    `_CC_*` — severity-keyed, already allergen-agnostic), community modifier
    (`_apply_community`), and ranking (`rank_restaurants_for_user`).

`RestaurantSignals` gains generic first-party clean-claim modeling beyond
`nut_free_claim` where cheaply available (e.g. `allergen_menu_available`,
`allergy_disclaimer` already exist and are generic); a generic
`<allergen>_free_claim` is out of scope for v1 unless the evidence layer already
surfaces it.

The `UserAllergenAssessment` output shape is unchanged: `per_allergen` already a
list; aggregation stays "worst tier across the user's allergens".

LLM scorer (`allergen_score_llm.py`): generalize the nut-worded prompt to name
the user's actual allergens; guardrail math already generic. Diets are **not**
sent through the risk-LLM (they are a separate evaluator).

### 4. Diet evaluator — `safeplate/diet_score.py` (new)

`DietAssessment`:
- `diet` — `vegetarian` / `vegan`.
- `verdict` — `not_compatible / limited / good_options / unknown`.
- `support` — 0–1 score (how well the restaurant serves the diet: count/share of
  compatible items, explicit vegan/veg labels).
- `rationale` — list of strings.
- `offending_items` / `compatible_items` — dish-level evidence.

Logic: a dish is diet-incompatible when its chart marks an excluded allergen
(milk/egg → not vegan) **or** a meat/animal-ingredient KB hit fires. Restaurant
verdict summarizes option availability. Asymmetry: unlabeled/unknown dishes are
**not** assumed compatible; when the whole menu is unknown, verdict is `unknown`
(not `good_options`).

Merged into the response alongside allergen assessments; rendered in the unified
card as a diet-compatibility badge.

### 5. Profile + payload — `common.py`, `api_server.py`/`search_service.py`/`menu_service.py`

Payload gains (backward compatible):
- `allergens: [{allergen, severity, crossContact}]`
- `diets: ["vegetarian" | "vegan" | "gluten_free"]` (gluten_free expands to the
  gluten allergen preset)

`_user_profile_from_payload`:
- If `allergens` present → build `UserProfile(allergens=(AllergenPref(...), ...))`
  directly (nuts entry keeps `nut_types`).
- Else → **legacy fallback**: today's `for_nuts(...)` path, byte-identical.
- Diet flags → new `UserProfile.diets: frozenset[str]` field (defaulted empty, so
  old callers/tests unchanged).

### 6. UI — `app_template.html`

- Onboarding modal: nut-chip block becomes a **14-allergen grid**; each allergen
  expandable to set its own severity; nuts keeps its per-nut sub-selector. Add a
  **diet-toggle row** (Gluten-free / Vegetarian / Vegan). Unlocks the dormant
  `.allergen-chip.locked` / `.soon` CSS.
- State + persistence: expand `state`/`saveProfile`/`loadProfile` to the new
  shape; **migrate** old `localStorage["safeplate.profile"]`
  (`{severity, crossContact, nutTypes}`) into a nuts entry.
- Payload builders (`/api/search` ~L1535, `/api/menu` ~L1588/L1933) send the new
  keys.
- Rendering: replace hardcoded `" for nuts"` (drawer L1875/L1735/L1789) with
  per-allergen labels; add a **per-allergen breakdown** in the "Why this score"
  drawer (each selected allergen a row: tier + rationale + riskiest items); card
  shows worst-allergen tier + diet-compatibility badge. `isNut()` chip coloring
  generalizes to per-allergen chip classes.

### 7. Content generation — ultracode fan-out (offline, one-time, cached)

A workflow, run offline, output committed as data:
- **Draft stage** — parallel agents, one per (allergen × cuisine family), draft
  hidden-ingredient dish→allergen entries (pattern, allergens, risk, note).
- **Verify stage** — a separate adversarial agent per entry confirms it is a
  real, well-known culinary fact (not a guess), else drops it to the cuisine-prior
  fallback. Only grounded entries ship, each with confidence + provenance.
- Same shape for the **meat/animal taxonomy** (diets) and **multilingual terms**
  for non-nut allergens.
- Output → `data/allergen_kb/*.json`. Never fabricates a *clean* claim (which
  would lower risk); only presence priors, which are safe-asymmetric.

### 8. Testing / quality gate

- **Nut regression** — assert a legacy nuts-only profile scores byte-identically
  to today (protects the gate; Approach A makes this true by construction).
- New unit tests: `test_allergens.py` (registry + reconciliation),
  `test_diet_score.py`, generic-allergen scorer tests (milk/gluten chart-hit
  registers, cuisine-prior fallback, absence-≠-safe floor), payload back-compat.
- Eval harness: multi-allergen bench over frozen chart snapshots (does
  milk/gluten scoring rank sensibly?), diet-compat check.
- Live smoke: score a real chain with an allergen matrix (e.g. Wagamama /
  Chipotle) for milk, gluten, and vegan.

## Out of scope (v1)

- General allergen sub-typing beyond nuts (e.g. shellfish → shrimp/crab) — YAGNI.
- Per-allergen first-party "<allergen>-free claim" modeling beyond what the
  evidence layer already surfaces.
- Sending diets through the risk-LLM scorer.

## Key files

- New: `safeplate/allergens.py`, `safeplate/diet_score.py`,
  `data/allergen_kb/*.json`, tests above.
- Changed: `allergen_prior.py` (generic KB loaders + functions),
  `allergen_score.py` (dispatch + generic path), `allergen_score_llm.py` (prompt),
  `common.py` (profile), `menu_text.py` (term vocab via registry),
  `app_template.html` (selectors + rendering).
- Untouched (protected): the nut scoring block in `allergen_score.py`, the nut
  tables/functions in `allergen_prior.py`.
