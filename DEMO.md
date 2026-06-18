# SafePlate Demo

SafePlate has a deterministic demo mode for local walkthroughs. It uses tracked
fixtures under `fixtures/demo/` and does not call live place providers, Brave, or
Gemini from app requests.

## Launch

```powershell
python scripts/start_safeplate_app.py --demo
```

Then open:

```text
http://127.0.0.1:8765
```

The search box is prefilled with `SafePlate Demo`. Click **Find safe places** to
load the fixture restaurants.

## Walkthrough

1. **Demo Thai Kitchen** shows the menu-backed risk path. Open the card and the
   drawer updates from cuisine estimate to menu-backed risk because items such as
   Pad Thai and Massaman Curry mention peanuts/tree nuts.
2. **Demo Garden Bistro** shows the honest no-menu path. The app keeps the
   cuisine/location estimate and labels the coverage as no online menu.
3. **Demo Allergen Grill** shows policy/evidence signals. The drawer surfaces an
   allergy disclaimer, cross-contact language, staff-notification language, and a
   nut-free claim from the fixture text.

## Live Mode Notes

Run without `--demo` for live providers:

```powershell
python scripts/start_safeplate_app.py
```

Live mode uses Google Places when `GOOGLE_PLACES_API_KEY` is set, otherwise it
falls back to OpenStreetMap. Brave Search and Gemini remain optional fallbacks
when their keys are configured. Live mode writes generated outputs to `data/`,
which is ignored by git.

## Known Limits

- Nuts are the only fully supported allergen for this milestone.
- Full Gemini extraction and Gemini `url_context` page reading are intentionally
  not in the default app hot path.
- Demo fixtures are curated for a polished walkthrough; they are not a benchmark.
