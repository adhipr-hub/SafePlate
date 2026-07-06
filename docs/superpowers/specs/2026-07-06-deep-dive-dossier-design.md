# Deep-Dive Dossier — prototype design

**Date:** 2026-07-06
**Status:** approved (build authorized)
**Type:** prototype / product exploration (Idea 1 from the "future of SafePlate" review)

## Summary

A **single-restaurant deep dive**: instead of scanning ~12 nearby places, the user
points at *one* restaurant, and SafePlate spends its full budget going deep on it —
streaming the crawl live ("watch it work") and then presenting a rich **safety
dossier** (verdict + evidence + dishes to watch + allergy-handling signals +
community anecdotes + provenance).

It is added to the **existing app** as two new, purely-additive routes
(`GET /dossier`, `GET /dossier/stream`). Existing routes and behavior are
untouched — production stays byte-identical. It **reuses the production pipeline
wholesale**; the prototype is orchestration + presentation, plus one new "deeper
site" lever.

## Scope decisions (locked with the user)

- **Focus:** both the *experience* (streaming dossier) and *capability* (deeper
  crawl), end-to-end.
- **Data mode:** live keys only (no demo fixtures). Every run is a real crawl.
- **Input:** restaurant **name + location** (resolved via the existing Places
  providers), also accepting a direct **website URL**.
- **Deeper levers (chosen):** (1) web-wide **allergy-anecdote mining** — already
  performed by `run_menu_extraction`'s community path (Brave search →
  LLM-classified quotes; Google Places reviews are *not* available on this key);
  (2) a **new deeper-site scan** (about / FAQ / allergen / contact pages + social
  links) for allergy-handling language.
- **Not chosen (out of scope):** per-dish whole-menu scoring; auto call-ahead
  script. (Phone number is still shown.)
- **Streaming granularity:** coarse — `run_menu_extraction` runs as a single
  honest "deep extract" stage (menu + allergen chart + community + scoring). No
  finer sub-events inside it, and no fake/scripted animation.
- **Profile default:** nuts (allergy severity), with on-page controls.

## Architecture

Additive, non-invasive. Three artifacts:

1. **`safeplate/dossier.py`** — the orchestrator + SSE generator. Public surface:
   - `iter_dossier_events(params: dict, *, demo_mode=False) -> Iterator[str]` —
     yields SSE-framed text (`event:`/`data:` blocks) as it runs the stages.
   - `dossier_html() -> str` — serves the page template, hot-reloading on mtime
     change (mirrors `api_server.app_html`).
   - `build_target(params) -> Target | None` — resolve name+location/URL to one
     restaurant.
   - `scan_deeper_site(website_url, *, user_agent, api_key, model) -> DeeperSite`
     — the new lever.
   - `assemble_dossier(...) -> dict` — the final payload.
2. **`safeplate/dossier_template.html`** — the dossier page. Reuses the design
   tokens from `app_template.html` (`:root`), the score-ring / provenance / risk
   vocabulary, and the WCAG "never color alone" rule.
3. **`api_server.py`** — two new branches inside the existing handler's `do_GET`,
   after `_check_auth()` (so the dossier is auth-gated like everything else):
   - `path == "/dossier"` → `_send_html(dossier_html())`.
   - `path == "/dossier/stream"` → stream `iter_dossier_events(query_params)` as
     `text/event-stream` (write each chunk via the existing broken-pipe-safe
     `_write_body`). This is the only edit to a production file; existing
     branches are unchanged.

   EventSource is GET-only, so the target + profile ride as query-string params.

## Stages (real, coarse, streamed)

1. **resolve** — `run_restaurant_search({location, provider, listMode:"prior",
   limit, radius})` (cheap: prior cards, no extraction) → pick the row whose name
   best matches the typed name. Yields the resolved name/address/website. If a
   direct URL was given, skip resolution and synthesize the target. **Terminal
   error** only if nothing resolves and no URL was supplied.
2. **deep_extract** — `run_menu_extraction(target_payload)` → the structured
   response (`summary` with tier/risk/confidence/`perAllergen`/`menuBackedRisk`
   incl. `riskiestItems`+`evidence`/`restaurantSignals`/`regionNotice`,
   `menuItems`, `communityQuotes`, `coverage`). One honest stage covering site
   crawl + allergen chart + community mining + scoring.
3. **deeper_site** *(new lever)* — fetch the homepage, discover up to ~4 internal
   links matching `about|faq|allerg|contact|dietary|nutrition`, fetch ≤3, run
   `extract_allergy_signals` (grounded quotes only) on each, and collect social
   links (instagram/facebook/x). Best-effort.
4. **assemble** — build the dossier payload from stages 2–3; emit `dossier` then
   `done`.

**SSE events:** `stage_start{key,label}`, `stage_done{key,summary}`,
`stage_error{key,message}`, `dossier{payload}`, `done`, `error{message}`, with
periodic `:` heartbeat comments to keep the connection alive during the slow
extract.

## Dossier payload → UI sections

- **Header** — name, address, cuisine, phone, rating.
- **Verdict** — risk word + score ring + confidence + "how we know" provenance
  tier (`evidenceBasis`). Risk is always carried by word + ring + text, never
  color alone.
- **Watch out for these** — `riskiestItems` with reasons; plus "N other dishes
  parsed, no named nuts" from the menu count. (No new per-dish scoring.)
- **Deeper-site signals** — grounded allergy-handling statements + flags
  (allergy-friendly / cross-contact / ask-staff / allergen-menu / nut-free) with
  source links; detected social links.
- **Community anecdotes** — `communityQuotes`; a reported reaction reads as a loud
  down-signal (safety-asymmetric framing).
- **Provenance & freshness** — sources used (`coverage`), region banner when the
  allergen data is from another region (`regionNotice`).

## Error handling (safety-asymmetric)

Every stage is wrapped. `resolve` failure is the one terminal error (emit `error`,
stop). Any other stage failure emits `stage_error`, **lowers confidence / says
"couldn't verify"**, and the dossier continues — never a false "safe". SSE writes
swallow `BrokenPipe/ConnectionReset`. Missing API keys emit a clear setup `error`
event. Same `_MAX_BODY_BYTES`/auth/security-header posture as the rest of the app
(streaming endpoint is GET, no body).

## Testing

Additive only (`tests/test_dossier.py`); production test count unaffected:

- Orchestrator with `run_restaurant_search` / `run_menu_extraction` /
  `scan_deeper_site` stubbed → assert the **stage sequence and SSE event
  framing**, and that a **failed `deeper_site` degrades to `stage_error` without
  changing the verdict toward "safe"**.
- `build_target` name-matching (exact, case-insensitive, direct-URL bypass, no
  match).
- `assemble_dossier` payload-shape test (all sections present; empty levers
  degrade gracefully).

Extraction/scoring/community stay covered by their existing suites (reused, not
reimplemented).

## Non-goals

No separate server or port; no changes to existing routes/behavior; no demo
fixtures; no per-dish full-menu scoring; no auto call-ahead script; no new
extraction or scoring logic (only orchestration + presentation + the deeper-site
lever).
