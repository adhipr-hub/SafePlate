# SafePlate

SafePlate is an AI-powered restaurant and travel food safety assistant for people
with allergies and dietary restrictions. It combines restaurant discovery,
menu-source crawling, deterministic menu extraction, optional Gemini validation,
and nut-allergy risk scoring in a local web app and CLI pipeline.

The current build can:

1. Take a typed location or browser latitude/longitude.
2. Query OpenStreetMap, Geoapify, or Google Places for nearby food places.
3. Rank restaurants by a cuisine/location nut-risk prior.
4. Load menu evidence on demand and update the drawer with menu-backed nut risk.
5. Show allergy-awareness signals, source coverage, and generated JSON/CSV outputs.

## Current MVP

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the collector:

```powershell
python scripts/fetch_restaurants.py --location "Berkeley, CA" --radius 1500 --limit 25
```

## Local App

SafePlate also has a local desktop-style app shell. On Windows, double-click:

```text
Start SafePlate App.bat
```

Or start it from PowerShell:

```powershell
python scripts/start_safeplate_app.py
```

For a deterministic walkthrough that does not call live providers or APIs:

```powershell
python scripts/start_safeplate_app.py --demo
```

Then open:

```text
http://127.0.0.1:8765
```

The local app has a modern single-page UI: type a location and it finds nearby
restaurants and ranks them by estimated nut risk (from the cuisine/location
prior), shown as a colour-coded score on each card. Click a card to open the
detail drawer, which loads menu evidence on demand, updates the risk summary
when menu items are available, and shows menu items with allergen/dietary chips,
allergy-awareness signals, coverage badges, and the risk rationale. Live mode
saves the same `data/` JSON/CSV outputs used by the rest of the pipeline. The
front-end lives in `safeplate/app_template.html`.

Provider behavior:

- If `GOOGLE_PLACES_API_KEY` is set before the app starts, Auto uses Google
  Places.
- If no paid/provider key is set, Auto falls back to OpenStreetMap.
- If `BRAVE_SEARCH_API_KEY` is set before the app starts, SafePlate can use
  Brave Search during menu evidence loading, but only after the normal website
  crawl finds no usable menu sources or the selected restaurant has no website.
- Browser location runs in the browser through `navigator.geolocation`; it works
  best on `localhost` / `127.0.0.1`.

Choose a provider with `--provider`:

```powershell
python scripts/fetch_restaurants.py --location "Berkeley, CA" --radius 1500 --limit 25 --provider osm
python scripts/fetch_restaurants.py --location "Berkeley, CA" --radius 1500 --limit 25 --provider geoapify
python scripts/fetch_restaurants.py --location "Berkeley, CA" --radius 1500 --limit 20 --provider google
python scripts/fetch_restaurants.py --location "Berkeley, CA" --radius 1500 --limit 25 --provider both
python scripts/fetch_restaurants.py --location "Berkeley, CA" --radius 1500 --limit 20 --provider all
```

Geoapify supports flexible categories and conditions from its Places API. For
example:

```powershell
python scripts/fetch_restaurants.py --location "Berkeley, CA" --provider geoapify --geoapify-categories catering
python scripts/fetch_restaurants.py --location "Berkeley, CA" --provider geoapify --geoapify-categories catering.restaurant,catering.cafe --geoapify-conditions wheelchair.yes
```

If `python` is not on your PATH, install Python from https://www.python.org/
or run the script from an environment that has Python available.

The script writes files like:

```text
data/restaurants_berkeley_ca_2026_06_08_153012.json
data/restaurants_berkeley_ca_2026_06_08_153012.csv
data/restaurants_berkeley_ca_2026_06_08_153012.summary.json
```

## Data Source

This starter version supports:

- Nominatim for geocoding.
- OpenStreetMap Overpass API for nearby restaurant-like places.
- Geoapify Places API for optional richer restaurant metadata.
- Google Places API for ratings, review counts, price level, operational status,
  and current opening-hours data.
- Brave Search API for optional website/menu-source recovery during menu
  evidence loading when the normal website crawl has no usable menu source.

OpenStreetMap is free, but public services have fair-use limits. Keep test
queries small while developing.

For friendlier API usage, set a custom user agent:

```powershell
$env:SAFEPLATE_USER_AGENT="SafePlate student project <your-email@example.com>"
```

To use Geoapify, create a free API key and set it before running:

```powershell
$env:GEOAPIFY_API_KEY="your-geoapify-api-key"
python scripts/fetch_restaurants.py --location "Berkeley, CA" --radius 1500 --limit 25 --provider geoapify
```

To use Google Places, set a Google Places API key before running:

```powershell
$env:GOOGLE_PLACES_API_KEY="your-google-places-api-key"
python scripts/fetch_restaurants.py --location "Berkeley, CA" --radius 1500 --limit 20 --provider google
```

Google Places Nearby Search currently returns up to 20 results per request in
this starter collector.

To use Brave Search fallback in the local app, set a Brave Search API key before
starting the app:

```powershell
$env:BRAVE_SEARCH_API_KEY="your-brave-search-api-key"
python scripts/start_safeplate_app.py
```

Brave Search does not replace Google Places, Geoapify, OpenStreetMap, or the
normal website crawl. It is used only as a verified fallback while loading menu
evidence: if no menu source is otherwise available, SafePlate searches for
likely official websites or menu pages, then only accepts a recovered restaurant
website when the result matches the restaurant name plus address/phone/location
evidence. Recovered websites are marked in the app and stored under
`raw_payload.safeplate_enrichment.brave_website_recovery`.

This project uses **Places API (New)**, not the legacy Places API. The Google
provider calls the `https://places.googleapis.com/v1/places:searchNearby`
endpoint and uses a field mask so SafePlate only asks for the fields needed for
the restaurant dataset.

Google Places does not currently provide structured restaurant menu items in
this project. A live test of the claimed `businessMenus` field returned an
invalid-field error, so SafePlate treats Google Places as a restaurant metadata
source, not a menu-data source.

## Output Fields

The normalized rows include:

- `name`
- `address`
- `latitude`
- `longitude`
- `distance_meters`
- `rating`
- `review_count`
- `price_level`
- `categories`
- `website_url`
- `phone_number`
- `opening_hours`
- `business_status`
- `is_open_now`
- `source_last_updated`
- `data_quality_score`
- `source_name`
- `source_id`
- `fetched_at`
- `raw_payload`

Some fields, like ratings and price level, are usually not available from
OpenStreetMap. They are included in the schema so the pipeline can later support
Google Places, Yelp, or another richer provider.

`business_status` is best-effort for OpenStreetMap. This starter collector marks
food places as `presumed_operational` when they appear as active OSM amenities,
but it does not guarantee the business is open or still operating.

## Data Quality Report

Each run prints and saves a quality summary with counts like:

```text
Data quality:
- 25/25 have names
- 14/25 have addresses
- 9/25 have websites
- 7/25 have phone numbers
- 11/25 have opening hours
- 18/25 have cuisine/category tags
- 4/25 have source freshness tags
- average quality score: 0.617
```

When you run `--provider both` or `--provider all`, the summary also compares
source coverage:

```text
Source coverage:
- 25 rows from geoapify
- 25 rows from openstreetmap
- 21 restaurants found in both sources
- 8 restaurants not covered in both
- 3 restaurants only found in geoapify
- 5 restaurants only found in openstreetmap
```

The source comparison uses a conservative match: same normalized restaurant name
and coordinates within about 75 meters.

This helps SafePlate separate "we found candidates" from "we trust this data
enough to build safety features on top of it."

## Menu Source Discovery

After restaurant collection, SafePlate can look for likely menu evidence on
restaurant websites:

```powershell
python scripts/find_menu_sources.py --url "https://example-restaurant.com" --restaurant-name "Example Restaurant"
```

You can also run it against a restaurant CSV created by `fetch_restaurants.py`:

```powershell
python scripts/find_menu_sources.py --restaurants-csv data/restaurants_berkeley_ca_2026_06_08_225753.csv
```

This script fetches each homepage with normal Python HTTP requests, then parses
HTML links, images, and Schema.org JSON-LD with Beautiful Soup. It saves likely
menu source candidates as JSON and CSV. By default, it keeps only validated
primary menu-like pages and filters out noisy food photos or generic ordering
links. Candidate types include:

- `website_link`
- `ordering_page`
- `pdf`
- `image`
- `nutrition_or_allergen_page`
- `schema_org_menu`

Ordering pages and image candidates are opt-in:

```powershell
python scripts/find_menu_sources.py --restaurants-csv data/restaurants_berkeley_ca_2026_06_08_225753.csv --include-ordering-pages
python scripts/find_menu_sources.py --restaurants-csv data/restaurants_berkeley_ca_2026_06_08_225753.csv --include-images
```

Static Beautiful Soup parsing is the current default because it was much faster
than the naive Playwright-first experiment while producing nearly identical
useful results on the NYC test set.

For a visual table you can open in your browser, add `--html-report`:

```powershell
python scripts/find_menu_sources.py --restaurants-csv data/restaurants_berkeley_ca_2026_06_08_225753.csv --html-report
```

Menu discovery also supports sitemap lookup, a shallow deeper crawl, and
location-aware menu preference. It also reads Schema.org JSON-LD fields such as
`hasMenu` and `menu` when restaurant websites publish structured menu metadata:

```powershell
python scripts/find_menu_sources.py --restaurants-csv data/restaurants_cupertino.csv --crawl-depth 2 --html-report
python scripts/find_menu_sources.py --url "https://example.com" --restaurant-name "Example" --location-hint "Cupertino"
```

In the local app, Brave Search can also recover menu sources when
`BRAVE_SEARCH_API_KEY` is configured. It only runs if the normal website crawl
finds no usable menu sources. Then SafePlate searches for direct menu pages, PDF
menus, and image-menu URLs tied to the same restaurant/domain/location. Those
candidates appear in the menu-source list with `Brave Search candidate` in the
reason field.

Rows include an `evidence_grade`:

- `A`: validated official menu-like page
- `B`: strong ordering/menu lead
- `C`: image or medium-confidence lead
- `D`: weak lead
- `F`: not fetchable

You can also render an HTML report from an existing menu-source CSV:

```powershell
python scripts/render_menu_report.py --menu-sources-csv data/menu_sources_example.csv
```

For a more polished dashboard over the current restaurant/menu extraction
outputs, render the SafePlate data studio:

```powershell
python scripts/render_safeplate_dashboard.py --menu-items-csv data/menu_items_example.csv --menu-text-csv data/menu_text_example.csv --restaurants-csv data/restaurants_example.csv
```

This creates a standalone `safeplate_dashboard_*.html` file with restaurant
navigation, source-method breakdowns, category coverage, menu search, and
Schema.org/HTML/dietary/allergen filters.

After finding menu sources, extract visible menu text from validated HTML pages.

For HTML menu pages, extraction fetches the page and passes the HTML through
Beautiful Soup. It first reads embedded Schema.org JSON-LD `MenuItem` records
when the restaurant publishes them, then falls back to visible-text price
parsing for anything structured data misses. The text extraction output includes
raw menu text, price counts, dietary terms, and allergy-relevant keyword hits.
The same command also creates a first-pass `menu_items_*.csv` and
`menu_items_*.json` with menu item candidates:

- restaurant name
- menu source URL
- category
- item name
- description
- price
- dietary keyword hits
- allergen keyword hits
- extraction confidence

The `extraction_method` field shows where each candidate came from, such as
`schema_org_menu_item`, `html_visible_text`, `pdf_text`, or OCR/vision methods.

This is still evidence collection, not safety classification.

Menu text extraction supports multiple evidence methods:

- `html_visible_text` for static HTML menu pages
- `html_listed_item` for price-less dishes listed in menu markup (`<li>` / menu
  containers); price is optional, the item name is what matters
- `schema_org_menu_item` / `schema_org_microdata` for structured menu data
- `embedded_json` for menus shipped as JSON in the page (Next.js, ordering widgets)
- `allergen_matrix` for dish-by-allergen grid tables (the allergen/nutrition
  matrices chains publish); maps each dish directly to the allergens it contains
- `pdf_text` for PDF menu text extraction with `pypdf`
- `gemini_image` for image menus, read with Gemini vision when `GEMINI_API_KEY` is set
- `gemini_url_context` for JS menus Gemini can fetch and read directly

The extraction HTML report summarizes how much each method and source type
contributed: text records, characters, price hits, item candidates, restaurants,
dietary keyword hits, and allergen keyword hits.

Dietary and allergen terms are literal evidence terms from source text at this
stage. For example, a `nuts` allergen hit means the menu/source text contained a
matched term such as `nuts`, `walnut`, or `pine nuts`. SafePlate is not yet
inferring hidden ingredients or making a safety guarantee.

## Gemini Menu Evidence Extraction

Given a menu-text CSV with the documented columns (raw menu text, price counts,
dietary terms, and allergy-relevant keyword hits), SafePlate can send that
evidence to Gemini for strict structured extraction:

```powershell
$env:GEMINI_API_KEY="your-gemini-api-key"
python scripts/extract_menu_evidence_gemini.py --menu-text-csv data/menu_text_example.csv
```

This stage reads the BeautifulSoup-cleaned text and asks Gemini to extract only
stated evidence into JSON:

- menu items
- prices
- dietary labels such as vegan, vegetarian, or gluten-free
- allergen mentions
- tofu, plant-protein, or other modification options
- allergy disclaimers
- cross-contact warnings
- instructions like notifying staff about allergies

The default model is `gemini-3.1-flash-lite`, which is the current low-latency
Flash-Lite model in the Gemini docs. You can override it:

```powershell
$env:GEMINI_MODEL="gemini-3.5-flash"
```

The command writes:

- `gemini_menu_evidence_*.json` for nested restaurant evidence
- `gemini_menu_evidence_*_items.csv` for flat menu item rows
- `gemini_menu_evidence_*_notes.csv` for flat allergy/diet/policy notes

For more complete menu coverage, provide a menu-items CSV of deterministic item
candidates (with the documented columns) as the backbone and let Gemini
clean/enrich those rows:

```powershell
python scripts/extract_menu_evidence_gemini.py --menu-text-csv data/menu_text_example.csv --menu-items-csv data/menu_items_example.csv
```

In this mode, Gemini is instructed to return one output row for every
`candidate_id` from the rule parser. That makes the LLM better for cleanup,
dietary/allergen labeling, and restaurant-level notes while the deterministic
parser stays responsible for broad menu item recall.

Do not commit API keys to the repo. Keep them in environment variables.

This Gemini stage is still evidence extraction, not final safety ranking. The
recommended ranking design is:

1. Extract evidence per restaurant.
2. Score each restaurant against a specific user dietary profile.
3. Rank restaurants together using those scores, confidence, restaurant quality,
   and user preferences such as "avoid Italian" or "prefer vegan options."

Image menus are read with Gemini vision when `GEMINI_API_KEY` is set; no local
OCR engine is required.

For JavaScript-rendered menus, an optional headless-browser fallback is available
via Playwright (`pip install playwright && playwright install chromium`), enabled
with `fetch_mode="dynamic"` / `"auto"`. It is off by default.

Menu discovery and text extraction respect each website's `robots.txt` before
fetching pages. If a page is disallowed, SafePlate skips it instead of scraping
it.

This step extracts first-pass menu item candidates, but it is still evidence
collection, not safety classification. The next major layer is strict LLM
extraction that turns cleaned evidence chunks into validated menu JSON without
inventing items, prices, allergens, or dietary claims.

## Project Layout

```text
fixtures/
  demo/
scripts/
  fetch_restaurants.py
  extract_menu_evidence_gemini.py
  find_menu_sources.py
  render_menu_report.py
  start_safeplate_app.py
safeplate/
  allergen_prior.py
  app_template.html
  config.py
  demo_fixtures.py
  export.py
  geo.py
  local_app.py
  menu_sources.py
  menu_text.py
  reports.py
  robots.py
  schemas.py
  schema_org.py
  providers/
    geoapify.py
    google_places.py
    osm.py
tests/
eval/
data/
```
