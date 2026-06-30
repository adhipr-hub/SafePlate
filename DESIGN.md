---
name: SafePlate
description: Allergy-aware dining decisions you can trust — warm editorial calm, a living green aurora, risk graded in color.
colors:
  page: "#F7F3EA"
  paper2: "#FAF7F0"
  surface: "#FDFBF6"
  border: "#E6DECF"
  border-strong: "#CFC4B2"
  ink: "#211B16"
  ink-muted: "#4A4039"
  ink-faint: "#5E544B"
  green-deep: "#166534"
  green-mid: "#16A34A"
  green-vivid: "#22C55E"
  green-tint: "#DCFCE7"
  green-bright: "#1B7A45"
  chip-on-border: "#9DE3B7"
  mint: "#86EFAC"
  hero-1: "#12492D"
  hero-2: "#0B3A22"
  hero-ink: "#F3FBF6"
  aurora-forest: "#0F4A2E"
  aurora-mid: "#16A34A"
  aurora-mint: "#86EFAC"
  aurora-gold: "rgba(214,178,120,0.10)"
  risk-low-ink: "#15803D"
  risk-med-ink: "#92400E"
  risk-high-ink: "#991B1B"
  risk-low-ring: "#22C55E"
  risk-med-ring: "#FBBF24"
  risk-high-ring: "#F87171"
  risk-low-tint: "#F0FDF4"
  risk-med-tint: "#FFFBEB"
  risk-high-tint: "#FEF2F2"
  risk-low-border: "#BBF7D0"
  risk-med-border: "#FDE68A"
  risk-high-border: "#FECACA"
  signal-blue: "#2563EB"
  signal-blue-tint: "#EFF4FF"
  signal-blue-border: "#CFE0FF"
  ai-violet: "#6D28D9"
  ai-violet-tint: "#EDE9FE"
  ai-violet-border: "#DDD0F7"
  callahead-purple: "#7C3AED"
  star: "#FBBF24"
  mchip-nut-bg: "#FEE2E2"
  mchip-other-bg: "#FEF9C3"
  mchip-other-border: "#FEF08A"
  mchip-other-ink: "#854D0E"
  scrim: "rgba(20,14,8,0.34)"
typography:
  display:
    fontFamily: "Playfair Display, Georgia, serif"
    fontSize: "clamp(40px, 6vw, 72px)"
    fontWeight: 400
    lineHeight: 1.02
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Playfair Display, Georgia, serif"
    fontSize: "clamp(28px, 4vw, 42px)"
    fontWeight: 400
    lineHeight: 1.08
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Hanken Grotesk, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Hanken Grotesk, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "-0.005em"
  label:
    fontFamily: "Hanken Grotesk, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.14em"
  figure:
    fontFamily: "Spline Sans Mono, ui-monospace, monospace"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1
    letterSpacing: "-0.01em"
rounded:
  xxs: "4px"
  hair: "5px"
  chip: "6px"
  tag: "7px"
  xs: "8px"
  ctl: "9px"
  ctl2: "10px"
  sm: "12px"
  md: "14px"
  lg: "18px"
  xl: "24px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  xxl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.green-deep}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "0 20px"
    height: "50px"
  button-ghost:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "50px"
  card-restaurant:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "17px"
  input-search:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "14px"
  chip-allergen:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.pill}"
    padding: "7px 13px"
  chip-allergen-on:
    backgroundColor: "{colors.green-tint}"
    textColor: "{colors.green-deep}"
    rounded: "{rounded.pill}"
    padding: "7px 13px"
---

# Design System: SafePlate

> Source of truth for tokens is `safeplate/app_template.html` (`:root`). This file
> documents that system; when they disagree, the stylesheet wins — update this doc.

## 1. Overview

**Creative North Star: "Editorial Aurora"**

SafePlate reads like a calm, premium editorial — a careful expert laying findings on
warm paper — with one living signature: a slow green-to-mint **aurora** that drifts
behind the hero. The mood is unhurried, confident, and legible: a verdict you can act
on, plus the evidence you can inspect. It's a safety-critical tool, so the design earns
trust by being transparent and warm, never loud.

The field is **warm bone paper** (`#F7F3EA`) carrying softly-lifted near-white surfaces
(`#FDFBF6`). The brand moment is the **aurora** — a drifting forest→mint radial wash
(`#0F4A2E → #16A34A → #86EFAC`) behind the hero, atmosphere only. Around it sits a
disciplined green brand ramp and a small, semantically strict risk palette (green /
amber / red). Type pairs **Playfair Display** — a high-contrast serif used at its
regular weight (with italics for emphasis) for display warmth — against **Hanken
Grotesk** for clean, legible UI/body, with **Spline Sans Mono** for figures (scores,
prices, counts). Color is rationed: green means *us / safe / confirmed*, and the three
risk hues are reserved exclusively for grading allergen risk.

What this system explicitly rejects: the **hype-y AI SaaS** look (gradient-drenched
heroes, buzzword theatrics, manufactured urgency — including decorative vanity
metrics); the **childish / cutesy wellness** register (pastels, mascots, gamified
cheer); and the **generic crowd-review app** (star-rating soup, dense undifferentiated
listings, opinion over evidence). SafePlate leads with evidence and provenance.

**Key Characteristics:**
- Warm bone paper + softly-lifted near-white surfaces; a drifting green aurora as the brand moment; calm editorial density.
- One brand color (green) used for identity and "safe / confirmed", rationed hard.
- A strict 3-hue risk palette (green/amber/red) that never bleeds into decoration.
- Serif display warmth (Playfair Display) over neutral, legible Hanken Grotesk UI text; Spline Sans Mono for figures.
- Flat at rest; soft, warm ambient shadows that deepen on hover, focus, and for overlays.
- Provenance is a first-class visual language — every score shows "how we know".

## 2. Colors

A warm-neutral foundation with a single green brand voice and a tightly-scoped
safety-signal palette; every saturated color carries meaning.

### Primary
- **Brand Green** — three steps for one voice. **Deep Green** (`#166534`) for text on
  tints and the "confirmed / safe" state; **Mid Green** (`#16A34A`) for links and brand
  accents; **Vivid Green** (`#22C55E`) for the low-risk score ring and live status pips.
  **Green Tint** (`#DCFCE7`) backs selected chips and positive badges. The primary CTA
  is a deep-green gradient (`#15803D → #0F4A2E`), AA-safe for white text at both stops.
- **The Aurora** — the signature. A slow-drifting radial wash of forest→mid→mint
  (`#0F4A2E`, `#16A34A`, `#86EFAC`) with a faint warm-gold bloom (`rgba(214,178,120,.10)`)
  behind the hero. It is brand *atmosphere*, never a risk signal — never confuse the
  aurora green with the low-risk score green. Animated via `transform` only and disabled
  under `prefers-reduced-motion`.

### Secondary
- **Signal Blue** (`#2563EB`): the "live / community signal" provenance tier — data we
  observed but didn't directly confirm. Used on badges and the `pv-signal` chip only.
- **AI Violet** (`#6D28D9` on tint `#EDE9FE`, border `#DDD0F7`): marks machine-generated
  reasoning — the `ai` badge, citation pills, and call-ahead provenance (`#7C3AED`).
  Violet always means "an LLM produced or fetched this," never decoration.

### Tertiary — The Risk Palette
A closed set of three hues, each in four roles (ink / ring / tint-bg / border). This
palette is **reserved for grading allergen risk** and nothing else.
- **Low / Safe** — ink `#15803D`, ring `#22C55E`, tint `#F0FDF4`, border `#BBF7D0`.
- **Medium / Caution** — ink `#92400E`, ring `#FBBF24`, tint `#FFFBEB`, border `#FDE68A`.
- **High / Danger** — ink `#991B1B`, ring `#F87171`, tint `#FEF2F2`, border `#FECACA`.

The split between *ink* (readable on paper) and *ring* (vivid) is deliberate: vivid
hues carry the at-a-glance signal, darker inks carry the text so contrast never fails.

**Menu-chip tints** (drawer dish chips) extend this set: a *nut* chip on `#FEE2E2`
with `#991B1B` ink; an *other-allergen* chip on `#FEF9C3` / `#FEF08A` border / `#854D0E`
ink; a *diet* chip on the green-tint family. They label dish allergens, nothing else.

### Neutral & Ink
- **Page** (`#F7F3EA`): the warm bone field behind everything. **Paper2** (`#FAF7F0`):
  alternate tonal ground for tinted sections. **Surface** (`#FDFBF6`): cards, the search
  card, drawer, top bar.
- **Ink** (`#211B16`): display/headlines (~13:1 on paper). **Ink Muted** (`#4A4039`):
  body copy (~9:1). **Ink Faint** (`#5E544B`): captions, labels, placeholders, eyebrows
  (~6.6:1 — still AA-pass; there is no lighter text token).
- **Border** (`#E6DECF`) / **Border Strong** (`#CFC4B2`): hairlines, dividers, and the
  hover/active border step.
- **Star** (`#FBBF24`): rating stars only. **Scrim** (`rgba(20,14,8,0.34)`): the dim
  behind the open detail drawer.

### Named Rules
**The Meaning-Only Color Rule.** Saturated color is never decorative. Green = brand /
safe / confirmed. Risk hues = allergen risk grade. Blue = observed signal. Violet =
machine reasoning. If a color isn't carrying one of those meanings, it's wrong.

**The Two-Tone Risk Rule.** Risk always uses the vivid hue for the glance signal *and*
the dark ink for any text. Never set risk text in the vivid ring color on a tint.

## 3. Typography

**Display Font:** Playfair Display (Georgia, serif fallback)
**UI / Body Font:** Hanken Grotesk (system-ui / -apple-system fallback)
**Figure Font:** Spline Sans Mono (ui-monospace fallback)

**Character:** A high-contrast serif/sans pairing on a true contrast axis. Playfair
Display is a refined transitional serif — used at its **regular (400) weight, with
italics for emphasis** ("the *guesswork*.") — giving headlines editorial elegance and a
warm, human voice. Hanken Grotesk carries UI and body: clean, friendly, and highly
legible at small sizes. Spline Sans Mono handles figures (scores, prices, counts,
stats) where tabular alignment matters. The warmth lives in the serif and the green,
not in decoration.

### Hierarchy
- **Display** (Playfair 400, `clamp(40px, 6vw, 72px)`, line-height ~1.02): the hero `h1`
  only, with an italic green emphasis clause. One per page.
- **Headline** (Playfair 400, `clamp(28px, 4vw, 42px)`, line-height 1.08): marketing /
  landing section titles.
- **Title** (Hanken 700, 18–20px): results bar, drawer restaurant name, section headings.
- **Body** (Hanken 400, 15px, line-height 1.6): default UI and prose. Cap measure at
  65–75ch.
- **Label** (Hanken 700, 11px, tracking 0.14em, UPPERCASE): section eyebrows, form-group
  labels, menu category headers. Always faint ink (`#5E544B`), never a body weight.
- **Figure** (Spline Sans Mono 500–600, `font-variant-numeric: tabular-nums`): score
  numbers, prices, the hero stat figures, percentages.

### Named Rules
**The One Serif Rule.** Playfair Display is for display and section titles only — it
never drops into body, labels, buttons, or chips (Hanken's job). It is used at 400 (+
italic); **don't reach for heavy Playfair weights** — they aren't loaded and break the
editorial-light feel.

**The Figures-in-Mono Rule.** Numbers that line up (scores, prices, stats, percentages)
use Spline Sans Mono with tabular figures, on one line — never let a figure wrap.

## 4. Elevation

Soft, warm, and ambient, driven by state. Surfaces rest nearly flat (`e1`) and gain
depth as a *response* — hover lifts a card, focus rings a field, and overlays (drawer,
toast) sit clearly above the page. Shadows are warm-tinted (`rgba(40,28,16,…)`) and
diffuse, never hard or dark; depth signals interactivity and layering, not
hierarchy-at-rest.

### Shadow Vocabulary
- **e1** (`0 1px 3px rgba(40,28,16,.06), 0 1px 2px rgba(40,28,16,.04)`): resting cards, tiles.
- **e2** (`0 6px 22px -6px rgba(40,28,16,.10) …`): mild lift.
- **e3** (`0 16px 44px -10px rgba(40,28,16,.16) …`): card/feature hover, toast.
- **e4** (`0 30px 80px -16px rgba(20,40,28,.24) …`): the hero search card — the focal object.
- **ed** (`-16px 0 50px -10px rgba(40,28,16,.20)`): the right-edge detail drawer.

The CTA carries its own colored green glow that deepens on hover — the one place shadow
takes the brand hue.

### Named Rules
**The Lift-On-Intent Rule.** Cards and features are flat-ish at rest (`e1`) and rise to
`e3` with a -2px translate on hover. Resting elevation stays calm; motion earns the lift.

## 5. Components

Refined and reassuring: rounded, softly shadowed, calm. Radius scale: `4–8px` (xxs…xs)
for tight elements (citation/menu chips, badges, controls), `12–14px` (sm/md) for
buttons/inputs, `18–24px` (lg/xl) for cards and the search card, `999px` for pills.

### Buttons
- **Primary ("Find safe places"):** deep-green gradient (`#15803D → #0F4A2E`), white
  text, 700, ~50px tall, with a green glow. Hover lifts -1px and intensifies the glow.
- **Ghost / icon (geo, close):** surface fill, hairline border, muted ink; hover adds
  the stronger border and darker ink, -1px lift. Disabled drops to ~42% opacity.

### Chips
All pill-shaped, all carrying meaning:
- **Allergen filter:** hairline border, surface fill, muted ink. Selected (`.on`) flips
  to green-tint fill, deep-green text, mint border. Locked dims with a dashed border + a
  "SOON" tag. On mobile, chips grow to a 44px min target.
- **Provenance (`pv-*`):** the trust vocabulary — Confirmed (green), Restaurant-info,
  Inferred (amber), Menu-reviewed, Estimate (neutral), Call-ahead (violet). Tiny, bordered.
- **Menu chips (`mchip`):** Nut (red), Other-allergen (amber), Diet (green).
- **Awareness (`sig-chip`):** surface pill with a pip; the affirmative state goes green.

### Cards / Containers
- **Restaurant card:** surface, `18px` (lg) radius, resting `e1`, with a risk stripe +
  score ring + risk word (never color alone). Hover lifts -2px to `e3`.
- **Search card:** the hero focal object — surface, `24px` (xl) radius, heaviest shadow (`e4`).
- **Region banner:** an amber (`tint #FFFBEB` / border `#FDE68A`) note under the verdict
  when the shown allergen data is from another region — icon + text, never color alone.

### Inputs / Fields
- **Search field:** surface fill, hairline border, `14px` (md) radius, leading icon in
  faint ink; placeholder uses `#5E544B` (AA-pass — not a light gray).
- **Focus:** `:focus-visible` is a 2.5px mid-green outline with 3px offset, everywhere.

### Navigation
- **Top bar:** sticky, translucent surface with a light `backdrop-filter` blur and a
  hairline bottom border. Brand mark is a green rounded square with a check glyph.

### Signature Component — The Score Ring & Verdict
The product's defining element. An SVG ring renders the risk score with a tabular-nums
(Spline Mono) number at center and a small UPPERCASE risk word + confidence beneath. In
the drawer it pairs with the **Verdict** panel (label + rationale), the **"how we know"**
provenance line, and a **"Why this score"** list whose tappable lines link to cited
evidence (violet `cite` pills) — "evidence on the desk."

## 6. Do's and Don'ts

### Do:
- **Do** keep saturated color meaningful: green = brand / safe / confirmed; the three
  risk hues = allergen risk grade; blue = observed signal; violet = machine reasoning.
- **Do** pair every risk signal with a second channel — the score ring, the risk word,
  the stripe, or text. **Never communicate risk by color alone** (WCAG 2.2 AA).
- **Do** keep risk *text* in the dark ink tones (`#15803D` / `#92400E` / `#991B1B`).
- **Do** rest surfaces near-flat (`e1`) and let shadows deepen on hover/focus and overlays.
- **Do** use Playfair Display for display and section titles only (400 + italic);
  everything else is Hanken Grotesk; figures are Spline Sans Mono.
- **Do** keep body copy at `#4A4039` or darker on the bone page; `#5E544B` is the faint
  floor for labels/captions/placeholders — there is no lighter text.
- **Do** surface provenance ("how we know") wherever a safety claim appears.

### Don't:
- **Don't** drift toward the **hype-y AI SaaS** look — no buzzword heroes, no manufactured
  urgency, **no decorative vanity metrics** (hero stats must be true and checkable, not
  fabricated telemetry). The one sanctioned gradient is the CTA / aurora; **no gradient text.**
- **Don't** go **childish / cutesy** — no pastels-as-personality, no mascots, no gamified cheer.
- **Don't** become a **generic crowd-review app** — no star-rating soup, no opinion-over-evidence ranking.
- **Don't** use the risk palette decoratively. Amber, red, and green-safe mean exactly one thing each.
- **Don't** use `border-left`/`border-right` > 1px as a colored accent stripe (the
  restaurant card's documented risk stripe is the one sanctioned exception).
- **Don't** set body text in a light gray on the bone page; the faint floor is `#5E544B`.
- **Don't** animate layout properties (width/height/margin) — use `transform`/`opacity`.
- **Don't** ship motion without a `prefers-reduced-motion: reduce` fallback.
