# Product

## Register

product

## Users

People living with food allergies and dietary restrictions — nut allergies first,
with the architecture aimed at multi-allergen support over time. This includes the
allergic diner themselves and the people who order on their behalf (parents,
partners, friends), often while traveling or eating somewhere unfamiliar.

Their context is high-stakes and time-pressured: they're deciding *where it's safe
to eat right now*, frequently on a phone, sometimes hungry and standing outside a
restaurant. The job to be done is not "browse restaurants" — it's "tell me which
of these nearby places I can trust with my allergy, and show me why." A wrong call
isn't a bad meal; it can be a medical emergency. They need a decision they can act
on and the evidence to back it.

## Product Purpose

SafePlate turns messy, scattered restaurant and menu evidence into a trustworthy,
ranked dining decision for allergic diners. It discovers nearby restaurants,
crawls and extracts menu evidence, scores nut-allergy risk against the user's
profile, and presents the result as color-coded, evidence-backed rankings — each
with a rationale and visible provenance ("how we know").

It exists because the alternatives fail allergic diners: crowd-review apps surface
opinion over evidence, and restaurant data is inconsistent, incomplete, and not
organized around safety. Success is when a user trusts SafePlate enough to make a
real eating decision from it — and that trust is earned through transparency, not
asserted. The product's north star is "extraction → trustworthy decisions," with
safety asymmetry (a missed risk is far worse than a false alarm) as a hard
constraint, not a tuning knob.

The app is the primary surface. A marketing and legal layer (home/landing,
How it works, About, FAQ, Contact, Privacy, Terms) shares the app's design tokens
so the whole thing reads as one product; those pages serve the app, not the other
way around.

## Brand Personality

Trustworthy, precise, and calm — with genuine warmth. SafePlate should feel like a
careful expert who is also on your side: rigorous about evidence, honest about
uncertainty, and reassuring rather than alarming. It states what it knows, shows
where it came from, and is straight about what it doesn't know. The voice is
quiet confidence, never hype and never cute. Words to design by: *trustworthy,
precise, calm, warm, reassuring, honest.*

The emotional goal is **calm, earned trust**: the user should feel they can rely on
the call without anxiety, because the evidence is right there to inspect.

## Anti-references

- **Hype-y AI SaaS.** No gradient-drenched buzzword landing pages, no "powered by
  AI" theatrics, no manufactured urgency. The intelligence shows up as better,
  more transparent decisions — not as marketing.
- **Childish / cutesy wellness.** No pastel mascots, no cutesy tone, no
  gamified-wellness vibe. This is a safety-critical tool; the tone stays grown-up.
- **Generic crowd-review app.** Not Yelp/AllergyEats — avoid star-rating soup,
  dense undifferentiated listings, and opinion-over-evidence ranking. SafePlate
  leads with evidence and provenance, not the wisdom of the crowd.

## Design Principles

1. **Evidence over opinion.** Every safety claim is backed by visible, inspectable
   provenance. The user can always answer "how does it know that?" Trust is shown,
   never just asserted.
2. **Earn calm.** Reduce anxiety by being transparent and legible, not by hiding
   complexity. A confident answer plus its reasoning beats a louder warning.
3. **Safety is asymmetric.** A missed risk (false negative) is far more costly than
   a false alarm. When the design must choose, it errs toward surfacing risk and
   admitting uncertainty — never toward a reassuring-but-unsupported "safe."
4. **Honest about uncertainty.** Incomplete coverage, weak sources, and low
   confidence are first-class states, communicated plainly — not papered over with
   a clean-looking but hollow result.
5. **One coherent product.** App and marketing/legal pages share one design system
   so the experience feels unified; the marketing layer always serves the app.

## Accessibility & Inclusion

Target **WCAG 2.2 AA** across the product, with safety-critical accommodations
that go beyond baseline:

- **Never rely on color alone** to convey risk. Risk level is always carried by an
  additional channel (label, icon, text rationale) so it survives color blindness
  and low-vision contexts — non-negotiable for a safety tool.
- **Contrast:** body text ≥ 4.5:1, large/bold text ≥ 3:1, including risk
  indicators, badges, and placeholder text. No light-gray-on-cream body copy.
- **Reduced motion:** every animation has a `prefers-reduced-motion: reduce`
  alternative (crossfade or instant).
- **Keyboard + screen reader:** the full decision flow (search → cards → evidence
  drawer) is keyboard-navigable with sensible focus order and labeled controls.
