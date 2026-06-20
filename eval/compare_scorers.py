"""Head-to-head: deterministic scorer vs the hybrid LLM scorer.

Runs both on a set of scenarios and prints where they agree / differ. The hybrid's
LLM call needs Gemini; when quota is out it falls back to the deterministic result,
so this script first probes LLM availability and says so. Re-run when quota resets
for the real comparison.

    python eval/compare_scorers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safeplate.allergen_score import (  # noqa: E402
    CommunitySignal, RestaurantSignals, Severity, UserProfile, score_restaurant_for_user,
)
from safeplate.allergen_score_llm import score_restaurant_with_llm  # noqa: E402
from safeplate.config import get_gemini_api_key, get_gemini_model  # noqa: E402


def _item(name, *, allergen_terms=None, method="gemini_text"):
    return {"item_name": name, "description": "", "allergen_terms": allergen_terms or [],
            "extraction_method": method}


NUT_ALLERGY = UserProfile.for_nuts(Severity.ALLERGY)
NUT_PREF = UserProfile.for_nuts(Severity.AVOID_PREFERENCE)

# (label, profile, kwargs for the scorers)
SCENARIOS = [
    ("Grounded matrix: peanut in a dish (american)", NUT_ALLERGY,
     dict(cuisines=["american"], region="US",
          menu_items=[_item("Satay Skewers", allergen_terms=["peanut"], method="allergen_matrix")])),
    ("Navigable matrix: 1 nut dish among many safe (thai)", NUT_ALLERGY,
     dict(cuisines=["thai"], region="US", menu_items=(
         [_item("Satay", allergen_terms=["peanut"], method="allergen_matrix")]
         + [_item(n, allergen_terms=["soy"], method="allergen_matrix") for n in
            ("Rice", "Salad", "Soup", "Curry", "Bowl")]))),
    ("Clean chart: matrix lists no nuts (american)", NUT_ALLERGY,
     dict(cuisines=["american"], region="US",
          menu_items=[_item("Burger", allergen_terms=["milk", "egg"], method="allergen_matrix")])),
    ("Prior-only: Thai, no menu (US)", NUT_ALLERGY, dict(cuisines=["thai"], region="US")),
    ("Prior-only: British pub, no menu (the '0.3' case)", NUT_ALLERGY,
     dict(cuisines=["british"], region="UK")),
    ("Dish-name prior: 'Pad Thai' (no matrix)", NUT_ALLERGY,
     dict(cuisines=["thai"], region="US", menu_items=[_item("Pad Thai")])),
    ("Low-nut cuisine, preference user (japanese)", NUT_PREF,
     dict(cuisines=["japanese"], region="US")),
    ("Community adverse report (japanese)", NUT_ALLERGY,
     dict(cuisines=["japanese"], region="US",
          community=[CommunitySignal(type="adverse_event", allergen="nuts",
                                     quote="nut reaction here", age_days=30)])),
    ("Nut-free claim (american)", NUT_ALLERGY,
     dict(cuisines=["american"], region="US", signals=RestaurantSignals(nut_free_claim=True))),
]


def _llm_available(api_key, model) -> bool:
    if not api_key:
        return False
    from safeplate.gemini_menu import GeminiMenuError, _post_gemini_generate_content
    try:
        _post_gemini_generate_content(
            payload={"contents": [{"parts": [{"text": "ping"}]}],
                     "generationConfig": {"maxOutputTokens": 1}},
            api_key=api_key, model=model)
        return True
    except GeminiMenuError:
        return False


def main() -> None:
    api_key, model = get_gemini_api_key(), get_gemini_model()
    live = _llm_available(api_key, model)
    print(f"LLM available: {live}" + ("" if live else "  -> hybrid falls back to deterministic (quota/no key)"))
    print(f"{'scenario':52s} {'deterministic':18s} {'hybrid-llm':18s}  agree")
    print("-" * 96)
    agree = diff = 0
    for label, profile, kw in SCENARIOS:
        det = score_restaurant_for_user(profile, **kw)
        hyb = score_restaurant_with_llm(profile, api_key=api_key, model=model, **kw)
        same = (det.tier == hyb.tier)
        agree += same
        diff += (not same)
        d = f"{det.tier:9s} {det.overall_risk:.2f}"
        h = f"{hyb.tier:9s} {hyb.overall_risk:.2f}"
        print(f"{label[:52]:52s} {d:18s} {h:18s}  {'=' if same else 'DIFF'}")
    print("-" * 96)
    print(f"tier agreement: {agree}/{len(SCENARIOS)}   differ: {diff}")
    if not live:
        print("NOTE: LLM was unavailable, so hybrid == deterministic. Re-run when quota resets.")


if __name__ == "__main__":
    main()
