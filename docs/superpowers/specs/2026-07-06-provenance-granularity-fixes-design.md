# Provenance-granularity fixes: location-mismatch + region false-positive

**Date:** 2026-07-06
**Status:** Design — approved (open decision resolved: §7 option A)
**Found via:** live deep-dive dossier run on *Sweet Maple, 20010 Stevens Creek Blvd,
Cupertino, CA*.

## 1. Problem

Running the deep-dive dossier on the Cupertino Sweet Maple surfaced two
provenance defects. Both stem from one root cause: **SafePlate reasons about menu
provenance at *country* granularity and trusts incidental country mentions in
free menu prose.**

The dossier extraction produced this coverage:

| Source | Result |
| --- | --- |
| `sweetmaplesf.com/menu-cupertino` | 0 items — page is JS-rendered, static fetch empty |
| `.../02-28-2026-sweet-maple-`**`santa-monica`**`-menu-…pdf` | 50 items — **used as the menu** |

…and stamped the verdict with `regionNotice.sourceRegion = "NZ"` / "New Zealand".

**Bug 1 — wrong-location menu shown silently.** The Cupertino menu was
unreadable, so on-site discovery fell back to a **Santa Monica** menu PDF and
presented those 50 dishes (and the whole verdict) under the Cupertino header,
with no signal that the menu is from another location. The provenance guard only
distinguishes *countries*, so a same-country wrong-*city* menu passes unflagged.

**Bug 1b — the wrong-location menu also *suppressed* the search for the right
one.** The Brave menu-PDF fallback ([discover.py:749](../../../safeplate/extraction2/discover.py))
only fires when on-site extraction is *thin* (`len(result.items) < _MENU_PDF_THIN`,
=8). The Santa Monica PDF returned 50 items — not thin — so the Cupertino-specific
Brave query (`"Sweet Maple" "Cupertino" menu filetype:pdf`) never ran. The
thinness gate counts *quantity*, not *location-correctness*.

**Bug 2 — region false positive from menu prose.** `detect_source_region`
([region.py:184](../../../safeplate/extraction2/region.py)) treats an unambiguous
multiword country name as a region tell. On this menu, "New Zealand" appears only
as a **wine origin**:

```
"matua, sauvignon blanc, new zealand"
"the first new zealand sauvignon blanc."
```

Both mentions are ingredient/beverage provenance, not the restaurant's locale, so
the US Santa Monica menu is branded "from New Zealand, not verified for your area"
— a false alarm that erodes trust in a product whose stance is *calm earned trust*.

**Related latent bug — the dossier never renders the notice anyway.** The dossier
template checks `regionNotice.message` ([dossier_template.html:406](../../../safeplate/dossier_template.html)),
but `region_notice()` returns `{verified, homeRegion, homeLabel, sourceRegion,
sourceLabel}` with **no `message` field**. So even a *correct* region notice would
not display in the dossier. Bug 1's new notice must render correctly, and this
gap must be fixed for either notice to be visible.

## 2. Goals / non-goals

**Goals**
- Detect and clearly surface when the shown menu is from a **different location**
  than the diner's (keep the menu — "flag, don't hide").
- Let a detected location mismatch **re-open the Brave menu-PDF fallback** so the
  correct-location menu gets a chance to be found.
- Stop menu-borne country names (wine/ingredient origins) from **falsely** tagging
  a source's region, without weakening genuine foreign-chart detection.
- Make the dossier actually render provenance notices.

**Non-goals**
- Rendering JS menus / headless-browser work to read `/menu-<city>` directly
  (deferred; the flag + Brave re-open mitigate it).
- Changing any **scoring** logic. Both fixes touch provenance *notices* and
  *discovery gating* only — per-nut scoring output stays byte-identical, so the
  offline quality gate is unaffected.
- A general city gazetteer. Detection is URL/structure-based (see §3, §4).

## 3. Bug 1 — location-mismatch notice + Brave re-open

### 3.1 Detection (URL city-slug compare, layered by confidence)

Add `_location_notice_for(coverage, menu_items, *, address)` beside
`_region_notice_for` in [menu_service.py](../../../safeplate/menu_service.py). It
has everything it needs: `coverage[].url`, `menu_items[].menu_source_url`, and the
diner `address`.

1. **Diner city** — reuse `discover._city_token(address)` → `"Cupertino"`,
   normalized to a slug (`cupertino`). No city ⇒ no notice (can't compare).
2. **Used source(s)** — the `menu_source_url`(s) that actually contributed the
   shown items.
3. **Same-location shortcut** — if a used source URL contains the diner-city slug
   (`/menu-cupertino`) ⇒ verified same location, no notice.
4. **Shown-city label** — tokenize the used source URL path + filename on
   non-alphanumerics; strip: restaurant-name tokens (we know the name), `menu`,
   `pdf`, pure-numeric/date tokens, and a small **menu-descriptor stoplist**
   (`brunch dinner lunch breakfast drinks dessert kids catering wine cocktail
   seasonal weekend new updated current food allergen nutrition`). A residual
   place-like slug (e.g. `santa-monica`) that ≠ diner city ⇒ **mismatch notice
   naming it**: "This menu is from the **Santa Monica** location, not Cupertino."
5. **Coverage-diff corroborator (high confidence)** — if a *different* discovered
   source in `coverage` contained the diner-city slug (e.g. `/menu-cupertino` was
   found) but the used items came from a source lacking it, fire the notice even
   without a clean label: "We couldn't confirm this is the Cupertino menu."

The menu-descriptor stoplist keeps `dinner-menu.pdf` from reading as a city. A
Bug-1 false positive only ever says "verify this is your location's menu" —
safety-neutral, never "safe."

### 3.2 Notice shape and rendering

`_structured_menu_response` adds `summary["locationNotice"]` (parallel to
`regionNotice`):

```json
{ "verified": false, "shownCity": "Santa Monica", "homeCity": "Cupertino",
  "confidence": "labeled" | "inferred" }
```

- `assemble_dossier` ([dossier.py:482](../../../safeplate/dossier.py)) copies it
  into `provenance.locationNotice`.
- **Fix the render path** in [dossier_template.html](../../../safeplate/dossier_template.html):
  build both banners from their label fields (mirroring the main app's
  `regionBannerHtml`, [app_template.html:2235](../../../safeplate/app_template.html)),
  not from a non-existent `.message`. Location banner copy: "This menu is from the
  restaurant's **{shownCity}** location — dishes and prep can differ from
  {homeCity}. Confirm with the {homeCity} location."

### 3.3 Brave re-open (Bug 1b)

The location mismatch is computed in `menu_service`, *after* discovery. To let it
influence the thinness gate we surface the signal *into* the extraction pass:
when the best on-site menu source's city contradicts the diner city, treat the
result as **not trustworthy for the thinness check**, so the gate at
[discover.py:749](../../../safeplate/extraction2/discover.py) opens even though
`len(result.items) >= _MENU_PDF_THIN`.

Concretely: extend the thinness condition to
`len(result.items) < _MENU_PDF_THIN or _menu_city_mismatch(best_source_url, address)`,
where `_menu_city_mismatch` reuses the §3.1 URL-slug compare (shared helper, one
implementation). This lets the Cupertino-specific Brave query run; if it recovers
a Cupertino menu, that supersedes the Santa Monica PDF and the notice clears. If
it recovers nothing, we still show the Santa Monica menu **with** the mismatch
notice — no regression, strictly more chances to be right.

Guardrails: the existing `_pdf_mentions` collision guard and `overall_deadline`
budget still apply; the re-open adds at most the 3 bounded menu-PDF queries.

## 4. Bug 2 — structural corroboration for country-name tells

In `detect_source_region`:

- **ccTLD tells stay decisive on their own** (URL host + ccTLD-bearing domains in
  the visible text). Unchanged — this preserves the Burger King ← NZ / Starbucks ←
  CH / Nando's ← GB cases the module was built for.
- A multiword country **name** (`_STRONG_NAME_SIGNALS`) counts **only when the
  same text carries an independent *structural* tell for that same country.** No
  ingredient veto (open decision resolved to option A, §7): an un-corroborated
  bare-prose country name never asserts a region.

Structural tells are a small, bounded table for exactly the 7 name-signal
countries (GB, NZ, ZA, SA, AE, KR, HK), each with its **calling code** and
**unambiguous currency token** (the ccTLD is already counted via the domain scan):

| ISO2 | calling code | currency token(s) |
| --- | --- | --- |
| NZ | `+64` | `NZ$`, `NZD` |
| GB | `+44` | `£`, `GBP` |
| ZA | `+27` | `ZAR` |
| SA | `+966` | `SAR`, `﷼` |
| AE | `+971` | `AED` |
| KR | `+82` | `₩`, `KRW` |
| HK | `+852` | `HK$`, `HKD` |

New helper `_structural_signals(text) -> set[str]` scans for these. In
`detect_source_region`, a name vote for country *C* survives only if *C* is in
(in-text ccTLD votes ∪ `_structural_signals(text)`).

- Sweet Maple wine / "NZ green mussels" on a US menu → name present, but no `.nz`,
  `+64`, or `NZ$` (doc is `$` + US ZIP) → **uncorroborated → dropped → no notice.** ✅
- Real NZ menu / NZ chart on neutral CDN with `.co.nz` footer → corroborated → fires. ✅

## 5. Files touched

**Bug 1**
- `safeplate/menu_service.py` — `_location_notice_for`, `summary["locationNotice"]`.
- `safeplate/extraction2/discover.py` — shared `_menu_city_mismatch` helper;
  extend the thinness gate (§3.3).
- `safeplate/dossier.py` — carry `locationNotice` into provenance.
- `safeplate/dossier_template.html` — render location + region banners from labels
  (fixes the `.message` gap).
- New small city-slug util (shared by menu_service + discover) + tests.

**Bug 2**
- `safeplate/extraction2/region.py` — `_structural_signals` + corroboration in
  `detect_source_region`.
- `tests/test_region_locale.py` — new cases + the change in §7.

## 6. Tests & invariants

- **Bug 2 unit:** Sweet Maple wine text → `None`; "NZ green mussels" on a US menu
  → `None`; real NZ menu with `+64`/`NZ$` → `NZ`. All existing ccTLD/domain-tell
  and false-friend tests stay green; `test_detect_strong_multiword_name` is
  updated per §7 (bare name → `None`, name + structural tell → `NZ`).
- **Bug 1 unit:** city-slug extraction (`sweet-maple-santa-monica-menu.pdf` →
  `santa-monica`; `dinner-menu.pdf` → no city; `menu-cupertino` → `cupertino`);
  notice layering incl. the coverage-diff path; `_menu_city_mismatch` truth table.
- **Regression:** frozen Sweet Maple fixture asserts **no** NZ region notice **and**
  a Santa-Monica location notice.
- **Invariant (protects the gate):** no scoring code changes; the nut path stays
  byte-identical. Bug 1's notice is additive; Bug 1b only *widens* when the Brave
  fallback may run (never narrows) and keeps all existing collision/budget guards.

## 7. Resolved decision — accept loss of bare-prose foreign claims (option A)

Pure structural corroboration (§4) drops **every** un-corroborated country name —
including legitimate provenance claims, not just ingredient origins. This
**reverses** the existing `test_detect_strong_multiword_name`
([test_region_locale.py:33](../../../tests/test_region_locale.py)):

```python
text = "Allergen guide — proudly made in New Zealand."
assert R.detect_source_region(text, "https://cdn.x.com/n.pdf") == "NZ"  # OLD behavior
```

"Proudly made in New Zealand" is a real foreign signal with no ccTLD/currency/phone
to corroborate it, so §4 returns `None` and no longer detects it. This is an
accepted safety-asymmetric tradeoff: we fix a false *positive* (wine) at the cost
of a false *negative* (bare-prose foreign claim with no structural cue).

**Decision: option A (accept it).** No ingredient veto list is added. Rationale:
keep the module simple and free of an open-ended ingredient blocklist; genuinely
foreign allergen charts/menus almost always carry a ccTLD, address, phone, or
currency somewhere, so the practical loss is small, and ccTLD detection still
covers the original Burger King/Starbucks/Nando's cases unchanged.

**Required change:** update `test_detect_strong_multiword_name` to assert the new
behavior — a bare-prose country name with no structural tell returns `None`; add a
sibling test showing the same text **with** a structural tell (e.g. append
"`Call +64 9 555 0100.`" or "`NZ$18`") still returns `NZ`, documenting the
corroboration boundary.

## 8. Sequencing

Bug 1 and Bug 2 are independent and can land as separate PRs. Suggested order:
Bug 2 (self-contained, unblocks the false alarm) → Bug 1 notice + render fix →
Bug 1b Brave re-open (depends on the shared city-mismatch helper).
