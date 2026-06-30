# Onboarding Quiz + Chain Picker — Design

**Date:** 2026-06-29
**Status:** Approved (brainstorming) → ready for implementation plan
**Builds on:** the merged personal-experience-calibration feature (`experienceHistory` in
localStorage → AI scorer context).

## 1. Goal

Replace the cramped inline personalization controls in the search card with a friendly
**first-run onboarding flow**: a plain-language **nut quiz** that sets the diner's profile,
and a tap-only **categorized chain picker** for recording how comfortable they are eating
at common chains. The search card slims down to location + search + a profile summary.

## 2. Decisions (from brainstorming)

- **Onboarding owns setup; the card slims down.** The quiz sets the profile; the picker
  captures places. The search card keeps only the location input, "Find safe places," and a
  compact profile-summary line with an **Edit** button. The inline severity/nut/cross-contact
  chip rows **and** the free-text "Places you've eaten" editor are removed from the card.
- **Quiz = guided plain-language setup** of the EXISTING profile fields. No medical
  diagnosis. "Not sure" / skipped → the **safe baseline** (all nuts · very careful · Allergy).
- **Chain picker = tap → 1–10 comfort.** The 1–10 means **"how comfortable are you eating
  here"** (10 = fully comfortable, 1 = avoid), **not** reaction presence. It maps onto the
  existing `rating` field — no schema change.
- **Curated catalog**, tap-only (no typing). **~30** of the most recognizable US brands —
  about 3–4 per category — kept small so first-run scans fast (personalization generalizes by
  category, so a few exemplars per category suffice). Places not in the catalog are rated from
  the restaurant **drawer** ("Rate your experience").
- **First run: auto on first visit, skippable.** Reopenable anytime via **Edit**.
- **Build: client-side modal wizard; catalog as static JS data** (Approach A). The only
  backend change is one prompt-wording line.

## 3. Non-goals

- No backend catalog endpoint (static JS const — Approach B rejected).
- No separate `/onboarding` page (in-place modal — Approach C rejected).
- No data-shape change to `experienceHistory` (`{name, rating, note}` unchanged).
- No medical inference; no multi-allergen (nuts only today).
- No cross-device sync (per-browser localStorage; no accounts).

## 4. Components

### 4.1 Onboarding modal (`safeplate/app_template.html`)
A dialog reusing the existing scrim + `_setBgInert` + focus handling, with a 2-step progress
indicator:

- **Step 1 — Nut quiz** (each question maps to a profile field; clear default each):
  1. "How serious is your nut allergy?" → Preference / Intolerance / Allergy / Anaphylaxis (`state.severity`)
  2. "Do traces, shared fryers, or 'may contain' worry you?" → Not a concern / Somewhat / Very careful (`state.crossContact`)
  3. "Which nuts?" → **All nuts** (default) or specific (the 11 nut chips) (`state.nutTypes`)
- **Step 2 — "Where do you eat?" picker:** the catalog as a category-grouped grid of
  tappable chain buttons. Tapping selects the chain and expands an inline **1–10 "How
  comfortable are you eating here?"** slider (default **7** — they're tapping places they
  eat at, but adjustable) + optional note; rated chains show their number and can be
  cleared (clearing removes the history entry). "Skip — I'll add later" and "Done" both
  finish.

### 4.2 Slimmed search card
Location input + "Find safe places" + a **profile summary line**
(e.g. "Anaphylaxis · all nuts · very careful · 5 places rated") + an **Edit** button that
reopens the modal at Step 1. The chip rows and free-text places editor are removed; their
state-update logic is reused inside the modal.

### 4.3 Drawer affordance (relabel)
The existing "Rate your experience" control stays for off-catalog restaurants, relabeled to
the comfort scale ("How comfortable are you eating here? 1–10").

## 5. Data & persistence

No data-shape changes. State + localStorage:
- **Profile:** `state.severity`, `state.crossContact`, `state.nutTypes` — written by the quiz;
  new `loadProfile()`/`saveProfile()` persist to `localStorage["safeplate.profile"]` (today
  only history persists).
- **History:** `state.experienceHistory` (already persisted) — picker + drawer write
  `{name, rating, note}`, `rating` = comfort (1–10).
- **First-run flag:** `localStorage["safeplate.onboarded"]`; absent → auto-open after config
  load; set on Done or Skip.

### 5.1 Catalog
A static JS const `CHAIN_CATALOG = [{category: str, chains: [str, ...]}, ...]`, **~30** US
brands (~3–4 each) across ~8 categories (Fast food/Burgers, Pizza, Mexican, Coffee, Chicken,
Sandwiches/Subs, Asian, Bakery/Dessert). Pure tap-source; selecting writes
a history entry by brand name. Case-insensitive de-dup with drawer entries (the shipped
upsert already does this).

## 6. Comfort scale → AI prompt (the one backend change)

The `your_history` paragraph in `_SCORER_SYSTEM` (`safeplate/allergen_score_llm.py`,
shipped last feature) is reworded: each rating is the **diner's own comfort/trust eating
there** (10 = fully comfortable, 1 = avoids it); calibrate similar places toward that
comfort; **never** use it to call a dish safe when the chart confirms it contains their
allergen. `_SCORER_SYSTEM_BATCH` inherits it (`= _SCORER_SYSTEM + suffix`). The hard
confirmed-presence floor in `_apply_guardrails` is unchanged. Fully backward-compatible with
the existing plumbing, cache key, and `personalized` flag.

## 7. Accessibility (WCAG 2.2 AA)

`role="dialog"` + `aria-modal`; focus to first control on open, focus trap, **Esc = skip**,
`_setBgInert` on the rest, focus returned to the trigger on close. Quiz options
keyboard-operable; picker chains are `<button>`s; the comfort slider has a visible label +
numeric value (never color-only). Modal transitions honor `prefers-reduced-motion`. User
free-text (notes, any names) escaped via the existing `esc()`.

## 8. First-run logic & migration

- On load, after config: if `localStorage["safeplate.onboarded"]` is absent, open the modal.
  Set the flag on Done or Skip. "Edit" reopens regardless.
- Skipping applies the safe baseline and the app works normally.
- **Migration:** existing users keep their saved history (the 1–10 numbers still read
  "higher = more comfortable"); they see the modal once and can skip. No data migration.

## 9. Safety

- Skip / "not sure" → most cautious baseline (all nuts · very careful · Allergy).
- The comfort signal can relax soft floors but **never** overrides confirmed chart presence
  (unchanged `_apply_guardrails` grounded floor).
- Profile + history stay in the browser; sent to the scorer/Gemini only at request time
  (privacy line shown in the flow), consistent with the shipped feature.

## 10. Testing

- **Backend:** a test asserting the `your_history` prompt paragraph carries the comfort
  framing AND still the "never call a dish safe vs a confirmed chart" clause. Full suite
  stays green (391).
- **Frontend (controller browser smoke; no JS test harness):** first-run modal auto-opens;
  completing the quiz sets + persists the profile (round-trips across reload); the picker
  writes comfort-rated history; the slimmed card shows the summary and "Edit" reopens; drawer
  relabel present; Esc/keyboard work; 0 console errors.

## 11. Open questions / future

- Catalog breadth is a curation call; start ~30 (3–4 per category) and grow from real misses.
- A future "most-tapped chains first" ordering could speed setup once usage data exists.
- Backend-served catalog (Approach B) only if non-engineers need to edit it.
