# CLAUDE.md

## Design Context

SafePlate's design system and product strategy are documented for agents:

- **PRODUCT.md** (root) — register (`product`), users, purpose, brand personality,
  anti-references, design principles, and accessibility bar (WCAG 2.2 AA +
  safety-aware color: never rely on color alone for risk).
- **DESIGN.md** (root) — the visual system (color tokens, typography, components,
  layout). The committed source of truth for tokens is `safeplate/app_template.html`.

Core stance: **evidence over opinion, calm earned trust, safety is asymmetric**
(a missed risk is worse than a false alarm). The app is the primary surface; the
marketing/legal pages (rendered by `safeplate/pages.py`) share its tokens so the
product reads as one. Read PRODUCT.md / DESIGN.md before design work.

The impeccable design skill is configured for this project (live mode targets
`safeplate/app_template.html`).
