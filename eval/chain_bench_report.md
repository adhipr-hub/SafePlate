# SafePlate Extraction Effectiveness — Global Chain Benchmark

**Scope:** `discover_and_extract` run against 23 major restaurant chains worldwide, each resolved through Google Places in its **home** region. Source data: `eval/datasets/chain_bench_results.jsonl`. All headline statistics below are the verifier's recomputed ground truth; analyst commentary is used only as context.

---

## 1. Headline verdict

**For raw menus, the pipeline is strong; for the safety-critical allergen data it exists to produce, it is weak — and where it does produce allergen data, provenance is the dominant hazard.** Every one of the 23 runs completed without error, 20/23 (87%) returned a non-empty menu, and the run pulled 2,949 items in total (median 76.5 per producing chain, up to 686). But only 16/23 chains carried *any* per-dish allergen data, and the aggregate allergen-signal density is just **489/2,949 = 16.6%** — roughly five of every six extracted items are safety-blind text with no nut verdict. Worse, the allergen data that *does* exist is frequently from the wrong place: 3 US flagships were scored from a foreign per-dish allergen matrix (Burger King from a Malta site, Starbucks from Switzerland, Taco Bell from a Tokyo-region S3 bucket), and ~10 of the 20 producers touched a non-official, shared-CDN, or aggregator host. Only **3 chains (Subway, Domino's, Dunkin')** clear the bar that actually matters for this product — substantial per-dish allergen coverage from an official domain in the correct home country — with a further handful clean-but-thin or high-coverage-but-foreign. For a nut-allergy tool, a confident allergen verdict built on another country's recipe and labeling law is the exact safety-asymmetric failure the product is meant to avoid, so raw recall overstates real effectiveness by a wide margin.

---

## 2. Scorecard (23 chains)

| Chain | Region | Items | Allergen items | Method | Top source host | Status |
|---|---|---:|---:|---|---|---|
| Domino's Pizza | US | 80 | 78 | matrix | cache.dominos.com | ✅ good |
| Dunkin' | US | 53 | 50 | matrix | www.dunkindonuts.com | ✅ good |
| Subway | US | 78 | 52 | matrix | media.subway.com | ✅ good |
| MOS Burger | JP | 65 | 14 | matrix + text | www.mos.jp | ✅ good (thin allergen) |
| Guzman y Gomez | AU | 253 | 13 | text + matrix | www.guzmanygomez.com.au | ✅ good (thin allergen) |
| Vapiano | DE | 193 | 10 | structured | www.vapiano.de | ✅ good (thin allergen) |
| Burger King | US | 75 | 63 | matrix | burgerking.com.mt (Malta) | ⚠️ wrong-source |
| Taco Bell | US | 54 | 49 | matrix | tacobellimg.s3.ap-northeast-1 (Tokyo) | ⚠️ wrong-source |
| Chick-fil-A | US | 58 | 46 | matrix | fastfoodinusa.com (3rd-party) | ⚠️ wrong-source |
| KFC | US | 47 | 44 | matrix | assets.ctfassets.net (opaque CDN) | ⚠️ wrong-source |
| Pret a Manger | GB | 372 | 20 | matrix + text | assets.ctfassets.net (opaque CDN, FR bleed) | ⚠️ wrong-source |
| Starbucks | US | 686 | 18 | matrix + text | www.starbucks.ch (Switzerland) | ⚠️ wrong-source |
| Wendy's | US | 52 | 15 | matrix + text | assets-global.website-files.com (Webflow CDN) | ⚠️ wrong-source |
| McDonald's | US | 210 | 11 | matrix + text | www.mcdonalds.com (+ Webflow CDN partial) | ⚠️ wrong-source (partial) |
| Greggs | GB | 364 | 5 | text + matrix | a.storyblok.com (shared CDN) | ⚠️ wrong-source |
| Costa Coffee | GB | 21 | 1 | matrix + text | weur-cdn.menuweb.menu (aggregator) | ⚠️ wrong-source |
| Nando's | GB | 125 | 0 | text | www.nandos.co.uk | ⚠️ menu-only-no-allergens |
| Tim Hortons | CA | 116 | 0 | text | www.blork.org (personal blog) | ⚠️ menu-only-no-allergens |
| Yoshinoya | JP | 39 | 0 | text | www.yoshinoya-holdings.com (intl menu) | ⚠️ menu-only-no-allergens |
| Chipotle Mexican Grill | US | 8 | 0 | text | www.chrie.org (unrelated org) | ⚠️ menu-only-no-allergens (thin) |
| Jollibee | PH | 0 | 0 | — | — (Places → onelink.me app deep-link) | ❌ failed |
| Haidilao Hot Pot | SG | 0 | 0 | — | — (Places → superhiinternational.com) | ❌ failed |
| Din Tai Fung | TW | 0 | 0 | — | — (12 JS allergy_info candidates, 0 extracted) | ❌ failed |

**Status tally:** ✅ good 6 (3 high-coverage-clean + 3 clean-but-thin) · ⚠️ wrong-source 10 · ⚠️ menu-only-no-allergens 4 · ❌ failed 3.

---

## 3. By dimension

### 3a. Geographic / source provenance — the critical safety finding

This is the dominant hazard, so it leads. Of the 20 chains that extracted any menu, only **7 (35%)** came from an official domain in the correct home country (Subway, Domino's, Dunkin', MOS Burger, Nando's, Vapiano, Guzman y Gomez), and the defensibly-clean count drops to **3** once you require *substantial allergen coverage* alongside clean provenance (Nando's has 0 allergens; Vapiano/MOS/GyG are clean but thin). Everything else is wrong-country, non-official, or an opaque shared CDN.

**The worst class — a per-dish allergen *matrix* sourced from the wrong country — hit 3 US flagships:**

- **Burger King (US):** 100% from `burgerking.com.mt` — Malta. 63 allergen rows built on EU recipes/suppliers/labeling law. The official `bk.com` coverage returned `found=false`.
- **Starbucks (US):** 100% from `www.starbucks.ch` — Switzerland. Sample items are unmistakably European ("Caffe Latte with semi skimmed milk", "almond drink", "soya drink", "oat drink"). All three official-domain coverage rows returned `found=false`.
- **Taco Bell (US):** 100% from `tacobellimg.s3.ap-northeast-1.amazonaws.com` — the AWS Tokyo region, i.e. Taco Bell Japan's asset bucket. 49 allergen rows applied to a US store.

**Two more delivered allergen matrices from non-official hosts:** Chick-fil-A from `fastfoodinusa.com` (46 rows, third-party site) and Wendy's from `assets-global.website-files.com` (15 rows, Webflow shared CDN).

**Opaque shared CDNs add a second tier of unverifiable provenance** (country and officialness cannot be confirmed from the data): KFC and Pret on Contentful `assets.ctfassets.net`, Greggs on Storyblok, McDonald's partially on Webflow. There is visible contamination here — Pret's items contain French-language baguettes ("Baguette Avocat, Concassé d'olives noires de Kalamata & Pignons de pin"), suggesting Pret France bleeding into a UK query.

**Counting every named-bad host, 10 of 20 producers touch a non-official / CDN / aggregator source** (Tim Hortons `blork.org`, Chipotle `chrie.org`, Chick-fil-A `fastfoodinusa.com`, Costa `menuweb.menu`, Wendy's + McDonald's `website-files.com`, Greggs `storyblok`, KFC + Pret `ctfassets`, Taco Bell S3), and **3 are clearly the wrong country** (BK `.mt`, Starbucks `.ch`, Taco Bell Tokyo region).

**Why this is acutely unsafe:** different countries have different recipes, suppliers, cross-contact practices, and allergen-labeling laws. A Malta or Swiss allergen chart applied to a US store can yield a confident-but-wrong "safe" verdict — the safety-asymmetric failure the product is built to prevent. **Root cause is structural:** in every critical case the official-domain coverage returned `found=false` ("no schema; LLM found nothing"), and the Brave fallback then won with a foreign/unofficial host because the fallback ranking has **no home-country guard and no official-domain preference**. The one encouraging signal: Dunkin' correctly *rejected* `www.dunkinksa.com` (Saudi, `found=false`) before settling on the official US `dunkindonuts.com` — proving a country/official guard is feasible.

### 3b. Recall (raw extraction)

Raw recall is high: **20/23 (87%)** produced a non-empty menu; **2,949 items total**, median 65 across all 23 (mean 128.2), producing-only median 76.5 (range 8–686). Tiering: 3 zero-item, 1 thin (<10: Chipotle 8), 11 decent (10–99), 8 rich (100+: Starbucks 686, Pret 372, Greggs 364, Guzman 253, McDonald's 210, Vapiano 193, Nando's 125, Tim Hortons 116). Caveat: the rich tier is heavily inflated by size/milk/language-variant explosion (Starbucks splits each drink across milk types; Greggs splits Large/Regular/Decaf; Pret carries duplicate EN+FR rows), so unique-dish recall is lower than the headline.

### 3c. Allergen coverage (the safety metric)

Only **16/23 (70%)** chains carry any per-dish allergen data, and density is shallow: **489/2,949 = 16.6%**. Only **7 chains cover ≥50% of their items** with allergen data — Domino's 78/80, Subway 52/78, Taco Bell 49/54, Chick-fil-A 46/58, Dunkin' 50/53, KFC 44/47, Burger King 63/75 — but **3 of those 7 carry hazardous provenance** (BK Malta, Taco Bell Tokyo, Chick-fil-A 3rd-party). The allergen-bearing method is almost entirely the **vision allergen-matrix**: every ≥50%-coverage chain used `gemini_allergen_matrix`. Plain `gemini_text` and even structured schema.org rarely carry allergens — Vapiano's 193 structured items yielded only 10 allergen rows (5.2%).

**Method split by item volume:** `gemini_text` 2,187 (74.2%) · vision matrix (`gemini_allergen_matrix` 565 + `allergen_matrix` 4) 569 (19.3%) · `schema_org_menu_item` 193 (6.5%, all Vapiano). So 74% of raw recall comes from the path that produces *no* allergen signal, and the product-useful matrix path carries under a fifth.

### 3d. Region recall

| Region | Ran | Got items | Got allergen data |
|---|---:|---:|---:|
| US | 11 | 11 | 10 |
| Canada | 1 | 1 | 0 |
| UK + DE | 5 | 5 | 4 |
| Asia–Oceania | 6 | 3 | 2 |

Western markets had near-perfect raw recall (UK alone 4/4/3; DE 1/1/1). **All 3 zero-item failures cluster in Asia–Oceania** (Jollibee, Haidilao, Din Tai Fung); the only Asia–Oceania producers were MOS Burger, Yoshinoya, and Guzman y Gomez.

---

## 4. Failure taxonomy

**(a) Zero candidates → zero items (upstream Places resolution, not extraction):**
- **Jollibee (PH):** Places resolved `jollibee.onelink.me` — an AppsFlyer app deep-link. `n_candidates=0`, aborted ~3.1s.
- **Haidilao (SG):** Places resolved `superhiinternational.com` — a wrong holding/parent site. `n_candidates=0`, ~3.5s.

**(b) Candidates but zero items (JS widget, no parseable doc, no PDF fallback):**
- **Din Tai Fung (TW):** harvested 12 `allergy_info` candidates (all `link2`) but `methods={}`, `coverage=[]`, 0 items after 31s — a JS allergen tool with no static matrix, and no `brave_pdf` fallback fired.

**(c) Thin stub:**
- **Chipotle (US):** JS-SPA official site yielded 4 stub items ("burritos", "bowls of food", "chips and salsa"); Brave then hit `chrie.org` (an unrelated hospitality-education org) for 4 more. 8 items, 0 allergens, 7 LLM calls wasted.

**(d) Full menu, zero allergen signal (items exist, no nut verdict — product-useless and unsafe):**
- **Nando's (GB)** 125/0 (own-site text, no matrix), **Tim Hortons (CA)** 116/0 (from `blork.org` personal blog; official site found nothing), **Yoshinoya (JP)** 39/0 (English/international menu from `yoshinoya-holdings.com`, not the resolved JP store), **Chipotle (US)** 8/0. Costa (GB) is effectively here too at 21/1. All used `gemini_text` only, never a matrix.

**Cross-cutting cause — Brave over-dependence:** Brave web-search fallback is the **sole** supplier of used data for **12/20** producers and contributes to 14/20; on-site link harvest alone covers only 6/20. Worse for the data that matters: **6 of the 7 highest-allergen-coverage results were Brave-won** (all but Subway). Brave delivers recall but, lacking a provenance guard, also delivers most of the wrong-country and third-party-host hazards above. The clean structured exemplar (Vapiano, on-site schema.org, conf 0.99, **0 LLM calls**) shows the low-cost ideal — but 19/20 producers needed LLM calls.

---

## 5. Top recommendations (ranked)

1. **Add a home-country + official-domain guard to source ranking (highest priority, safety-critical).** In every dangerous case the official domain returned `found=false` and the fallback silently substituted a foreign/unofficial host. Strongly down-rank foreign-TLD allergen PDFs (`.mt`, `.ch`), foreign cloud regions (S3 `ap-northeast-1`), and known third-party/aggregator hosts (`fastfoodinusa.com`, `blork.org`, `chrie.org`, `menuweb.menu`) when the query's home country is elsewhere. Dunkin's correct rejection of `dunkinksa.com` proves this is feasible.

2. **Surface provenance to the scorer and the user; never apply a foreign allergen matrix as a confident verdict.** Tag each extraction with country/officialness confidence and refuse (or heavily discount) cross-country allergen data rather than emitting a confident "safe." A missed risk is worse than a "we couldn't verify this for your region" message.

3. **Wire dynamic JS rendering into discovery.** All three zero-item failures and the Chipotle stub are SPA / JS-allergen-widget / app-deeplink cases. A headless render step (and forcing the `brave_pdf` fallback when on-site candidates extract 0) would recover Din Tai Fung's 12 allergy_info widgets and the Chipotle/Starbucks SPAs instead of leaning on Brave finding a foreign mirror.

4. **Fix Places website resolution for app-deeplink / holding-site results.** Jollibee (`onelink.me`) and Haidilao (`superhiinternational.com`) never had a real site to crawl. Detect app-store/deeplink/holding domains and fall back to a brand-name web search for the official site before extraction.

5. **Treat opaque shared CDNs (Contentful, Webflow, Storyblok) as unverified provenance.** KFC/Pret/Greggs/McDonald's pass home-country only by assumption; Pret shows real French-language contamination. Require an additional official-domain or brand-asset-path signal before trusting a shared-CDN allergen matrix.

6. **Down-weight variant-inflated item counts and prefer matrix coverage in ranking.** Collapse size/milk/language variants (Starbucks, Greggs, Pret) so the scorer optimizes for *allergen-bearing unique dishes*, not raw item volume. The size-variant grounding fix has already shipped; extend it to milk/language variants.

7. **Prefer the structured/vision-matrix paths over plain text.** `gemini_text` carries 74% of items but almost no allergen signal; the vision allergen-matrix is the only reliable allergen source. When a chain yields only text, mark it menu-only (no safety verdict) rather than presenting an apparently-rich but unscoreable menu.
