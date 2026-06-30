# Task 5 Report — Slim search card + profile summary + drawer relabel

## What was removed

**In-card markup (search card):**
- `.allergen-row` "Avoiding" row (static Nuts/Sesame/Dairy/Gluten chips)
- `.allergen-row.nut-row` "Which nuts" row (all 12 per-nut chips)
- `.allergen-row` "How serious" severity chip row
- `.allergen-row` "Cross-contact" chip row
- `#histSection` (free-text Places editor: `#histName`, `#histRating`, `#histNote`, `#histAddBtn`, `#histList`, privacy line)

**In-card JS (hazardous on removal):**
- `document.querySelector("#histAddBtn").onclick` block — would have thrown `Cannot set onclick of null` at load; deleted entirely.
- `document.querySelectorAll("#search .allergen-chip.sev").forEach(...)` event wiring — removed.
- `document.querySelectorAll("#search .allergen-chip.cc").forEach(...)` event wiring — removed.
- Per-nut picker block: `const _nutChips`, `function _setChip`, `function syncNutState`, `document.querySelectorAll("#search .allergen-chip.nut").forEach(...)` — all removed. These had no callers outside this block.
- Startup calls `syncNutState(); paintCC();` — removed. `syncNutState()` would have reset `state.nutTypes = []`, clobbering the profile restored by `loadProfile()`.

**Functions left as dead but harmless defs:**
- `paintCC()` (definition kept at ~1377; zero remaining callers — no harm, queries return empty NodeList if somehow called).
- `renderHistoryList()` (definition kept; remaining callers produce safe no-ops via `if(!el) return` guard on `#histList` which no longer exists).
- `effectiveCC()` (definition kept; no remaining callers but harmless).

## What was added

**Markup:**
- `<div class="profile-summary" id="profileSummary"></div>` in place of the removed rows, inside `.search-card`.

**CSS (after `.cc-hint` block):**
- `.profile-summary`, `.profile-summary .ps-text`, `.profile-summary .ps-edit`, `.profile-summary .ps-edit:hover`

**JS:**
- `const _SEV_LABEL`, `const _CC_LABEL` label maps.
- `function renderProfileSummary()` — renders severity · nuts · cross-contact (+ "N places rated" if any) plus an "Edit profile" button that calls `openOnboarding(1)`.

**Calls to `renderProfileSummary()`:**
- In `renderResults()` (after card wiring) — so "N places rated" refreshes after every search.
- At startup, after `loadProfile()` and `renderHistoryList()`.
- `closeOnboarding()` already contained the guard call (Task 3): `if (typeof renderProfileSummary === "function") renderProfileSummary();`

## Drawer relabel

- Heading changed: "Rate your experience" → "How comfortable are you eating here?"
- `aria-label` changed: "Your rating (1 to 10)" → "How comfortable, 1 to 10"
- Caption added inside `.rate-widget`: `<p class="hist-privacy">1 = avoid · 10 = totally comfortable</p>`
- Drawer save handler already performs upsert (idx check before push) — no change needed.

## Grep confirmations

- `#histAddBtn` — GONE (no querySelector/onclick reference remains).
- `#histSection` — GONE from markup.
- In-card `.allergen-chip.sev/.cc/.nut` event wiring — GONE (only `paintCC` function body contains a `.allergen-chip.cc` selector, which is a dead def with no callers).
- `syncNutState();` startup call — GONE.
- `paintCC();` startup call — GONE.
- `renderProfileSummary` — defined at ~2203, called at startup (2311), in `renderResults` (1733), in `closeOnboarding` (1339).
- `#profileSummary` — present at line 995.

## Pytest result

392 passed, 11 subtests passed (no Python changes).

## Concerns

None. The `paintCC` and `nut-row` CSS are orphaned dead code but completely harmless. The `.nut-row` CSS no longer has any matching DOM elements (the modal nut chips use `ob-row`). If desired, these can be cleaned up in a future pass, but they pose no correctness or runtime risk.

---

# Task 5 Final-Review Fixes — Safety-direction mismatch + stale count

## Fix 1 — `closeOnboarding()` now persists baseline (safety-direction mismatch)

**Problem:** When a user skips onboarding without interacting, `openOnboarding()` sets `state.crossContact = state.crossContact || "strict"` in-session. `closeOnboarding()` called `markOnboarded()` but never `saveProfile()`. After reload, `loadProfile()` left `crossContact=""`, the modal would not reopen (onboarded flag set), and the card showed "very careful" (`_CC_LABEL[""]`) while the backend scored `crossContact:""` as "moderate" — UI over-claimed caution vs what was actually scored.

**Fix applied:** `safeplate/app_template.html` line 1338 — added `saveProfile()` immediately after `markOnboarded()`:
```js
_setBgInert(false); markOnboarded(); saveProfile();
```

**Grep confirmation:**
- Line 1338: `_setBgInert(false); markOnboarded(); saveProfile();` ✓

## Fix 2 — Drawer save handler refreshes profile summary (stale count)

**Problem:** The drawer "How comfortable are you eating here?" save handler called `saveHistory()` and `renderHistoryList()` but not `renderProfileSummary()`, so the card's "N places rated" count went stale until the next search.

**Fix applied:** `safeplate/app_template.html` line 2088 — added guarded `renderProfileSummary()` call after `renderHistoryList()`:
```js
if (typeof renderProfileSummary === "function") renderProfileSummary();
```

**Grep confirmation:**
- Lines 2087–2088: `renderHistoryList();` followed immediately by `if (typeof renderProfileSummary === "function") renderProfileSummary();` ✓

## Pytest result

392 passed, 11 subtests passed (no Python changes; 2.93s).

## Concerns

None.
