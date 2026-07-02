# Deploying SafePlate as a live website (Render)

This puts the **live** SafePlate app on a public HTTPS URL using Render's free tier.

> ⚠️ **Live mode spends real money.** A single search can trigger Google Places +
> ~12 Gemini menu extractions. Two protections are built in — an HTTP password and
> a per-IP rate limit — but you must **also** cap the spend on your API keys
> (Step 1). Do not skip that.

## What you need
- A **GitHub** account (free)
- A **Render** account (free) — <https://render.com>
- API keys you want to use: Google Places and/or Gemini (optionally Brave,
  Geoapify). The app still runs with **no keys** using free OpenStreetMap data,
  just with lower quality.

## Step 1 — Get keys and CAP THE SPEND (do this first)
- **Google Places:** Google Cloud Console → enable *Places API* → create an API
  key, then set a **budget alert + quota cap** so a runaway can't bill you.
- **Gemini:** Google AI Studio → create an API key in a project with billing
  limits (or use a free-tier key).
- **Brave / Geoapify** (optional): both have free tiers.

## Step 2 — Put the code on GitHub
The repo is already initialized and committed locally. Create an empty repo on
GitHub (e.g. `safeplate`), then:

```bash
git remote add origin https://github.com/<you>/safeplate.git
git push -u origin main
```

Your `.env` and `data/` are gitignored, so no secrets or local outputs are pushed.

## Step 3 — Create the Render service
1. Render Dashboard → **New** → **Web Service** → connect your GitHub repo.
2. Fill in:
   - **Runtime:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `python scripts/start_safeplate_app.py --host 0.0.0.0 --port $PORT --no-browser`
   - **Health check path:** `/healthz`
   - **Instance type:** Free
3. **Environment variables** (Advanced → Add):

   | Key | Value |
   | --- | --- |
   | `SAFEPLATE_PASSWORD` | a strong password — **REQUIRED**, gates the whole site |
   | `SAFEPLATE_USERNAME` | optional, defaults to `safeplate` |
   | `SAFEPLATE_RATE_LIMIT_PER_MIN` | `30` (paid requests per visitor per minute) |
   | `GOOGLE_PLACES_API_KEY` | your key (optional) |
   | `GEMINI_API_KEY` | your key (optional) |
   | `BRAVE_SEARCH_API_KEY` | your key (optional) |
   | `GEOAPIFY_API_KEY` | your key (optional) |
   | `SAFEPLATE_USER_AGENT` | `SafePlate <your-email>` |

4. **Create Web Service.** The first build takes a few minutes.

*(Alternative: point Render's **Blueprint** at the included [`render.yaml`](render.yaml)
and just fill the secret values. The manual dashboard path above is the most
reliable, though.)*

## Step 4 — Open it
Visit `https://<your-service>.onrender.com`. The browser prompts for the
username/password you set, then SafePlate loads. Because it's HTTPS, the
"use my location" button works for remote users too.

## How the protection works
- **Password:** when `SAFEPLATE_PASSWORD` is set, every page and API call requires
  HTTP Basic auth. If it is **unset, the site is open** — never deploy live
  without it.
- **Rate limit:** each visitor IP is capped at `SAFEPLATE_RATE_LIMIT_PER_MIN`
  search/menu calls per minute (HTTP 429 beyond that). Set it to `0` to disable.
- Both live in [`safeplate/local_app.py`](safeplate/local_app.py). Local runs are
  unaffected (no password set = open, which is fine on your own machine).

## Good to know (free-tier limits)
- The free instance **sleeps after ~15 min idle**; the next visit cold-starts
  (~30–60s).
- The server is Python's built-in `http.server` — fine for personal/demo traffic,
  not high load. Scaling up would mean moving to a WSGI/ASGI server (separate task).
- The disk is **ephemeral** (wiped on each deploy) and only used for caches, so
  that's fine.
