from __future__ import annotations

import unittest

import safeplate.community_signals as cs
import safeplate.brave_search as brave
import safeplate.extraction2.interpret_llm as illm
from safeplate.allergen_score import Severity, UserProfile, score_restaurant_for_user


def _brave_results(*texts):
    return [
        brave.BraveSearchResult(
            title=t, url=f"https://example.com/{i}", description="",
            extra_snippets=[], raw_payload={},
        )
        for i, t in enumerate(texts)
    ]


class CommunitySignalsTests(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._orig_cache = os.environ.get("SAFEPLATE_CACHE_DIR")
        os.environ["SAFEPLATE_CACHE_DIR"] = self._tmp
        self._orig_brave = brave.brave_web_search
        self._orig_llm = illm._call_with_retry

    def tearDown(self):
        import os
        if self._orig_cache is None:
            os.environ.pop("SAFEPLATE_CACHE_DIR", None)
        else:
            os.environ["SAFEPLATE_CACHE_DIR"] = self._orig_cache
        brave.brave_web_search = self._orig_brave
        illm._call_with_retry = self._orig_llm

    def test_grounded_handling_and_dishes(self):
        # On-topic snippet (names the restaurant) -> classified.
        snippet = "Golden Diner is amazing with my nut allergy and the Cashew Chicken is incredible"
        brave.brave_web_search = lambda **k: _brave_results(snippet)
        illm._call_with_retry = lambda *a, **k: {
            "handling": [{"type": "good_handling", "allergen": "nuts",
                          "quote": "amazing with my nut allergy"}],
            "dishes": ["Cashew Chicken"],
        }
        res = cs.fetch_community_signals(
            restaurant_name="Golden Diner", address="1 Main St, Townsville, CA",
            user_agent="t", brave_api_key="k", gemini_api_key="k",
            gemini_model="m", want_dishes=True,
        )
        self.assertEqual(len(res.signals), 1)
        self.assertEqual(res.signals[0].type, "good_handling")
        self.assertEqual([d.item_name for d in res.dishes], ["Cashew Chicken"])
        self.assertEqual(res.dishes[0].extraction_method, "community_mention")

    def test_offrestaurant_results_are_filtered(self):
        # SAFETY: snippets about a DIFFERENT place / generic guides must be dropped
        # BEFORE the LLM, so we never misattribute another restaurant's allergy info.
        brave.brave_web_search = lambda **k: _brave_results(
            "Golden Corral allergen statement and buffet info",
            "NYC restaurants safe for peanut allergies: a city guide",
        )
        def _boom(*a, **k):
            raise AssertionError("classify must not run on off-restaurant snippets")
        illm._call_with_retry = _boom
        res = cs.fetch_community_signals(
            restaurant_name="Golden Diner", address="123 Madison St, New York, NY",
            user_agent="t", brave_api_key="k", gemini_api_key="k", gemini_model="m",
            want_dishes=True,
        )
        self.assertEqual(res.signals, [])
        self.assertEqual(res.dishes, [])

    def test_ungrounded_quote_is_dropped(self):
        brave.brave_web_search = lambda **k: _brave_results("Bistro2 is a nice place with good coffee")
        illm._call_with_retry = lambda *a, **k: {
            "handling": [{"type": "adverse_event", "allergen": "peanut",
                          "quote": "I had a severe peanut reaction here"}],  # NOT in snippet
            "dishes": [],
        }
        res = cs.fetch_community_signals(
            restaurant_name="Bistro2", address=None, user_agent="t",
            brave_api_key="k", gemini_api_key="k", gemini_model="m",
        )
        self.assertEqual(res.signals, [])  # hallucinated quote rejected

    def test_no_keys_returns_empty(self):
        called = {"n": 0}
        def _boom(**k):
            called["n"] += 1
            raise AssertionError("should not be called")
        brave.brave_web_search = _boom
        res = cs.fetch_community_signals(
            restaurant_name="X", address=None, user_agent="t",
            brave_api_key=None, gemini_api_key=None, gemini_model="m",
        )
        self.assertEqual(res.signals, [])
        self.assertEqual(res.dishes, [])
        self.assertEqual(called["n"], 0)

    def test_dishes_omitted_when_not_requested(self):
        brave.brave_web_search = lambda **k: _brave_results("Bistro3 -- the Pad Thai is great")
        illm._call_with_retry = lambda *a, **k: {"handling": [], "dishes": ["Pad Thai"]}
        res = cs.fetch_community_signals(
            restaurant_name="Bistro3", address=None, user_agent="t",
            brave_api_key="k", gemini_api_key="k", gemini_model="m",
            want_dishes=False,
        )
        self.assertEqual(res.dishes, [])  # dishes only when no menu (want_dishes)

    def test_adverse_signal_raises_score(self):
        # End-to-end: a grounded community adverse report must raise the fused risk.
        snippet = "At Bistro4 my son had an allergic reaction to nuts"
        brave.brave_web_search = lambda **k: _brave_results(snippet)
        illm._call_with_retry = lambda *a, **k: {
            "handling": [{"type": "adverse_event", "allergen": "nuts",
                          "quote": "had an allergic reaction to nuts"}],
            "dishes": [],
        }
        res = cs.fetch_community_signals(
            restaurant_name="Bistro4", address=None, user_agent="t",
            brave_api_key="k", gemini_api_key="k", gemini_model="m",
        )
        profile = UserProfile.for_nuts(Severity.ALLERGY)
        base = score_restaurant_for_user(profile, cuisines=["american"], region="US")
        withc = score_restaurant_for_user(profile, cuisines=["american"], region="US",
                                          community=res.signals)
        self.assertGreater(withc.overall_risk, base.overall_risk)
        self.assertTrue(withc.community_reported)


if __name__ == "__main__":
    unittest.main()
