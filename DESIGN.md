---
name: SafePlate
description: Allergy-aware dining decisions you can trust — evidence on the desk, risk graded in color.
colors:
  page: "#F5F3EF"
  surface: "#FFFFFF"
  border: "#E3DDD5"
  border-strong: "#C9C2B8"
  ink: "#1A1714"
  ink-muted: "#6A5F57"
  ink-faint: "#A09088"
  green-deep: "#166534"
  green-mid: "#16A34A"
  green-vivid: "#22C55E"
  green-tint: "#DCFCE7"
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
  ai-violet: "#6D28D9"
  ai-violet-tint: "#EDE9FE"
  callahead-purple: "#7C3AED"
  star: "#FBBF24"
  mchip-nut-bg: "#FEE2E2"
  mchip-other-bg: "#FEF9C3"
  mchip-other-border: "#FEF08A"
  mchip-other-ink: "#854D0E"
  hero-glow-blue: "rgba(14,165,233,0.08)"
  scrim: "rgba(8,8,8,0.32)"
typography:
  display:
    fontFamily: "Newsreader, Georgia, serif"
    fontSize: "clamp(36px, 5.2vw, 62px)"
    fontWeight: 700
    lineHeight: 1.02
    letterSpacing: "-0.018em"
  headline:
    fontFamily: "Newsreader, Georgia, serif"
    fontSize: "clamp(25px, 3.2vw, 34px)"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  title:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  body:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "-0.011em"
  label:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.13em"
rounded:
  xxs: "4px"
  hair: "5px"
  chip: "6px"
  tag: "7px"
  xs: "8px"
  ctl: "9px"
  ctl2: "10px"
  sm: "12px"
  md: "16px"
  lg: "20px"
  xl: "24px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "22px"
  xxl: "54px"
components:
  button-primary:
    backgroundColor: "{colors.green-mid}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "0 20px"
    height: "50px"
  button-ghost:
    backgroundColor: "{colors.page}"
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
    backgroundColor: "{colors.page}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "14px"
  chip-allergen:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.pill}"
    padding: "5px 12px"
  chip-allergen-on:
    backgroundColor: "{colors.green-tint}"
    textColor: "{colors.green-deep}"
    rounded: "{rounded.pill}"
    padding: "5px 12px"
---

# Design System: SafePlate

## 1. Overview

**Creative North Star: "The Evidence Desk"**

SafePlate is the workspace of a careful analyst who happens to be on your side. Every
screen lays its findings out on a warm, paper-toned surface — restaurants ranked,
risk graded, and the evidence behind each call within reach. The mood is unhurried
and legible: a calm verdict you can act on, plus the reasoning you can inspect if you
want to. This is a safety-critical tool, so the design earns trust by being
transparent, never by being loud.

The system is built from a warm off-white field (`#F5F3EF`) carrying clean white
surfaces, a disciplined green brand ramp, and a small, semantically strict risk
palette (green / amber / red). Type pairs a Newsreader serif for display warmth against
IBM Plex Sans for dense, neutral body and UI text. Color is rationed: green means *us /
safe / confirmed*, and the three risk hues are reserved exclusively for grading
allergen risk. The interface rests near-flat and lifts softly on interaction —
shadows are a response to state, not decoration.

What this system explicitly rejects: the **hype-y AI SaaS** look (gradient-drenched
heroes, buzzword theatrics, manufactured urgency); the **childish / cutesy wellness**
register (pastels, mascots, gamified cheer); and the **generic crowd-review app**
(star-rating soup, dense undifferentiated listings, opinion over evidence). SafePlate
leads with evidence and provenance, not the wisdom of the crowd.

**Key Characteristics:**
- Warm paper field + clean white surfaces; calm, document-like density.
- One brand color (green) used for identity and "safe / confirmed", rationed hard.
- A strict 3-hue risk palette (green/amber/red) that never bleeds into decoration.
- Serif display warmth (Newsreader) over neutral, legible IBM Plex Sans UI text.
- Flat at rest; soft ambient shadows that deepen on hover, focus, and for overlays.
- Provenance is a first-class visual language — every claim shows "how we know".

## 2. Colors

A warm-neutral foundation with a single green brand voice and a tightly-scoped
safety-signal palette; every saturated color carries meaning.

### Primary
- **Brand Green** — three steps for one voice. **Deep Green** (`#166534`) for text on
  tints and the "confirmed / safe" state; **Mid Green** (`#16A34A`) for primary CTAs,
  links, and brand accents; **Vivid Green** (`#22C55E`) for the low-risk score ring and
  live status pips. **Green Tint** (`#DCFCE7`) backs selected chips and positive
  badges.

### Secondary
- **Signal Blue** (`#2563EB`): the "live / community signal" provenance tier — data we
  observed but didn't directly confirm. Used on badges and the `pv-signal` chip only.
- **AI Violet** (`#6D28D9` on tint `#EDE9FE`): marks machine-generated reasoning — the
  `ai_assisted` badge, citation pills, and the call-ahead provenance (`#7C3AED`). Violet
  always means "an LLM produced or fetched this," never decoration.

### Tertiary — The Risk Palette
A closed set of three hues, each in four roles (ink / ring / tint-bg / border). This
palette is **reserved for grading allergen risk** and nothing else.
- **Low / Safe** — ink `#15803D`, ring `#22C55E`, tint `#F0FDF4`, border `#BBF7D0`.
- **Medium / Caution** — ink `#92400E`, ring `#FBBF24`, tint `#FFFBEB`, border `#FDE68A`.
- **High / Danger** — ink `#991B1B`, ring `#F87171`, tint `#FEF2F2`, border `#FECACA`.

The split between *ink* (readable on white) and *ring* (vivid) is deliberate: vivid
hues carry the at-a-glance signal, darker inks carry the text so contrast never fails.

**Menu-chip tints** (drawer dish chips) extend this set: a *nut* chip on `#FEE2E2`
with `#991B1B` ink; an *other-allergen* chip on `#FEF9C3` / `#FEF08A` border / `#854D0E`
ink; a *diet* chip on the green-tint family. They label dish allergens, nothing else.

### Neutral
- **Page** (`#F5F3EF`): the warm paper field behind everything and inside inputs/drawer body.
- **Surface** (`#FFFFFF`): cards, the search card, drawer head, verdict panel, top bar.
- **Ink** (`#1A1714`): primary text; also the "selected" fill on sort toggles and the toast.
- **Ink Muted** (`#6A5F57`): secondary text, meta rows, supporting copy. The body-copy floor.
- **Ink Faint** (`#A09088`): labels, captions, placeholders, section eyebrows. Never body copy.
- **Border** (`#E3DDD5`) / **Border Strong** (`#C9C2B8`): hairlines, dividers, and the
  hover/active border step.
- **Star** (`#FBBF24`): rating stars only.
- **Scrim** (`rgba(8,8,8,0.32)`): the dim behind the open detail drawer.
- **Hero glow** (`rgba(14,165,233,0.08)`): the faint blue radial accent in the hero backdrop (paired with a green glow) — atmosphere only, never content.

### Named Rules
**The Meaning-Only Color Rule.** Saturated color is never decorative. Green = brand /
safe / confirmed. Risk hues = allergen risk grade. Blue = observed signal. Violet =
machine reasoning. If a color isn't carrying one of those meanings, it's wrong.

**The Two-Tone Risk Rule.** Risk always uses the vivid hue for the glance signal *and*
the dark ink for any text. Never set risk text in the vivid ring color on white.

## 3. Typography

**Display Font:** Newsreader (with Georgia, serif fallback)
**Body Font:** IBM Plex Sans (with system-ui / -apple-system fallback)

**Character:** A high-contrast serif/sans pairing on a true contrast axis, chosen to
read as *The Evidence Desk* and to avoid the saturated default faces (no Inter,
Fraunces, Geist, etc.). Newsreader is a warm editorial text serif — it gives
headlines a calm, document/longform authority, the reassuring human voice. IBM Plex
Sans carries UI and evidence text: precise and technical with quiet warmth, and its
excellent tabular figures make the risk scores, prices, and percentages line up
cleanly. The warmth lives in the serif and the paper, not in decoration.

### Hierarchy
- **Display** (Newsreader 700, `clamp(36px, 5.2vw, 62px)`, line-height 1.02, tracking
  -0.018em): the hero `h1` only. One per page. (Tracking is looser than a display serif
  would take — Newsreader is a text face; don't crowd it.)
- **Headline** (Newsreader 700, `clamp(25px, 3.2vw, 34px)`, line-height 1.1, tracking
  -0.02em): marketing/landing section titles.
- **Title** (IBM Plex Sans 700, 18–20px, tracking -0.02em): results bar, drawer
  restaurant name, in-app section headings.
- **Body** (IBM Plex Sans 400, 15px, line-height 1.6, tracking -0.011em): default UI and
  prose. Cap measure at 65–75ch; the disclaimer and ledes already hold ~480–620px.
- **Label** (IBM Plex Sans 700, 11px, tracking 0.13em, UPPERCASE): the drawer `sec-head`,
  menu category headers. Always faint ink, never a body weight.

Numerics that line up (scores, prices, percentages) use `font-variant-numeric:
tabular-nums` — IBM Plex Sans's figures are built for this.

### Named Rules
**The One Serif Rule.** Newsreader is for display and section titles only. It never drops
into body, labels, buttons, or chips — that's IBM Plex Sans's job. Mixing breaks the
contrast. **Max body weight is 700** (IBM Plex Sans ends at Bold); the old 800 weights
were stepped down.

## 4. Elevation

Soft and ambient, driven by state. Surfaces rest nearly flat (`e1`) and gain depth as a
*response* — hover lifts a card, focus rings a field, and overlays (drawer, toast) sit
clearly above the page. Shadows are warm-neutral and diffuse, never hard or dark; depth
signals interactivity and layering, not hierarchy-at-rest.

### Shadow Vocabulary
- **e1** (`0 1px 3px rgba(0,0,0,0.07), 0 1px 2px rgba(0,0,0,0.04)`): resting cards, feature tiles.
- **e2** (`0 4px 16px -2px rgba(0,0,0,0.09), 0 1px 4px rgba(0,0,0,0.05)`): mild lift.
- **e3** (`0 12px 36px -6px rgba(0,0,0,0.14), 0 3px 10px rgba(0,0,0,0.06)`): card/feature hover, toast.
- **e4** (`0 24px 64px -10px rgba(0,0,0,0.18), 0 6px 20px rgba(0,0,0,0.07)`): the hero search card — the page's focal object.
- **ed** (drawer, `-10px 0 44px -4px rgba(0,0,0,0.13)`): the right-edge detail drawer.

The CTA carries its own colored glow (`0 4px 16px -4px rgba(21,128,61,0.52)`) that
deepens on hover — the one place shadow takes the brand hue.

### Named Rules
**The Lift-On-Intent Rule.** Cards and features are flat-ish at rest (`e1`) and rise to
`e3` with a -2px translate on hover. Resting elevation stays calm; motion earns the lift.

## 5. Components

Refined and reassuring: rounded, softly shadowed, calm. Tactile enough to invite the
next action without ever shouting.

### Buttons
- **Shape:** generously rounded (`16px`, the `md` radius); pills (`999px`) for chips and status. Small detail radii (`4–10px`, the `xxs`…`ctl2` steps) are reserved for tight elements only — citation/menu chips, badges, skeletons, and toolbar/control buttons.
- **Primary ("Go"):** mid→deep green gradient (`#16A34A → #15803D`), white text, 700 weight,
  50px tall, with a green glow shadow. Hover lifts -1px and intensifies the glow; active flattens.
- **Ghost / icon (geo, close):** page-toned fill, hairline border, muted ink; hover shifts to
  white surface with the stronger border and darker ink. Disabled drops to ~40% opacity.

### Chips
SafePlate runs several chip families, all pill-shaped, all carrying meaning:
- **Allergen filter:** hairline border, surface fill, muted ink. Selected (`.on`) flips to
  green-tint fill, deep-green text, green border. Locked state dims to ~32%.
- **Provenance (`pv-*`):** the trust vocabulary — Confirmed (green), Signal (blue),
  Inferred (amber/med ink), Estimate (neutral), Call-ahead (purple). Tiny, bordered, tinted.
- **Menu chips (`mchip`):** Nut (red), Other-allergen (amber), Diet (green) — each a
  tint bg + matching ink + light border.
- **Awareness (`sig-chip`):** surface pill with a pip; the affirmative state goes green.

### Cards / Containers
- **Restaurant card:** white surface, `20px` radius, 1.5px border, resting `e1`. A 5px
  left **risk stripe** carries the risk hue (paired with the score ring + word, never color
  alone). Hover lifts -2px to `e3` with a stronger border.
- **Search card:** the hero focal object — white, `24px` radius, the heaviest shadow (`e4`).
- **Feature tiles:** white (or page-toned on tinted sections), `20px` radius, `e1` → `e3` hover.
- **Internal padding:** ~17px on cards, 24px on feature tiles.

### Inputs / Fields
- **Search field:** page-toned fill, 1.5px border, `16px` radius, with a leading icon in faint ink.
- **Focus:** border shifts to mid-green plus a 3px green focus ring
  (`box-shadow: 0 0 0 3px rgba(22,163,74,0.12)`) — soft glow, not a hard outline.

### Navigation
- **Top bar:** sticky, translucent white with `backdrop-filter: blur(20px) saturate(1.8)`
  and a hairline bottom border. Brand mark is a green-gradient rounded square (logo glyph).
- **Nav links:** 13.5px, 600 weight, muted ink; hover darkens ink over a faint dark wash.
- **Status pill:** bordered pill with a dot; the live state turns the dot vivid green with a glow.

### Signature Component — The Score Ring & Verdict
The product's defining element. An SVG ring (rotated -90°) renders the risk score with a
tabular-nums number at center and a small UPPERCASE risk word + confidence beneath. In the
drawer it pairs with the **Verdict** panel (label + plain-language rationale) and a
**"Why this score"** list whose tappable lines link to cited evidence (violet `cite` pills)
— the literal embodiment of "evidence on the desk." The riskiest-items **warning box** uses
the high-risk tint/border with red ink.

## 6. Do's and Don'ts

### Do:
- **Do** keep saturated color meaningful: green = brand / safe / confirmed; the three risk
  hues = allergen risk grade; blue = observed signal; violet = machine reasoning.
- **Do** pair every risk signal with a second channel — the score ring, the risk word, the
  5px stripe, or text. **Never communicate risk by color alone** (WCAG 2.2 AA, safety-critical).
- **Do** keep risk *text* in the dark ink tones (`#15803D` / `#92400E` / `#991B1B`); reserve
  the vivid ring hues for fills, rings, and stripes.
- **Do** rest surfaces near-flat (`e1`) and let shadows deepen on hover/focus and for overlays.
- **Do** use Newsreader for display and section titles only; everything else is IBM Plex Sans.
- **Do** keep body copy at `#6A5F57` or darker on the warm page; reserve `#A09088` for labels,
  captions, and placeholders only — never paragraphs.
- **Do** surface provenance ("how we know") wherever a safety claim appears; honesty about
  uncertainty is a feature, not a blemish.

### Don't:
- **Don't** drift toward the **hype-y AI SaaS** look — no buzzword heroes, no manufactured
  urgency, no "powered by AI" theatrics. The one sanctioned gradient is the hero headline /
  CTA; **don't add gradient text anywhere else.**
- **Don't** go **childish / cutesy** — no pastels-as-personality, no mascots, no gamified
  cheer. This tool is grown-up and serious about safety.
- **Don't** become a **generic crowd-review app** — no star-rating soup, no dense
  undifferentiated listings, no opinion-over-evidence ranking. Lead with evidence.
- **Don't** use the risk palette decoratively. Amber, red, and green-safe mean exactly one
  thing each; a red accent that isn't "high risk" is a bug.
- **Don't** use `border-left`/`border-right` > 1px as a colored accent stripe on cards or
  callouts. The one exception is the restaurant card's risk stripe, which is a documented
  signal — not a decorative side-border.
- **Don't** set body text in light gray on the cream page (`#A09088` and lighter fails for
  paragraphs). If contrast is even close, bump toward ink.
- **Don't** ship motion without a `prefers-reduced-motion: reduce` fallback.
