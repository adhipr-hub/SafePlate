# Diet compatibility: LLM inference + accommodation signals — Design

**Date:** 2026-07-01
**Status:** Approved (design), pending implementation plan

## Goal

Make vegetarian/vegan compatibility genuinely useful by (A) **inferring** each
dish's compatibility — using an LLM that reasons about hidden ingredients, with a
deterministic taxonomy floor — instead of only crediting dishes that carry an
explicit `vegan`/`vegetarian` label; and (B) surfacing **diet-accommodation
signals** ("dishes can be made vegetarian/vegan") found on the restaurant website
and in community/review search, shown as sourced notes that can lift a verdict.

Diets remain a **distinct concept** from allergen RISK. Nothing here changes
allergen scoring, and the nut gate stays byte-identical.

## Background (current behaviour)

`safeplate/diet_score.py::assess_diet` classifies each menu item three ways:
- **conflict** — the dish name contains an excluded-category term (meat/poultry/
  gelatin[/honey], from `data/allergen_kb/meat_animal.json`) OR the dish's
  extracted `allergen_terms` include an excluded allergen (fish/shellfish/mollusc
  [/milk/egg]).
- **confirmed-compatible** — the dish carries a positive `dietary_terms` label
  (`vegan`/`vegetarian`/`plant-based`).
- **unknown** — everything else (no conflict, no label).

A reviewer deliberately required the positive label for "compatible," because
dairy/egg/broth hide in dish names. The verdict is `not_compatible` / `limited` /
`good_options` / `unknown` by the share of confirmed-compatible items.

Two signal pipelines already exist and are the hook points for Part B:
- **Website:** `safeplate/extraction2/allergy_signals.py::extract_allergy_signals`
  runs one grounded page-LLM call that returns allergy-handling booleans + verbatim
  statements → `AllergySignal` on the extraction result.
- **Community:** `safeplate/community_signals.py::fetch_community_signals` runs a
  Brave web search + one grounded LLM classify (allergy handling + dish mentions).

## Part A — Per-dish diet inference (LLM + deterministic floor)

### A1. LLM diet judge (`safeplate/diet_llm.py`, new)

When the "ai" engine is active AND a Gemini key is available AND the diner selected
at least one diet, a **focused, diet-only** LLM pass judges each menu item for each
selected diet:

- Input: the extracted menu items (name + description + any `dietary_terms` /
  `allergen_terms`) and the selected diets.
- Output per (item, diet): `{ item_name, verdict: "yes"|"no"|"unknown", reason,
  confidence }`. The LLM is instructed to reason about **hidden** animal
  ingredients (butter/cream/parmesan in risotto, fish sauce in pad thai, lard in
  refried beans, anchovy in caesar, gelatin, honey, egg wash).
- **Grounding:** a judgment is kept only if its `item_name` matches a real menu
  item (case-folded). Ungrounded/hallucinated items are dropped.
- **Cached** (keyed by menu-item hash + sorted diets + model) and rate-governed via
  the existing Gemini infra. It is a **separate** call from the allergen judge so
  diets stay a distinct concern.
- Fails closed: on any error / no key, returns no judgments and the caller uses the
  deterministic floor.

### A2. Deterministic floor (`safeplate/diet_score.py`)

Used when the LLM judge produced no judgment for an item (no key/quota, rules
engine, dropped-as-ungrounded, or the LLM said "unknown"). Extends today's logic:

- **conflict** — unchanged, PLUS: for **vegan only**, screen the dish NAME for
  obvious dairy/egg words that the meat taxonomy misses (cheese, paneer, butter,
  cream, ghee, custard, omelet/omelette, mayo, aioli, yogurt/yoghurt, milk, feta,
  mozzarella, etc.). New `dairy` and `egg` name lists live in
  `data/allergen_kb/meat_animal.json` and are wired to **vegan only** via the
  `vegan` DietSpec `excluded_categories` (`safeplate/allergens.py`). Vegetarians
  keep dairy/egg, so vegetarian behaviour for these names is unchanged.
- **confirmed-compatible** — unchanged (explicit positive label).
- **assumed-compatible** (NEW) — no conflict + no label → assume compatible.
  - Vegetarian: any no-conflict item.
  - Vegan: any no-conflict item that also passed the dairy/egg name screen.

### A3. Provenance + verdict

`DietAssessment` gains a **`basis`** field per compatible item and an overall basis:
`labeled` > `ai_assessed` > `estimated` (deterministic floor).

- `support`/share = compatible items / total items.
- Verdict from share: `good_options` (share ≥ 0.4), else `limited`; all-conflict →
  `not_compatible`; empty menu → `unknown` (unchanged asymmetry: an empty/unknown
  menu never yields `good_options`).
- **Vegan cap:** compatibility resting on the **deterministic `estimated`** basis is
  capped at `limited` (a name list can't see hidden dairy/egg). `labeled` and
  `ai_assessed` vegan may reach `good_options` (the LLM accounted for hidden
  ingredients). A grounded accommodation signal (Part B) **releases the cap**.
- Rationale wording always states the basis: "labeled vegan", "AI-assessed vegan
  (hidden dairy checked)", or "estimated from dish names (not confirmed)".

## Part B — Diet-accommodation signals (website + community)

Detect statements that the kitchen will make/serve dishes vegetarian or vegan
("can be made vegan", "great vegan options", "vegan menu on request", "ask for our
plant-based options").

### B1. New signal type

`DietSignal { diet: "vegetarian"|"vegan", quote: str, url: str, source:
"website"|"community" }`. Every `quote` must be a **verbatim substring** of the
searched/scraped source text (same grounding rule as existing signals). Diet
signals feed ONLY diet compatibility — never allergen risk.

### B2. Website (`extraction2/allergy_signals.py` + `schema.py`)

Extend the SAME page-LLM call (no extra request): add booleans
`veg_can_be_made` / `vegan_can_be_made` + require a grounded verbatim quote for each.
Surface grounded diet quotes on the extraction result (a `diet_signals` list on
`MenuExtractionResult`, kept separate from `allergy_signals`).

### B3. Community (`community_signals.py`)

Extend the SAME Brave+LLM classify (no extra request): add a `diet_flexibility`
array to the schema/prompt — `{ diet, quote }`, grounded against the snippet text,
attributed to the primary source URL. Surface as `DietSignal`s on
`CommunityResult`.

### B4. Effect on the verdict

- Each grounded `DietSignal` renders as a sourced **🌱 note** on the card with a
  clickable source (reuse the existing evidence deep-link pattern).
- A signal for a selected diet can **upgrade that diet's verdict by one step**
  (`unknown`/`limited` → `good_options`; releases the vegan `estimated` cap).
- **Safety-asymmetric:** signals only ever IMPROVE a diet verdict; they never
  override a conflict-based `not_compatible` and never touch allergen risk.

## UI (`safeplate/app_template.html`)

- Diet badge gains provenance-aware wording: distinguish "labeled" vs "AI-assessed"
  vs "estimated" (never color-alone — the words carry the meaning).
- Render 🌱 accommodation note(s) with clickable source link(s), reusing the
  evidence-link component.
- Only present when the diner selected diets (`summary.diets` non-empty).

## Data flow

`menu_service` gathers menu items + website `diet_signals` + community `DietSignal`s,
runs `diet_llm.judge_diet_compatibility` (when ai engine + key + diets), and calls
`assess_diets(diets, menu_items=…, llm_judgments=…, accommodation_signals=…)`. The
result maps into `summary.diets` / `summary.perAllergen` payload as today, now with
`basis` and `notes`.

## Invariants & safety

- **Nut gate byte-identical**; allergen risk scoring untouched (guarded by
  `tests/test_allergen_generic_score.py::test_nut_profile_byte_identical` +
  `eval/bench_multi_allergen.py` nut-parity).
- **Diets stay distinct**: diet inference/signals never change allergen risk.
- **Default-equivalence:** with no diets selected, the diet code path is dormant and
  the response is byte-identical to today (protected by an explicit test).
- **Grounding:** every LLM judgment maps to a real menu item; every accommodation
  quote is a verbatim source substring.
- **Evidence-first:** estimates are always labeled as estimates; only labeled/
  AI-assessed compatibility (or a grounded signal) may reach `good_options`.
- **Fail-closed:** every LLM/network path degrades to the deterministic floor / an
  empty result; it can never break the response.

## Testing

- `diet_score` floor: vegetarian assumed from no-meat name; vegan NOT assumed for a
  cheese/butter-named dish; estimated-vegan capped at `limited`; label → allowed
  `good_options`; all-conflict → `not_compatible`; empty menu → `unknown`.
- `diet_llm`: grounded judgment kept, ungrounded item dropped; "unknown" falls back
  to floor; hidden-ingredient cases (risotto/pad thai) judged `no` when expected;
  cache hit avoids a second call; no key → no judgments.
- Verdict fusion: AI-assessed vegan reaches `good_options`; deterministic-estimated
  vegan stays `limited` until a signal upgrades it.
- Signals: grounded diet quote kept, ungrounded dropped (website + community);
  signal upgrades verdict by one step; signal never downgrades or affects allergen
  risk.
- Default-equivalence + nut-parity guards unchanged and green.

## Out of scope (deferred)

- Diets beyond vegetarian/vegan (pescatarian, halal, kosher). Gluten-free stays the
  gluten allergen.
- Per-dish diet judgments in the drawer UI beyond the badge + notes (the extracted
  `dietary_terms` chips already exist).
