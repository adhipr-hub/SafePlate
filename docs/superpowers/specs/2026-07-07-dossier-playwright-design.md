# Deep-Dive Dossier: JS-rendered sites via Playwright

**Date:** 2026-07-07
**Status:** Approved (Approach A)

## Problem

The Deep-Dive Dossier's menu extraction cannot read menus built by client-side
JavaScript. The extraction pipeline (`extraction2.acquire.acquire`) fetches HTML
with `fetch_html_page()` at its default `fetch_mode="static"`, so a JS-only menu
page arrives as an empty app shell and the dossier falls back to cuisine priors.
The dossier's deeper-site scan renders the homepage with `fetch_mode="auto"` but
fetches the internal candidate pages with `"static"`, so JS-built allergy pages
are missed there too.

SafePlate already ships the renderer: `safeplate/dynamic_fetch.py` (Playwright
Chromium; consent-wall dismissal, bounded LRU cache, SSRF guard via `net_guard`,
crash recovery, single-thread render lock) exposed through
`page_fetch.fetch_html_page(fetch_mode="auto")`, which fetches statically first
and renders only when the page "looks JS-empty" (app-root marker + small HTML).
This feature is wiring, not new rendering code.

## Scope (user-decided)

- **Deep Dive only.** The dossier's menu extraction and its deeper-site internal
  pages use `fetch_mode="auto"`. The search list, drawer (`/api/menu`), and eval
  harness keep the static default and stay byte-identical.
- **Required dependency + deploy.** `playwright` moves from a commented-out
  optional to a real requirement, and the deploy configs install Chromium. The
  code keeps degrading gracefully (static-only) when the browser is missing.

## Design

### 1. Thread `fetch_mode` through the extraction chain

Add `fetch_mode: str = "static"` to, in order:

- `extraction2.acquire.acquire()`: pass it to `fetch_html_page()` for the HTML
  branch (images/PDFs are unaffected; they use `http_get`).
- `extraction2.discover.discover_and_extract()`: accept and forward to every
  internal `acquire()` call.
- `menu_service._extract_and_assess_structured()`: accept and forward to
  `discover_and_extract()`.
- `menu_service._run_structured_menu_extraction()`: read `payload["fetchMode"]`
  (validated to `static` / `auto` / `dynamic`; anything else falls back to
  `static`) and forward it.

`dossier._menu_payload()` sets `"fetchMode": "auto"`. No other caller sets it,
so every existing path keeps the exact static behavior by default.

### 2. Dossier deeper-site scan

`dossier.scan_deeper_site()`: internal candidate pages switch from
`fetch_mode="static"` to `"auto"` (the homepage already uses `"auto"`).

### 3. Result-cache separation

`discover_and_extract`'s result cache key includes a discriminator
(`_cache_discriminator(website_url, restaurant_name)`). Append the fetch mode
**only when it is not `"static"`** (e.g. suffix `"+fm=auto"`): existing cache
entries stay valid for the static paths, while a drawer-cached static
"no menu found" can never be replayed to the dossier (which would silently
suppress the render), and a dossier `auto` result is never served to a path
that expects static behavior.

### 4. Dependency + deploy

- `requirements.txt`: uncomment `playwright>=1.40.0`.
- `Dockerfile`: set `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright` (the install runs
  as root but the app runs as `appuser`; the default `~/.cache` path would be
  unreadable), then `playwright install --with-deps chromium` in the dependency
  layer.
- `render.yaml`: `buildCommand: pip install -r requirements.txt && playwright
  install chromium` (best-effort on the native Python runtime; system libs may
  be incomplete there).
- `DEPLOY.md`: note that the Docker runtime is the reliable path for JS
  rendering in production; on the native runtime the app degrades to
  static-only if Chromium can't run.

### 5. Safety and performance (no new code)

All existing guards apply unchanged: SSRF (`net_guard.assert_public_url` inside
`dynamic_fetch`), robots.txt check (`page_fetch`), the single-thread render
lock, the rendered-HTML LRU cache, and the pipeline's 90 s per-restaurant
extraction budget, which bounds worst-case render count. The `auto` heuristic
keeps Chromium out of the common static case.

### 6. Error handling

Unchanged semantics: a failed render inside `auto` falls back to the static
HTML (existing `page_fetch` behavior); a missing Playwright install raises
`DynamicFetchError`, which `auto` converts into "use the static result". The
dossier's stage error fields keep reporting exceptions as today.

## Testing

Unit tests with `fetch_html_page` monkeypatched (no real browser in CI):

1. Dossier menu payload produces `acquire()` calls with `fetch_mode="auto"`.
2. Drawer/search path (`payload` without `fetchMode`) still calls with
   `"static"` — the default-equivalence guard.
3. Invalid `fetchMode` values in the payload fall back to `"static"`.
4. The result-cache discriminator differs between static and auto runs, and is
   unchanged (vs. today) for static runs.
5. Deeper-site scan fetches internal pages with `"auto"`.

Full suite (688 tests) must stay green.

## Out of scope (possible follow-ups)

- A "rendered in a browser" provenance chip in the dossier UI.
- Enabling `auto` for the drawer once render latency is acceptable there.
- A per-run render cap knob (the 90 s budget covers this today).
