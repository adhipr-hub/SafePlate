# Dossier Playwright JS-Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Deep-Dive Dossier renders JS-built restaurant sites through the existing Playwright renderer, while every other path stays byte-identical static.

**Architecture:** Thread a `fetch_mode: str = "static"` parameter through the extraction chain (`extraction2.acquire.acquire` → `extraction2.discover.discover_and_extract` → `menu_service._extract_and_assess_structured`), driven by a `fetchMode` payload key that only `dossier._menu_payload` sets (to `"auto"`). The dossier's deeper-site internal pages also switch to `"auto"`. The extraction result cache gains a fetch-mode discriminator so static and auto runs never serve each other. Playwright becomes a required dependency with Chromium installed in the Docker build.

**Tech Stack:** Python 3.12+, Playwright (already implemented in `safeplate/dynamic_fetch.py` — this plan writes NO new rendering code), pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-07-dossier-playwright-design.md`.
- Every path other than the dossier keeps `fetch_mode="static"` by default; the full suite (688 tests as of writing) must stay green after every task.
- Valid fetch modes are exactly `"static"`, `"auto"`, `"dynamic"`; anything else falls back to `"static"`.
- The result-cache discriminator is unchanged for static runs (existing cache entries stay valid); non-static appends `+fm=<mode>`.
- No real browser in CI tests: all tests monkeypatch the fetch seam.
- Windows PowerShell/bash both available; run tests with `python -m pytest`.

---

### Task 1: `acquire()` accepts and forwards `fetch_mode`

**Files:**
- Modify: `safeplate/extraction2/acquire.py:48-75` (the `acquire` function)
- Test: `tests/test_dossier_fetch_mode.py` (create)

**Interfaces:**
- Produces: `acquire(url, *, source_type, user_agent=None, use_cache=True, fetch_mode="static")` — forwards `fetch_mode` to `fetch_html_page` for the HTML branch only (images/PDFs use `http_get` and ignore it).

- [ ] **Step 1: Write the failing test**

Create `tests/test_dossier_fetch_mode.py`:

```python
"""The Deep-Dive Dossier renders JS-built sites (fetch_mode="auto") while every
other path stays static. These tests lock the fetch_mode threading at each seam
with the fetch layer monkeypatched -- no real browser or network in CI."""

from __future__ import annotations

from types import SimpleNamespace

import safeplate.extraction2.acquire as acquire_mod
from safeplate.extraction2.acquire import acquire
from safeplate.page_fetch import HtmlPage


def _fake_page(url: str) -> HtmlPage:
    return HtmlPage(requested_url=url, final_url=url,
                    html="<html><body>Menu: Dal</body></html>",
                    fetch_method="static_html")


def test_acquire_forwards_fetch_mode(monkeypatch):
    calls = []

    def fake_fetch(url, *, user_agent, fetch_mode="static", use_cache=True):
        calls.append(fetch_mode)
        return _fake_page(url)

    monkeypatch.setattr(acquire_mod, "fetch_html_page", fake_fetch)
    acquire("http://example.test/menu", source_type="website_link",
            user_agent="t", fetch_mode="auto")
    assert calls == ["auto"]


def test_acquire_defaults_to_static(monkeypatch):
    calls = []

    def fake_fetch(url, *, user_agent, fetch_mode="static", use_cache=True):
        calls.append(fetch_mode)
        return _fake_page(url)

    monkeypatch.setattr(acquire_mod, "fetch_html_page", fake_fetch)
    acquire("http://example.test/menu", source_type="website_link", user_agent="t")
    assert calls == ["static"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dossier_fetch_mode.py -v`
Expected: both FAIL with `TypeError: acquire() got an unexpected keyword argument 'fetch_mode'` (first) and `TypeError: fake_fetch() ... 'fetch_mode'`-style mismatch or assertion failure (second), because `acquire` neither accepts nor forwards the kwarg.

- [ ] **Step 3: Implement**

In `safeplate/extraction2/acquire.py`, change the `acquire` signature and the HTML fetch line:

```python
def acquire(url: str, *, source_type: str, user_agent: str | None = None,
            use_cache: bool = True, fetch_mode: str = "static") -> Payload:
```

and at the bottom of the function (the HTML branch):

```python
    html = fetch_html_page(
        url, user_agent=user_agent, use_cache=use_cache, fetch_mode=fetch_mode
    ).html
    return payload_from_html(url, html, source_type=source_type)
```

Also extend the docstring's last paragraph with: `fetch_mode is forwarded to
fetch_html_page for HTML pages ("auto" lets the dossier render JS-built menus);
images and PDFs are plain HTTP and ignore it.`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dossier_fetch_mode.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python -m pytest tests/ -q` — expected: all pass (688 + 2 new).

```bash
git add safeplate/extraction2/acquire.py tests/test_dossier_fetch_mode.py
git commit -m "feat(extraction2): acquire() accepts fetch_mode for JS-rendered pages"
```

---

### Task 2: `discover_and_extract()` forwards `fetch_mode` and splits the result cache

**Files:**
- Modify: `safeplate/extraction2/discover.py` — `_cache_discriminator` (~line 468), `discover_and_extract` signature (~line 559), the `cache_disc = ...` line (~line 599), and the two `acquire(...)` call sites (~lines 625 and 788)
- Test: `tests/test_dossier_fetch_mode.py` (extend)

**Interfaces:**
- Consumes: `acquire(..., fetch_mode="static")` from Task 1.
- Produces: `discover_and_extract(website_url, *, user_agent, ..., use_cache=True, fetch_mode="static")`; `_cache_discriminator(website_url, restaurant_name, fetch_mode="static")` returns the existing value for static and appends `+fm=<mode>` otherwise.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dossier_fetch_mode.py`:

```python
from safeplate.extraction2.discover import _cache_discriminator


def test_cache_discriminator_unchanged_for_static():
    # Own-domain static: empty discriminator, exactly as before this feature
    # (existing cache entries must stay valid).
    assert _cache_discriminator("http://tandoori.example", "Tandoori Hut") == ""
    assert _cache_discriminator(
        "http://tandoori.example", "Tandoori Hut", fetch_mode="static") == ""


def test_cache_discriminator_splits_auto_runs():
    static = _cache_discriminator("http://tandoori.example", "Tandoori Hut")
    auto = _cache_discriminator(
        "http://tandoori.example", "Tandoori Hut", fetch_mode="auto")
    assert auto != static
    assert auto.endswith("+fm=auto")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dossier_fetch_mode.py -v -k discriminator`
Expected: FAIL with `TypeError: _cache_discriminator() got an unexpected keyword argument 'fetch_mode'`.

- [ ] **Step 3: Implement**

In `safeplate/extraction2/discover.py`:

(a) `_cache_discriminator` gains the parameter and suffix:

```python
def _cache_discriminator(
    website_url: str, restaurant_name: str | None, fetch_mode: str = "static"
) -> str:
    """Empty for normal own-domain sites (so chain branches share a cache entry);
    the normalized restaurant name on shared platforms (so two restaurants under the
    same aggregator domain don't collide). A non-static fetch_mode is appended so a
    static "no menu found" can never be replayed to the dossier's rendering run
    (which would silently suppress the browser), and vice versa."""
    host = urlparse(website_url or "").netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    if any(host == h or host.endswith("." + h) for h in _SHARED_PLATFORM_HOSTS):
        disc = " ".join((restaurant_name or "").split()).lower()
    else:
        disc = ""
    if fetch_mode != "static":
        disc = f"{disc}+fm={fetch_mode}"
    return disc
```

(b) `discover_and_extract` signature gains `fetch_mode: str = "static",` after
`use_cache: bool = True,`, and the docstring gains: `fetch_mode is forwarded to
every HTML acquisition ("auto" = render JS-empty pages with the headless
browser); it also keys the result cache so static and rendered runs never serve
each other.`

(c) The discriminator computation becomes:

```python
    cache_disc = _cache_discriminator(website_url, restaurant_name, fetch_mode)
```

(d) Both `acquire(...)` call sites gain the kwarg. At ~line 625:

```python
            return cand.url, acquire(cand.url, source_type=source_type,
                                     user_agent=user_agent, use_cache=use_cache,
                                     fetch_mode=fetch_mode)
```

(keep the existing kwargs exactly as they are; only add `fetch_mode=fetch_mode`).
Same one-kwarg addition at the ~line 788 PDF call site (harmless there; keeps the
call sites uniform).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dossier_fetch_mode.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python -m pytest tests/ -q` — expected: all pass.

```bash
git add safeplate/extraction2/discover.py tests/test_dossier_fetch_mode.py
git commit -m "feat(extraction2): thread fetch_mode through discovery + result-cache key"
```

---

### Task 3: `menu_service` reads `payload["fetchMode"]` and forwards it

**Files:**
- Modify: `safeplate/menu_service.py` — `_extract_and_assess_structured` signature (~line 51) and its `discover_and_extract(...)` call (~line 86); `_run_structured_menu_extraction` (~line 374, after the `no_cache = ...` line, and the `_extract_and_assess_structured(...)` call at ~line 386); new helper `_fetch_mode_from_payload` next to `_run_structured_menu_extraction`
- Test: `tests/test_dossier_fetch_mode.py` (extend)

**Interfaces:**
- Consumes: `discover_and_extract(..., fetch_mode="static")` from Task 2.
- Produces: `_fetch_mode_from_payload(payload: dict) -> str` (returns `"static"`, `"auto"`, or `"dynamic"`; anything else → `"static"`); `_extract_and_assess_structured(..., fetch_mode: str = "static")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dossier_fetch_mode.py`:

```python
import safeplate.extraction2.discover as discover_mod
from safeplate.allergen_score import Severity, UserProfile
from safeplate.menu_service import (
    _extract_and_assess_structured,
    _fetch_mode_from_payload,
)


def test_fetch_mode_from_payload_validates():
    assert _fetch_mode_from_payload({}) == "static"
    assert _fetch_mode_from_payload({"fetchMode": "auto"}) == "auto"
    assert _fetch_mode_from_payload({"fetchMode": "dynamic"}) == "dynamic"
    assert _fetch_mode_from_payload({"fetchMode": "browser!!"}) == "static"
    assert _fetch_mode_from_payload({"fetchMode": None}) == "static"


def _capture_discover(monkeypatch):
    captured = {}

    def fake_discover(website_url, **kwargs):
        captured.update(kwargs)
        return [], SimpleNamespace(
            items=[], allergy_signals=[], coverage=[], diet_signals=[]
        )

    monkeypatch.setattr(discover_mod, "discover_and_extract", fake_discover)
    return captured


def _run_extract(fetch_mode=None):
    kwargs = dict(
        name="Tandoori Hut", website_url="http://tandoori.example", address="",
        categories=[], latitude=None, longitude=None,
        profile=UserProfile.for_nuts(Severity.ALLERGY),
        user_agent="t", api_key=None,
    )
    if fetch_mode is not None:
        kwargs["fetch_mode"] = fetch_mode
    return _extract_and_assess_structured(**kwargs)


def test_extract_and_assess_forwards_fetch_mode(monkeypatch):
    captured = _capture_discover(monkeypatch)
    _run_extract(fetch_mode="auto")
    assert captured["fetch_mode"] == "auto"


def test_extract_and_assess_defaults_to_static(monkeypatch):
    # The drawer / search-card path never sets fetch_mode: default-equivalence.
    captured = _capture_discover(monkeypatch)
    _run_extract()
    assert captured["fetch_mode"] == "static"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dossier_fetch_mode.py -v -k "payload or forwards or defaults_to_static"`
Expected: FAIL — `ImportError: cannot import name '_fetch_mode_from_payload'` (and, once that exists, `TypeError`/`KeyError` on the others).

- [ ] **Step 3: Implement**

In `safeplate/menu_service.py`:

(a) Add the helper directly above `_run_structured_menu_extraction`:

```python
def _fetch_mode_from_payload(payload: dict[str, Any]) -> str:
    """How extraction fetches HTML. Only the Deep-Dive Dossier sets this ("auto":
    render JS-empty pages in the headless browser); every other caller gets the
    static default, keeping the list/drawer fast and byte-identical. Unknown
    values fall back to static rather than erroring a whole extraction."""
    mode = str(payload.get("fetchMode") or "static")
    return mode if mode in ("static", "auto", "dynamic") else "static"
```

(b) `_extract_and_assess_structured` signature: add `fetch_mode: str = "static",`
after `no_cache: bool = False,`. Forward it in the `discover_and_extract(` call
by adding `fetch_mode=fetch_mode,` after `use_cache=not no_cache,`.

(c) In `_run_structured_menu_extraction`, after the
`no_cache = bool(payload.get("noCache"))` line add:

```python
    fetch_mode = _fetch_mode_from_payload(payload)
```

and add `fetch_mode=fetch_mode,` to the `_extract_and_assess_structured(` call
(after `no_cache=no_cache,`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dossier_fetch_mode.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python -m pytest tests/ -q` — expected: all pass.

```bash
git add safeplate/menu_service.py tests/test_dossier_fetch_mode.py
git commit -m "feat(menu): payload fetchMode threads through structured extraction"
```

---

### Task 4: Dossier requests rendering (menu payload + deeper-scan pages)

**Files:**
- Modify: `safeplate/dossier.py` — `_menu_payload` (~line 463) and the internal-page fetch in `scan_deeper_site` (~line 421)
- Test: `tests/test_dossier_fetch_mode.py` (extend)

**Interfaces:**
- Consumes: `_fetch_mode_from_payload` semantics from Task 3 (payload key `"fetchMode"`).
- Produces: dossier menu payloads carry `"fetchMode": "auto"`; `scan_deeper_site` fetches internal candidate pages with `fetch_mode="auto"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dossier_fetch_mode.py`:

```python
import safeplate.page_fetch as page_fetch_mod
from safeplate.dossier import Target, _menu_payload, scan_deeper_site


def test_dossier_menu_payload_requests_auto_rendering():
    target = Target(name="Tandoori Hut", website_url="http://tandoori.example",
                    address="1 Curry Way", categories=["indian"],
                    latitude=None, longitude=None)
    payload = _menu_payload(target, {})
    assert payload["fetchMode"] == "auto"


def test_deeper_scan_internal_pages_use_auto(monkeypatch):
    calls = []

    def fake_fetch(url, *, user_agent, fetch_mode="static", use_cache=True):
        calls.append((url, fetch_mode))
        html = ('<html><body><a href="/allergy-info">Allergy info</a>'
                "</body></html>")
        return HtmlPage(requested_url=url, final_url=url, html=html,
                        fetch_method="static_html")

    monkeypatch.setattr(page_fetch_mod, "fetch_html_page", fake_fetch)
    result = scan_deeper_site("http://tandoori.example", user_agent="t",
                              api_key=None, model="gemini-test")
    # Homepage + the /allergy-info internal page, both fetched with "auto".
    assert [mode for _u, mode in calls] == ["auto", "auto"]
    assert result.pages_scanned  # scan ran (api_key=None stops before Gemini)
```

Note: `scan_deeper_site` does `from safeplate.page_fetch import fetch_html_page`
*inside* the function body, so patching `safeplate.page_fetch.fetch_html_page`
(the source module) intercepts it. `Target` is a dataclass whose only required
fields are `name` and `website_url` (the rest default), so the constructor
above is complete.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dossier_fetch_mode.py -v -k dossier_menu_payload`
Expected: FAIL with `KeyError: 'fetchMode'`; the deeper-scan test FAILS with modes `["auto", "static"]`.

- [ ] **Step 3: Implement**

In `safeplate/dossier.py`:

(a) In `_menu_payload`, add to the payload dict literal (after `"severity"`):

```python
        # Deep dive is the slow, thorough surface: let extraction render
        # JS-built menus (static-first; the browser only runs when the page
        # looks JS-empty). The list/drawer never send this key.
        "fetchMode": "auto",
```

(b) In `scan_deeper_site`, the internal-page loop changes from
`fetch_mode="static"` to:

```python
            page = fetch_html_page(url, user_agent=user_agent, fetch_mode="auto")
```

and update the nearby comment if one mentions static.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dossier_fetch_mode.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `python -m pytest tests/ -q` — expected: all pass.

```bash
git add safeplate/dossier.py tests/test_dossier_fetch_mode.py
git commit -m "feat(dossier): render JS-built sites (fetchMode=auto) in the deep dive"
```

---

### Task 5: Dependency + deploy (requirements, Dockerfile, render.yaml, DEPLOY.md)

**Files:**
- Modify: `requirements.txt` (the commented playwright line), `Dockerfile` (ENV block + dependency layer), `render.yaml` (`buildCommand`), `DEPLOY.md` (add a JS-rendering note)

**Interfaces:**
- Consumes: nothing from other tasks (independent).
- Produces: `playwright>=1.40.0` as a real dependency; Chromium available in the Docker image at `/opt/playwright`.

- [ ] **Step 1: requirements.txt**

Replace:

```
# Optional: headless-browser rendering for JavaScript menus (fetch_mode=dynamic).
# playwright>=1.40.0
```

with:

```
# Headless-browser rendering for JavaScript menus (the Deep-Dive Dossier's
# fetch_mode="auto"). The code degrades to static-only if the browser binary
# is missing; install it with: playwright install chromium
playwright>=1.40.0
```

- [ ] **Step 2: Dockerfile**

In the `ENV` block add (inside the same `ENV ... \` continuation, before `PORT=8765`):

```dockerfile
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
```

(Chromium installs as root during build but the app runs as `appuser`; the
default per-user `~/.cache/ms-playwright` path would be unreadable.)

After the `RUN pip install --no-cache-dir -r requirements.txt` line add:

```dockerfile
# Chromium (+ its system libraries) for JS-rendered menus in the Deep-Dive
# Dossier. Installed to PLAYWRIGHT_BROWSERS_PATH so the non-root runtime user
# can read it. If this layer is removed, the app still runs -- the dossier just
# degrades to static fetching.
RUN playwright install --with-deps chromium
```

- [ ] **Step 3: render.yaml**

Change:

```yaml
    buildCommand: pip install -r requirements.txt
```

to:

```yaml
    # `playwright install chromium` is best-effort on the native Python runtime
    # (system libraries may be incomplete there); the app degrades to static
    # fetching if Chromium can't run. The Dockerfile is the reliable path.
    buildCommand: pip install -r requirements.txt && playwright install chromium
```

- [ ] **Step 4: DEPLOY.md**

Add a short section (after whatever section describes the build/start commands):

```markdown
## JS-rendered menus (Playwright)

The Deep-Dive Dossier renders JavaScript-built sites with headless Chromium
(`playwright`). Locally: `pip install -r requirements.txt && playwright install
chromium`. In Docker the image installs Chromium with its system libraries
(reliable path). On Render's native Python runtime the build attempts
`playwright install chromium`; if the browser can't run there, the app degrades
gracefully to static fetching -- everything works, JS-only menus just fall back
to cuisine estimates.
```

- [ ] **Step 5: Verify locally, run the suite, commit**

Run: `python -c "from safeplate.dynamic_fetch import _HAS_PLAYWRIGHT; print(_HAS_PLAYWRIGHT)"`
Expected: `True` (Playwright is already installed on this machine).

Run: `python -m pytest tests/ -q` — expected: all pass.

```bash
git add requirements.txt Dockerfile render.yaml DEPLOY.md
git commit -m "build: playwright + Chromium required for dossier JS rendering"
```

---

### Task 6: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (698 = 688 baseline + 10 new).

- [ ] **Step 2: Live render smoke (local, network)**

Run:

```bash
python -c "
from safeplate.page_fetch import fetch_html_page
p = fetch_html_page('https://www.starbucks.com/menu', user_agent='SafePlateBot/0.1 (+local test)', fetch_mode='auto')
print(p.fetch_method, len(p.html))
"
```

Expected: prints either `static_html <n>` (site served real HTML statically) or
`dynamic_html <n>` with a large HTML size — proving the auto path runs end to
end without error. If the machine has no network, skip this step and note it.

- [ ] **Step 3: Dossier smoke via the app (optional, needs API keys)**

Start the app (`python scripts/start_safeplate_app.py --no-browser`), open
`/dossier`, and run a deep dive against a known JS-heavy restaurant site; the
menu-read stage should now produce items where it previously reported "no
machine-readable menu". This requires live provider keys; skip if unavailable.

- [ ] **Step 4: Final commit check**

Run: `git status --short` — expected: clean tree (everything committed in Tasks 1-5).
