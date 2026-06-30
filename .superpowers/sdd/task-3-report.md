# Task 3 Report: Onboarding modal shell + Step 1 quiz

## What was done

### CSS block
Added 22 rules under `/* ── Onboarding modal ── */` comment immediately after `.rate-save-btn:hover` and before the existing `/* Reduced motion */` block. Includes `.ob-scrim`, `.ob-scrim.show`, `.ob-modal`, `.ob-progress`, `.ob-dot`, `.ob-q`, `.ob-row`, `.ob-foot`, `.ob-skip`, `.ob-next`, `.ob-pane`, `.ob-pane.on`, plus the `@media (prefers-reduced-motion: reduce) { .ob-scrim { backdrop-filter: none; } }` rule.

### HTML markup
`div.ob-scrim#onboard` inserted after the `.search-card` closing `</div>` (still inside `search-zone`). Contains `#obProgress` (two `.ob-dot` spans), `#obStep1` quiz pane (severity / cross-contact / nuts chips), `#obStep2` empty pane (Task 4 placeholder), and `#obFoot` with `#obSkip`, `#obToStep2`, `#obDone`.

### JS
Added after `markOnboarded()` (Task 2), before `_histHash`. Includes:
- `_obTrigger`, `_obStep` state vars
- `_obSync(group, value)`, `_obRenderNuts()` helpers
- `applyQuizToState()`, `goStep(n)`, `openOnboarding(step=1)`, `closeOnboarding()`
- `openOnboarding` sets `state.crossContact = state.crossContact || "strict"` before `_obSync("obCc", …)` — safe default for never-set users
- Chip event listeners for `#obSev`, `#obCc`, `#obNuts`
- Button handlers for `#obToStep2`, `#obDone`, `#obSkip`
- Escape key handler on `#onboard`

### First-run auto-open
`if (!hasOnboarded()) openOnboarding(1);` placed between `loadProfile()` and `renderHistoryList()` at script end.

## Grep confirmations

All required identifiers found:
- `openOnboarding` — lines 1271, 2289
- `closeOnboarding` — lines 1281, 1307, 1308, 1310
- `goStep` — lines 1263, 1276, 1306
- `id="onboard"` — line 1024
- `id="obStep2"` — line 1065
- `if (!hasOnboarded())` auto-open — line 2289
- `typeof renderPicker === "function"` guard — line 1275
- `typeof renderProfileSummary === "function"` guard — line 1284

## Pytest result

392 passed, 11 subtests passed in 4.75s (no regressions)

## Concerns

None. The modal `div.ob-scrim` sits inside `search-zone` (not at `body` level), but since it is `position:fixed` with `z-index:1000` this has no visual impact. Chip event handlers reference DOM elements by `getElementById` at parse time; these IDs are present in the markup added in the HTML step, so wiring is safe at the point of execution.

---

# Bug Fix: #onboard inert due to placement inside #search

## What was wrong

`#onboard` was nested inside `<div class="search-zone" id="search">` (a direct child of `<body>`). When `openOnboarding()` called `_setBgInert(true)`, `#search` was inerted — taking the modal with it. The modal was visible (position:fixed) but fully non-interactive.

## Changes made

### 1. HTML: relocated #onboard

Moved the entire `<div class="ob-scrim" id="onboard" …>…</div>` block out of `#search` / `<main>` and made it a direct child of `<body>`, placed immediately before `<!-- Drawer -->` / `<div class="scrim" id="scrim"></div>` (line ~1204).

Grep confirmation: `id="onboard"` appears at line 1150, at 0-indent (body-level), after `</footer>` and before `<div class="scrim" id="scrim">`.

### 2. JS: keep set updated in _setBgInert

Changed:
```
const keep = new Set([$("#drawer"), $("#scrim"), $("#toast")]);
```
to:
```
const keep = new Set([$("#drawer"), $("#scrim"), $("#toast"), $("#onboard")]);
```

Grep confirmation: `const keep = new Set` at line 2110 includes `$("#onboard")`.

## Pytest result

392 passed, 11 subtests passed in 4.24s — no regressions.

---

# Review Fix: Chip scoping + modal focus trap

## Finding 1 — In-card chip selectors scoped to #search

Six selectors in `/* ── events ── */` and `/* ── per-nut picker ── */` region
(app_template.html ~lines 2189–2216) changed from document-scoped to `#search`-scoped:

1. `document.querySelectorAll(".allergen-chip.sev")` (onclick handler) → `document.querySelectorAll("#search .allergen-chip.sev")`
2. `document.querySelectorAll(".allergen-chip.sev")` (inner deselect-all) → `document.querySelectorAll("#search .allergen-chip.sev")`
3. `document.querySelectorAll(".allergen-chip.cc")` → `document.querySelectorAll("#search .allergen-chip.cc")`
4. `_nutChips`: `document.querySelectorAll('.allergen-chip.nut[data-nut]')` → `document.querySelectorAll('#search .allergen-chip.nut[data-nut]')`
5. `allBtn`: `document.querySelector('.allergen-chip.nut[data-nut="__all"]')` → `document.querySelector('#search .allergen-chip.nut[data-nut="__all"]')`
6. `document.querySelectorAll(".allergen-chip.nut")` (onclick) → `document.querySelectorAll("#search .allergen-chip.nut")`

`paintCC` (~line 1326): `document.querySelectorAll(".allergen-chip.cc")` → `document.querySelectorAll("#search .allergen-chip.cc")`

Modal `#obSev`/`#obCc`/`#obNuts` handlers: unchanged (already scoped by getElementById).

Grep confirmation: all 7 JS chip queries now contain `#search`; CSS selector (line 251) is untouched.

## Finding 2 — Modal Tab focus trap

`trapModalTab(e)` function added immediately after `trapDrawerTab` (~line 2136):
mirrors the drawer trap pattern, checks `#onboard` `.show` class, cycles focus
within the modal's focusable elements.

The existing `#onboard` keydown listener (~line 1311) updated to call
`trapModalTab(e);` at top before the Escape branch.

## Pytest result

392 passed, 11 subtests passed in 4.02s — no regressions.
