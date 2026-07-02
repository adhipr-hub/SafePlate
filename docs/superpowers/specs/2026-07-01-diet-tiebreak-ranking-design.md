# Diet as an allergen-risk tie-breaker — design

**Date:** 2026-07-01
**Branch context:** `diet-llm-inference-and-signals`
**Status:** approved design, pending implementation plan

## Problem

The menu list is ranked entirely by allergen risk. "Safest first" sorts on
`allergenPrior.risk` in `sortRows()` (`safeplate/app_template.html:1809-1816`).
The diet compatibility work (diet LLM judge + accommodation signals) produces a
rich per-dish diet verdict, but it lives in a separate `summary.diets` payload
that is rendered as a badge/note and **never influences the list order**.

When a user has selected a restrictive diet (vegan / vegetarian / gluten-free),
two restaurants that are equally safe on allergens are presented in arbitrary
provider order — even when one is a clearly better fit for the chosen diet.

## Goal

Let diet compatibility break ties **within** the allergen ranking, without ever
perturbing the safety ordering itself.

## Core stance alignment

- **Safety is asymmetric / allergen risk is primary.** Diet is strictly a
  secondary key. It can only reorder dishes the risk score already treats as
  *exactly* equally safe. It can never move a riskier dish above a safer one.
- **Evidence over opinion.** Within a tie, better-grounded diet verdicts
  (`labeled` > `ai_assessed`) outrank guessed ones.

## Behavior

Primary sort key is unchanged: `allergenPrior.risk` ascending.

A diet secondary key is applied **only when both** hold:
1. At least one restrictive diet is selected (`state.diets` is non-empty), and
2. Two rows have **exactly equal** rounded `allergenPrior.risk`.

If no diet is selected, or the risks differ, the ordering is byte-identical to
today. This preserves the default-equivalence invariant that protects the
quality gate.

## Diet sort key (per row)

Derived from `row.menuDetail?.summary?.diets` (attached at
`app_template.html:1772` after a card upgrades):

1. Locate the diet entry matching each selected diet in `state.diets`.
2. **Verdict rank** (primary): `good_options(3) > limited(2) > unknown(1) >
   not_compatible(0)`.
3. **Basis rank** (secondary): `labeled(4) > ai_assessed(3) > mixed(2) >
   estimated(1) > none(0)`.

Higher key sorts earlier (better diet fit first).

### Edge cases

- **No diet data yet** (un-upgraded cuisine estimate, or upgrade that produced no
  `summary.diets`): the row gets the **lowest** diet key. A confirmed diet match
  never loses its slot to an unknown.
- **Missing diet entry** for a selected diet within a present `diets` array:
  treated as `unknown`/`none` — the lowest non-absent key.

## Multi-diet selection — weakest fit wins

When several restrictive diets are selected at once (e.g. vegan **and**
gluten-free), the row's diet key is the **worst** fit across all selected diets:
the minimum verdict rank, then the minimum basis rank among those diets. A dish
only ranks ahead if it fits *everything* the user asked for — consistent with the
safety-conservative "all constraints matter" framing.

## Implementation surface

Localized to the client sort logic in `safeplate/app_template.html`:

- New helper `dietSortKey(row)` → returns a comparable tuple/number encoding
  (worst-verdict-rank, worst-basis-rank) across `state.diets`, or the lowest key
  when no diet data is present.
- `sortRows()` (`:1809-1816`): in the `state.sort==="risk"` branch, when the
  primary risk comparison is `0` and `state.diets.length`, fall through to the
  diet key comparison.

The two existing re-sort triggers already gate on `state.sort==="risk"` and need
no change:
- batched-upgrade settle re-sort (`:1790`), and
- the render-time `sortRows(state.rows)` call.

No backend change: the `summary.diets` payload already carries `verdict` and
`basis` (`menu_service._diet_summary_payload`, `menu_service.py:191-207`; values
defined in `diet_score.py:45-47`).

## Testing

- **Default-equivalence:** with `state.diets` empty, `sortRows` output is
  identical to today for any input (protects the nut/allergen gate).
- **Unequal risk:** diet never reorders when risks differ, even with a diet
  selected.
- **Tie + single diet:** exact-equal-risk rows order by verdict then basis;
  `good_options/labeled` precedes `limited/estimated` precedes no-data.
- **Tie + multi diet:** weakest-fit-wins — a dish strong in one diet but weak in
  another ranks below a dish moderately good in both.
- **No diet data:** un-upgraded rows sink below rows with a known good verdict on
  a tie.

## Non-goals

- Diet does **not** enter the allergen risk number itself.
- No change to the backend ranker `rank_restaurants_for_user`
  (`allergen_score.py:1402`); the live list path is the client sort.
- Google star rating remains a separate manual sort, untouched.
