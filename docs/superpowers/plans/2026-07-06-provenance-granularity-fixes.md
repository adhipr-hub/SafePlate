# Provenance-Granularity Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop SafePlate from (a) silently showing a wrong-location menu and (b) falsely tagging a US menu's region from a wine/ingredient country name.

**Architecture:** Two independent fixes sharing one principle — *structural signals vote, menu prose doesn't.* Bug 2 adds a corroboration gate to `region.detect_source_region`. Bug 1 adds a dependency-free `locality` module that compares the diner's Places-address city against the menu-source URL slug, surfaces a "different location" notice (fixing a dossier render gap on the way), and re-opens the Brave menu-PDF fallback when a city mismatch is detected.

**Tech Stack:** Python 3.14, stdlib only (`re`, `urllib.parse`), pytest.

## Global Constraints

- **No scoring changes.** Both fixes touch provenance *notices* and *discovery gating* only. Per-nut scoring output must stay byte-identical (protects the offline quality gate).
- **Dependency-free modules.** `region.py` and the new `locality.py` import only stdlib (`re`, `urllib.parse`) — no extraction/network imports (avoids import cycles; mirrors `region.py`'s existing stance).
- **Structural-only detection.** Region/location is read from ccTLD, calling code, currency, Places address, and URL slug — never from free menu text.
- **Safety-asymmetric copy.** A location notice must never imply "safe"; it says "verify with the {home} location."
- **Commit after every task** with the exact message shown.
- **Test runner:** `python -m pytest` from repo root `C:/Users/adhip/Documents/SafePlate`.

---

### Task 1: Structural-signal helper in region.py

**Files:**
- Modify: `safeplate/extraction2/region.py` (add after `_STRONG_NAME_SIGNALS`, line 118-126)
- Test: `tests/test_region_locale.py`

**Interfaces:**
- Produces: `_structural_signals(low: str) -> set[str]` — ISO2 codes whose calling code or unambiguous currency appears in the lowercased visible text. Consumed by Task 2.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_region_locale.py`:

```python
def test_structural_signals_detects_calling_code_and_currency():
    from safeplate.extraction2 import region as R
    assert R._structural_signals("call +64 9 555 0100") == {"NZ"}
    assert R._structural_signals("mains from nz$18") == {"NZ"}
    assert R._structural_signals("total £12.50 gbp") == {"GB"}
    # 'sar' inside 'caesar' must NOT trip Saudi Arabia (word-boundary guard).
    assert R._structural_signals("caesar salad") == set()
    assert R._structural_signals("nothing here") == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_region_locale.py::test_structural_signals_detects_calling_code_and_currency -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_structural_signals'`

- [ ] **Step 3: Write minimal implementation**

In `safeplate/extraction2/region.py`, immediately after the `_STRONG_NAME_SIGNALS` dict (ends line 126), add:

```python
# Independent STRUCTURAL tells (calling code + unambiguous currency) for exactly the
# countries that have a multiword NAME signal above. A country NAME (e.g. "new
# zealand") may only assert a region when one of these ALSO appears -- a bare menu-
# prose mention (a wine's origin) must not brand the source foreign. Alpha currency
# codes are word-bounded so "sar" can't match inside "caesar", "aed" inside a word,
# etc. ccTLD tells are handled separately (the domain scan) and stay decisive alone.
_STRUCTURAL_TELL_RES: dict[str, re.Pattern[str]] = {
    "NZ": re.compile(r"\+64\b|nz\$|\bnzd\b"),
    "GB": re.compile(r"\+44\b|£|\bgbp\b"),
    "ZA": re.compile(r"\+27\b|\bzar\b"),
    "SA": re.compile(r"\+966\b|\bsar\b|﷼"),
    "AE": re.compile(r"\+971\b|\baed\b"),
    "KR": re.compile(r"\+82\b|₩|\bkrw\b"),
    "HK": re.compile(r"\+852\b|hk\$|\bhkd\b"),
}


def _structural_signals(low: str) -> set[str]:
    """ISO2 codes with an independent structural tell (calling code or unambiguous
    currency) present in the already-lowercased visible text. Used to corroborate a
    multiword country NAME before it may assert a source region (see
    detect_source_region). Returns an empty set when none are present."""
    return {code for code, rx in _STRUCTURAL_TELL_RES.items() if rx.search(low)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_region_locale.py::test_structural_signals_detects_calling_code_and_currency -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add safeplate/extraction2/region.py tests/test_region_locale.py
git commit -m "feat(region): structural-tell helper (calling code + currency) for corroboration"
```

---

### Task 2: Corroboration gate in detect_source_region

**Files:**
- Modify: `safeplate/extraction2/region.py:184-215` (`detect_source_region`)
- Test: `tests/test_region_locale.py:33-35` (update existing) + new cases

**Interfaces:**
- Consumes: `_structural_signals` (Task 1), existing `host_country`, `_visible_text`, `_DOMAIN_RE`, `_STRONG_NAME_SIGNALS`.
- Produces: unchanged public signature `detect_source_region(text: str, url: str = "") -> str | None`. New behavior: a country NAME vote counts only when corroborated by a same-country ccTLD/structural tell.

- [ ] **Step 1: Write the failing tests**

In `tests/test_region_locale.py`, REPLACE the body of `test_detect_strong_multiword_name` (currently lines 33-35) with:

```python
def test_detect_bare_prose_name_no_longer_fires_without_corroboration():
    # Option A (spec 2026-07-06 §7): a bare-prose country name with no structural
    # tell no longer asserts a region -- the Sweet Maple wine false-positive fix.
    text = "Allergen guide — proudly made in New Zealand."
    assert R.detect_source_region(text, "https://cdn.x.com/n.pdf") is None


def test_detect_name_fires_when_structurally_corroborated():
    # Same name, now with a structural tell -> region asserted (corroboration boundary).
    text = "Proudly made in New Zealand. Call +64 9 555 0100."
    assert R.detect_source_region(text, "https://cdn.x.com/n.pdf") == "NZ"


def test_detect_wine_origin_does_not_brand_us_menu_foreign():
    # The real Sweet Maple (Cupertino) regression: NZ appears only as a wine origin.
    text = ("Matua, Sauvignon Blanc, New Zealand — $14. "
            "The first New Zealand Sauvignon Blanc. Springfield, IL 62704.")
    assert R.detect_source_region(text, "https://www.sweetmaplesf.com/files/menu.pdf") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_region_locale.py::test_detect_wine_origin_does_not_brand_us_menu_foreign tests/test_region_locale.py::test_detect_bare_prose_name_no_longer_fires_without_corroboration -v`
Expected: FAIL — the bare-prose and wine texts currently return `"NZ"`, not `None`.

- [ ] **Step 3: Write minimal implementation**

In `safeplate/extraction2/region.py`, replace the body of `detect_source_region` from the `scores: dict[str, int] = {}` line through the name-signal loop (currently lines 201-210) with:

```python
    scores: dict[str, int] = {}
    # 1) ccTLD-bearing domains mentioned in the text (e.g. "burgerking.co.nz") --
    #    a decisive structural tell, and a primary voter on its own (unchanged).
    domain_votes: set[str] = set()
    for token in _DOMAIN_RE.findall(low):
        code = host_country(token)
        if code:
            scores[code] = scores.get(code, 0) + 1
            domain_votes.add(code)
    # Corroboration set = in-text ccTLD votes + calling-code/currency tells.
    structural = domain_votes | _structural_signals(low)
    # 2) unambiguous multiword country names -- counted ONLY when an independent
    #    structural tell for the SAME country is present, so a bare menu-prose
    #    mention (a wine's "New Zealand" origin) can't brand the source foreign.
    for code, phrases in _STRONG_NAME_SIGNALS.items():
        if code in structural and any(p in low for p in phrases):
            scores[code] = scores.get(code, 0) + 1
```

(Leave the `cc = host_country(...)` early-return, the `_visible_text` line, the `if not scores` guard, and the `winners` tie-break exactly as they are.)

- [ ] **Step 4: Run the full region suite to verify pass + no regressions**

Run: `python -m pytest tests/test_region_locale.py tests/test_region_and_geocode.py tests/test_api_capture_region.py -v`
Expected: PASS — new cases green; existing ccTLD/domain-tell cases (`test_detect_nz_from_content_on_neutral_host`, `test_detect_domain_in_text`, `test_detect_incidental_mention_does_not_beat_domain_tell`, the false-friend and HTML tests) all still pass.

- [ ] **Step 5: Commit**

```bash
git add safeplate/extraction2/region.py tests/test_region_locale.py
git commit -m "fix(region): country-name tells need structural corroboration (Bug 2)

A wine origin ('New Zealand Sauvignon Blanc') no longer brands a US menu as
foreign. ccTLD tells stay decisive; a multiword country name votes only with a
same-country ccTLD/calling-code/currency tell. Accepts option A (spec §7): bare-
prose foreign claims with no structural cue are no longer detected."
```

---

### Task 3: locality module (city-slug helpers)

**Files:**
- Create: `safeplate/extraction2/locality.py`
- Test: `tests/test_locality.py`

**Interfaces:**
- Produces (consumed by Tasks 4 and 6):
  - `city_from_address(address: str | None) -> str | None`
  - `url_has_city(url: str, city: str) -> bool`
  - `source_city_slug(url: str, restaurant_name: str = "") -> str | None`
  - `menu_city_mismatch(url: str, address: str | None, restaurant_name: str = "") -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_locality.py`:

```python
from safeplate.extraction2 import locality as L

SANTA_MONICA = ("https://www.sweetmaplesf.com/files/"
                "02-28-2026-sweet-maple-santa-monica-menu-02-27-2026-pdf.pdf")
CUPERTINO_ADDR = "20010 Stevens Creek Blvd, Cupertino, CA 95014, USA"


def test_city_from_address():
    assert L.city_from_address(CUPERTINO_ADDR) == "cupertino"
    assert L.city_from_address("Palo Alto, CA") == "palo-alto"
    assert L.city_from_address("") is None
    assert L.city_from_address("SingleField") is None


def test_source_city_slug_extracts_place_dropping_name_and_descriptors():
    assert L.source_city_slug(SANTA_MONICA, "Sweet Maple") == "santa-monica"
    assert L.source_city_slug(
        "https://x.com/menu-cupertino", "Sweet Maple") == "cupertino"
    # pure descriptor filename -> no city
    assert L.source_city_slug("https://x.com/files/dinner-menu.pdf", "Sweet Maple") is None


def test_url_has_city():
    assert L.url_has_city("https://x.com/menu-cupertino", "cupertino") is True
    assert L.url_has_city(SANTA_MONICA, "cupertino") is False


def test_menu_city_mismatch():
    # wrong-location PDF for a Cupertino diner -> mismatch
    assert L.menu_city_mismatch(SANTA_MONICA, CUPERTINO_ADDR, "Sweet Maple") is True
    # the diner's own city menu -> not a mismatch
    assert L.menu_city_mismatch(
        "https://www.sweetmaplesf.com/menu-cupertino", CUPERTINO_ADDR, "Sweet Maple") is False
    # unreadable city -> never assert a mismatch
    assert L.menu_city_mismatch("https://x.com/files/menu.pdf", CUPERTINO_ADDR, "Sweet Maple") is False
    assert L.menu_city_mismatch(SANTA_MONICA, "", "Sweet Maple") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_locality.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'safeplate.extraction2.locality'`

- [ ] **Step 3: Write minimal implementation**

Create `safeplate/extraction2/locality.py`:

```python
"""City/locality slug helpers for menu-source provenance (dependency-free).

A restaurant chain serves DIFFERENT menus per location, but SafePlate's menu
discovery can fall back to another location's menu (a Cupertino query whose JS
menu is unreadable falling back to a Santa Monica PDF). These helpers detect that
mismatch from STRUCTURE -- the diner's Places address city vs. the menu-source URL
slug -- never from menu prose, mirroring region.py's stance that only structural
signals may vote. Stdlib-only so menu_service and discover can both import it with
no cycle.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Tokens in a menu-source path/filename that are NOT locations, so e.g.
# "dinner-menu.pdf" or "files/.../menu.pdf" don't read as a city. Covers menu/
# section words and common upload-path segments.
_NON_CITY_TOKENS = {
    # menu / section descriptors
    "menu", "menus", "pdf", "pdfs", "brunch", "dinner", "lunch", "breakfast",
    "drinks", "drink", "dessert", "desserts", "kids", "kid", "catering", "wine",
    "wines", "cocktail", "cocktails", "seasonal", "weekend", "new", "updated",
    "current", "food", "allergen", "allergens", "nutrition", "final", "draft",
    "specials", "special", "main", "mains", "full", "sample", "bar", "happy",
    "hour", "holiday", "spring", "summer", "fall", "autumn", "winter",
    "takeout", "delivery", "order", "ordering", "togo", "dinein", "print",
    "online", "download", "view", "our", "home", "index", "page",
    # upload / CMS path segments
    "files", "file", "uploads", "upload", "assets", "asset", "wp", "content",
    "sites", "default", "documents", "docs", "img", "images", "image", "media",
    "static", "cdn", "s", "downloads",
}


def _slug(text: str) -> str:
    """Fold text to a hyphen slug of alphanumeric tokens: 'Santa Monica' ->
    'santa-monica'."""
    return "-".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def city_from_address(address: str | None) -> str | None:
    """The diner-city slug from a Places address ('..., Cupertino, CA 95014, USA'
    -> 'cupertino'). The 2nd comma segment is usually the city (mirrors
    discover._city_token). None when there is no such segment."""
    if not address:
        return None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    return _slug(parts[1]) if len(parts) >= 2 else None


def url_has_city(url: str, city: str) -> bool:
    """True when EVERY token of the city slug appears as a whole word in the URL
    path (so 'palo-alto' needs both 'palo' and 'alto'). Empty city -> False."""
    if not city:
        return False
    path = urlparse(url or "").path.lower()
    return all(re.search(rf"\b{re.escape(tok)}\b", path) for tok in city.split("-"))


def source_city_slug(url: str, restaurant_name: str = "") -> str | None:
    """Best-effort city slug from a menu-source URL's path + filename. Tokenizes on
    non-alphanumerics, drops restaurant-name tokens, menu/section descriptors and
    upload-path segments, and pure-number/date tokens; the residual 1-3 contiguous
    tokens are the location candidate. None when nothing plausible remains."""
    path = urlparse(url or "").path.lower()
    raw = [t for t in re.split(r"[^a-z0-9]+", path) if t]
    name_tokens = {t for t in re.split(r"[^a-z0-9]+", (restaurant_name or "").lower()) if t}
    residual = [
        t for t in raw
        if t not in name_tokens and t not in _NON_CITY_TOKENS and not t.isdigit()
    ]
    if not residual or len(residual) > 3:
        return None
    return "-".join(residual)


def menu_city_mismatch(url: str, address: str | None, restaurant_name: str = "") -> bool:
    """True when the menu-source URL names a city that clearly differs from the
    diner's city. False when they match, when the URL already contains the diner
    city, or when no city can be read (can't assert a mismatch -> don't warn)."""
    home = city_from_address(address)
    if not home:
        return False
    if url_has_city(url, home):
        return False
    shown = source_city_slug(url, restaurant_name)
    return bool(shown) and shown != home
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_locality.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add safeplate/extraction2/locality.py tests/test_locality.py
git commit -m "feat(locality): structural city-slug helpers for menu-source provenance"
```

---

### Task 4: location notice in menu_service

**Files:**
- Modify: `safeplate/menu_service.py` (add `_location_notice_for` after `_region_notice_for`, line 175; wire into `_structured_menu_response`, lines 248-256 and 295-306)
- Test: `tests/test_location_notice.py`

**Interfaces:**
- Consumes: `locality.city_from_address`, `locality.menu_city_mismatch`, `locality.source_city_slug`, `locality.url_has_city` (Task 3). Menu items expose `.menu_source_url`; coverage entries expose `.url` (both already used by `_region_notice_for`).
- Produces: `_location_notice_for(coverage, menu_items, *, address, restaurant_name) -> dict | None` and `summary["locationNotice"]` / top-level `response["locationNotice"]`. Shape: `{"verified": False, "shownCity": str, "homeCity": str, "confidence": "labeled"|"inferred"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_location_notice.py`:

```python
from types import SimpleNamespace
from safeplate.menu_service import _location_notice_for

CUP = "20010 Stevens Creek Blvd, Cupertino, CA 95014, USA"
SM_PDF = ("https://www.sweetmaplesf.com/files/"
          "02-28-2026-sweet-maple-santa-monica-menu-02-27-2026-pdf.pdf")
CUP_PAGE = "https://www.sweetmaplesf.com/menu-cupertino"


def _item(url):
    return SimpleNamespace(menu_source_url=url, allergen_terms=["nuts"])


def _cov(url):
    return SimpleNamespace(url=url, region="")


def test_labeled_mismatch_when_used_source_names_other_city():
    n = _location_notice_for([_cov(SM_PDF)], [_item(SM_PDF)],
                             address=CUP, restaurant_name="Sweet Maple")
    assert n == {"verified": False, "shownCity": "Santa Monica",
                 "homeCity": "Cupertino", "confidence": "labeled"}


def test_no_notice_when_used_source_is_home_city():
    n = _location_notice_for([_cov(CUP_PAGE)], [_item(CUP_PAGE)],
                             address=CUP, restaurant_name="Sweet Maple")
    assert n is None


def test_inferred_when_home_menu_discovered_but_not_used():
    # A Cupertino page was discovered (coverage) but items came from an unlabeled
    # source that isn't the Cupertino menu.
    used = "https://www.sweetmaplesf.com/files/menu.pdf"
    n = _location_notice_for([_cov(CUP_PAGE), _cov(used)], [_item(used)],
                             address=CUP, restaurant_name="Sweet Maple")
    assert n == {"verified": False, "shownCity": "",
                 "homeCity": "Cupertino", "confidence": "inferred"}


def test_no_notice_without_address():
    assert _location_notice_for([_cov(SM_PDF)], [_item(SM_PDF)],
                                address="", restaurant_name="Sweet Maple") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_location_notice.py -v`
Expected: FAIL with `ImportError: cannot import name '_location_notice_for'`

- [ ] **Step 3: Write the implementation**

In `safeplate/menu_service.py`, add this function immediately after `_region_notice_for` ends (line 175):

```python
def _location_notice_for(
    coverage: list[Any], menu_items: list[Any], *, address: str, restaurant_name: str
) -> dict[str, Any] | None:
    """Location-provenance notice: is the SHOWN menu from the diner's location, or
    did discovery fall back to another branch's menu? Structural only (Places
    address city vs. menu-source URL slug), never menu prose. We keep the menu but
    flag the mismatch -- 'flag, don't hide'. None when there's nothing to say."""
    from safeplate.extraction2 import locality

    home = locality.city_from_address(address)
    if not home:
        return None
    used = [getattr(it, "menu_source_url", "") for it in menu_items]
    used = [u for u in used if u]
    if not used:
        return None
    home_label = home.replace("-", " ").title()

    # (a) A used source explicitly names a DIFFERENT city -> labeled mismatch.
    for url in used:
        if locality.menu_city_mismatch(url, address, restaurant_name):
            shown = locality.source_city_slug(url, restaurant_name) or ""
            return {
                "verified": False,
                "shownCity": shown.replace("-", " ").title(),
                "homeCity": home_label,
                "confidence": "labeled",
            }

    # (b) Coverage-diff: a diner-city menu was DISCOVERED but not the source we
    #     used -> inferred mismatch (no clean label to show).
    home_in_used = any(locality.url_has_city(u, home) for u in used)
    home_in_coverage = any(
        locality.url_has_city(getattr(c, "url", ""), home) for c in coverage
    )
    if home_in_coverage and not home_in_used:
        return {
            "verified": False,
            "shownCity": "",
            "homeCity": home_label,
            "confidence": "inferred",
        }
    return None
```

Then wire it into `_structured_menu_response`. After the `region_notice = _region_notice_for(...)` call (lines 248-250) add:

```python
    location_notice = _location_notice_for(
        coverage, menu_items, address=address, restaurant_name=restaurant_name
    )
```

In the `summary` dict, directly after the `"regionNotice": region_notice,` line (255), add:

```python
        "locationNotice": location_notice,
```

And in the returned response dict, directly after the top-level `"regionNotice": region_notice,` line (304), add:

```python
        "locationNotice": location_notice,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_location_notice.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add safeplate/menu_service.py tests/test_location_notice.py
git commit -m "feat(menu): location-mismatch notice when a wrong-city menu is shown (Bug 1)"
```

---

### Task 5: render both provenance banners in the dossier

**Files:**
- Modify: `safeplate/dossier.py:520-526` (`assemble_dossier` provenance dict)
- Modify: `safeplate/dossier_template.html:404-408` (region banner block)
- Test: manual — dossier is a browser SSE page; verified via preview in Step 4.

**Interfaces:**
- Consumes: `summary["locationNotice"]` (Task 4) and the existing `summary["regionNotice"]` (`{verified, sourceLabel, ...}`).
- Produces: `provenance.locationNotice` in the dossier payload; both banners rendered from label fields (fixes the `.message` gap — `region_notice()` never sets `.message`, so today's banner never shows).

- [ ] **Step 1: Carry the notice into the dossier payload**

In `safeplate/dossier.py`, inside `assemble_dossier`'s returned `"provenance"` dict (lines 528-532), add a line after `"regionNotice": summary.get("regionNotice"),`:

```python
            "locationNotice": summary.get("locationNotice"),
```

- [ ] **Step 2: Replace the region banner block with a label-driven region + location render**

In `safeplate/dossier_template.html`, replace the current block (lines 404-408):

```javascript
      // Region banner
      const rn = prov.regionNotice;
      if(rn && rn.message){
        html += '<div class="region"><span aria-hidden="true">⚑</span><span>'+esc(rn.message)+'</span></div>';
      }
```

with:

```javascript
      // Provenance banners (built from label fields; region_notice()/locationNotice
      // carry no prebuilt .message, so render from their parts).
      const rn = prov.regionNotice;
      if(rn && rn.verified === false){
        const src = esc(rn.sourceLabel || "another region");
        html += '<div class="region"><span aria-hidden="true">⚑</span><span>Allergen data is from '+src+
                ' — recipes and labelling differ by country. Use as a guide and confirm with the restaurant.</span></div>';
      }
      const ln = prov.locationNotice;
      if(ln && ln.verified === false){
        const home = esc(ln.homeCity || "your area");
        const msg = ln.shownCity
          ? ('This menu is from the '+esc(ln.shownCity)+' location, not '+home+
             '. Dishes and prep can differ — confirm with the '+home+' location.')
          : ("We couldn't confirm this is the "+home+" menu; it may be from another location. Confirm with the restaurant.");
        html += '<div class="region"><span aria-hidden="true">⚑</span><span>'+msg+'</span></div>';
      }
```

- [ ] **Step 3: Start the dossier server**

Run: `python -m pytest tests/test_location_notice.py tests/test_locality.py tests/test_region_locale.py -q` (sanity — still green)
Then ensure the app is running (`preview_start` "SafePlate", or reuse the running server on 8771).

- [ ] **Step 4: Verify both banners render for Sweet Maple, Cupertino**

Drive the dossier stream and confirm the payload now carries a `locationNotice` (labeled "Santa Monica") and NO `regionNotice` (Bug 2 fix), then load the page to see the banner:

Run:
```bash
curl -s -N --max-time 240 "http://127.0.0.1:8771/dossier/stream?name=Sweet%20Maple&location=Cupertino%2C%20CA&website=https%3A%2F%2Fwww.sweetmaplesf.com%2F&address=20010%20Stevens%20Creek%20Blvd%2C%20Cupertino%2C%20CA%2095014%2C%20USA&severity=allergy&engine=rules" | grep -o '"locationNotice":[^}]*}\|"regionNotice":[^}]*}'
```
Expected: a `locationNotice` with `"shownCity":"Santa Monica"` and `"verified":false`; `regionNotice` is `null` (no NZ tag). Then use `preview_snapshot`/`preview_screenshot` on `/dossier` after running the same query in the UI to confirm the "This menu is from the Santa Monica location" banner is visible.

- [ ] **Step 5: Commit**

```bash
git add safeplate/dossier.py safeplate/dossier_template.html
git commit -m "fix(dossier): render region + location provenance banners from labels

The dossier checked regionNotice.message, which region_notice() never sets, so no
provenance banner ever showed. Render both banners from their label fields and add
the new location-mismatch banner (Bug 1)."
```

---

### Task 6: re-open the Brave menu-PDF fallback on a city mismatch (Bug 1b)

**Files:**
- Modify: `safeplate/extraction2/discover.py:749-784` (the thinness gate inside `discover_and_extract`)
- Test: `tests/test_brave_reopen_gate.py`

**Interfaces:**
- Consumes: `locality.menu_city_mismatch` (Task 3). In-scope locals at the gate: `result.items` (each has `.menu_source_url`), `address`, `restaurant_name`, `brave_api_key`, `api_key`, `overall_deadline`, `_MENU_PDF_THIN`.
- Produces: the gate condition also fires when the used menu source's city contradicts the diner's, and the inner stop no longer breaks early while mismatched. Adds a testable helper `_used_menu_city_mismatch(result_items, address, restaurant_name) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_brave_reopen_gate.py`:

```python
from types import SimpleNamespace
from safeplate.extraction2.discover import _used_menu_city_mismatch

CUP = "20010 Stevens Creek Blvd, Cupertino, CA 95014, USA"
SM_PDF = ("https://www.sweetmaplesf.com/files/"
          "02-28-2026-sweet-maple-santa-monica-menu-02-27-2026-pdf.pdf")
CUP_PAGE = "https://www.sweetmaplesf.com/menu-cupertino"


def _items(url):
    return [SimpleNamespace(item_name="x", menu_source_url=url)]


def test_used_menu_city_mismatch_true_for_wrong_city():
    assert _used_menu_city_mismatch(_items(SM_PDF), CUP, "Sweet Maple") is True


def test_used_menu_city_mismatch_false_for_home_city():
    assert _used_menu_city_mismatch(_items(CUP_PAGE), CUP, "Sweet Maple") is False


def test_used_menu_city_mismatch_false_without_address():
    assert _used_menu_city_mismatch(_items(SM_PDF), None, "Sweet Maple") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_brave_reopen_gate.py -v`
Expected: FAIL with `ImportError: cannot import name '_used_menu_city_mismatch'`

- [ ] **Step 3: Add the helper and widen the gate**

In `safeplate/extraction2/discover.py`, add this module-level helper just above `discover_and_extract` (before line 541):

```python
def _used_menu_city_mismatch(result_items, address, restaurant_name) -> bool:
    """True when a menu source that produced the current items names a city that
    contradicts the diner's -- i.e. we're showing another location's menu, so the
    off-site Brave menu-PDF hunt should re-open even if the item count isn't thin."""
    from safeplate.extraction2 import locality

    if not address:
        return False
    seen: set[str] = set()
    for it in result_items:
        url = getattr(it, "menu_source_url", "") or ""
        if url and url not in seen:
            seen.add(url)
            if locality.menu_city_mismatch(url, address, restaurant_name or ""):
                return True
    return False
```

Then, in `discover_and_extract`, immediately before the thinness gate (line 749) add:

```python
        _city_mismatch = _used_menu_city_mismatch(result.items, address, restaurant_name)
```

Change the gate condition (line 753) from:

```python
        and len(result.items) < _MENU_PDF_THIN
```

to:

```python
        and (len(result.items) < _MENU_PDF_THIN or _city_mismatch)
```

And change the inner early-stop (line 783) from:

```python
            if len(result.items) >= _MENU_PDF_THIN:
                break
```

to:

```python
            if not _city_mismatch and len(result.items) >= _MENU_PDF_THIN:
                break
```

Note (known limitation, in scope per spec §3.3): recovered correct-city items are MERGED with the existing ones rather than replacing them; the location notice still flags provenance. Purging wrong-city items is out of scope.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_brave_reopen_gate.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add safeplate/extraction2/discover.py tests/test_brave_reopen_gate.py
git commit -m "feat(discover): re-open Brave menu-PDF fallback on a city mismatch (Bug 1b)

A wrong-location menu with >=8 items no longer suppresses the off-site hunt for
the diner's actual location menu; the thinness gate now also opens on a used-
source city mismatch."
```

---

### Task 7: Sweet Maple end-to-end regression + full-suite gate

**Files:**
- Create: `tests/test_sweet_maple_regression.py`
- Test: itself + the whole suite.

**Interfaces:**
- Consumes: `detect_source_region` (Task 2), `_location_notice_for` (Task 4), `menu_city_mismatch` (Task 3). No network — fabricated inputs mirroring the real Sweet Maple coverage.

- [ ] **Step 1: Write the regression test**

Create `tests/test_sweet_maple_regression.py`:

```python
"""Locks the exact Sweet Maple (Cupertino) defects found on 2026-07-06:
a wine-origin 'New Zealand' must NOT tag the US menu foreign, and the Santa Monica
fallback menu MUST raise a location-mismatch notice."""
from types import SimpleNamespace

from safeplate.extraction2.region import detect_source_region
from safeplate.menu_service import _location_notice_for

CUP = "20010 Stevens Creek Blvd, Cupertino, CA 95014, USA"
SM_PDF = ("https://www.sweetmaplesf.com/files/"
          "02-28-2026-sweet-maple-santa-monica-menu-02-27-2026-pdf.pdf")
WINE_TEXT = ("Matua, Sauvignon Blanc, New Zealand $14. "
             "The first New Zealand Sauvignon Blanc. 20010 Stevens Creek Blvd.")


def test_wine_origin_does_not_tag_region():
    assert detect_source_region(WINE_TEXT, SM_PDF) is None


def test_santa_monica_fallback_raises_location_notice():
    items = [SimpleNamespace(menu_source_url=SM_PDF, allergen_terms=["nuts"])]
    coverage = [SimpleNamespace(url=SM_PDF, region="")]
    n = _location_notice_for(coverage, items, address=CUP, restaurant_name="Sweet Maple")
    assert n["confidence"] == "labeled"
    assert n["shownCity"] == "Santa Monica"
    assert n["homeCity"] == "Cupertino"
    assert n["verified"] is False
```

- [ ] **Step 2: Run the regression to verify it passes**

Run: `python -m pytest tests/test_sweet_maple_regression.py -v`
Expected: PASS (both tests)

- [ ] **Step 3: Run the full provenance-related suite + a scoring smoke to confirm no regressions**

Run:
```bash
python -m pytest tests/test_region_locale.py tests/test_region_and_geocode.py tests/test_api_capture_region.py tests/test_locality.py tests/test_location_notice.py tests/test_brave_reopen_gate.py tests/test_sweet_maple_regression.py -q
```
Expected: PASS (all).

Then confirm scoring output is untouched (the invariant). Run the existing allergen-scoring test module:
```bash
python -m pytest tests/ -k "score or allergen or nut" -q
```
Expected: PASS — no scoring test changes (both fixes are provenance/discovery only).

- [ ] **Step 4: Commit**

```bash
git add tests/test_sweet_maple_regression.py
git commit -m "test(provenance): lock the Sweet Maple region + location regressions"
```

---

## Self-Review

**Spec coverage:**
- Bug 1 detection (URL city-slug compare, layered w/ coverage-diff) → Tasks 3, 4. ✔
- Bug 1 "flag, don't hide" notice + dossier render (incl. `.message` gap) → Tasks 4, 5. ✔
- Bug 1b Brave re-open → Task 6. ✔
- Bug 2 structural corroboration (ccTLD decisive; name needs structural tell) → Tasks 1, 2. ✔
- Option A decision (bare-prose name → None; test updated) → Task 2. ✔
- Tests + Sweet Maple regression + scoring invariant → Tasks 2, 3, 4, 6, 7. ✔

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✔

**Type consistency:** `menu_city_mismatch`, `source_city_slug`, `url_has_city`, `city_from_address`, `_structural_signals`, `_used_menu_city_mismatch`, and the `{verified, shownCity, homeCity, confidence}` notice shape are used identically across Tasks 3, 4, 6, 7. ✔

**Known limitations (documented, in-scope per spec):** Task 6 merges (not replaces) recovered items; `source_city_slug` may occasionally treat an unusual non-city residual token as a city (Bug-1 false positive is safety-neutral — it only says "verify with the {home} location").
