from __future__ import annotations

import unittest
from unittest.mock import patch

import safeplate.allergen_score_llm as sll
from safeplate.allergen_score import UserProfile, Severity, score_restaurant_for_user
from safeplate.local_app import _build_search_cards
from safeplate.schemas import RestaurantRecord


def _row(name, cuisine, dist):
    return RestaurantRecord(
        name=name, address="1 Main St, San Jose, CA", latitude=37.3, longitude=-121.9,
        distance_meters=dist, rating=None, review_count=None, price_level=None,
        categories=[f"primary_type:{cuisine}_restaurant"], website_url=f"https://{name}.example",
        phone_number=None, opening_hours=None, business_status=None, is_open_now=None,
        service_options={}, source_last_updated=None, data_quality_score=1.0,
        source_name="test", source_id=name, fetched_at="2026-06-18", raw_payload={},
    )


NUT = UserProfile.for_nuts(Severity.ALLERGY)


def _fake_extract(*, name, website_url, address, categories, latitude, longitude,
                  profile, user_agent, api_key, cuisines=None, region=None,
                  scoring_engine="rules"):
    # Stand in for the network extraction: deterministic cuisine-prior score, no menu.
    det = score_restaurant_for_user(profile, cuisines=cuisines, region=region or "US")
    return det, [], [], [], []


class BatchScoringWiringTests(unittest.TestCase):
    """The ai_assisted list must score the whole page in ONE batched LLM call, not N."""

    def test_ai_list_uses_one_batched_call(self):
        rows = [_row("alpha", "thai", 100), _row("beta", "japanese", 200)]
        payload = {"scoringEngine": "ai_assisted"}
        calls = {"n": 0, "sizes": []}

        def fake_batch(bundles, **k):
            calls["n"] += 1
            calls["sizes"].append(len(bundles))
            # Push every restaurant to a distinctive caution score so we can see the
            # batch result actually land in the cards.
            return {rid: {"id": rid, "risk": 0.33, "tier": "caution", "confidence": 0.7,
                          "rationale": [{"claim": "batched", "evidence_ids": ["E1"]}]}
                    for rid in bundles}

        with patch("safeplate.local_app._extract_and_assess_structured", _fake_extract), \
             patch("safeplate.local_app.get_gemini_api_key", return_value="k"), \
             patch("safeplate.local_app.get_gemini_model", return_value="m"), \
             patch.object(sll, "_call_llm_scorer_batch", fake_batch):
            cards = _build_search_cards(rows, payload, engine="structured", severity="allergy")

        self.assertEqual(calls["n"], 1)            # ONE call for the whole list
        self.assertEqual(calls["sizes"], [2])      # both restaurants in that one call
        risks = sorted(c["allergenPrior"]["risk"] for c in cards)
        self.assertEqual(risks, [0.33, 0.33])      # batched scores landed in the cards
        for c in cards:
            self.assertEqual(c["allergenPrior"]["tier"], "caution")

    def test_rules_list_makes_no_llm_call(self):
        rows = [_row("alpha", "thai", 100)]
        payload = {"scoringEngine": "rules"}

        def boom(*a, **k):
            raise AssertionError("rules must not call the batch scorer")

        with patch("safeplate.local_app._extract_and_assess_structured", _fake_extract), \
             patch("safeplate.local_app.get_gemini_api_key", return_value="k"), \
             patch.object(sll, "_call_llm_scorer_batch", boom):
            cards = _build_search_cards(rows, payload, engine="structured", severity="allergy")

        self.assertEqual(len(cards), 1)            # deterministic, no LLM


if __name__ == "__main__":
    unittest.main()
