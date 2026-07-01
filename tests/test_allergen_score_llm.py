from __future__ import annotations

import unittest

import safeplate.allergen_score_llm as sll
from safeplate.allergen_score import (
    AllergenPref, Severity, UserProfile, Tier, _SEVERITY_TUNING, score_restaurant_for_user,
)


def _item(name, *, allergen_terms=None, method="gemini_text"):
    return {"item_name": name, "description": "",
            "allergen_terms": allergen_terms or [], "extraction_method": method}


def _raise(*a, **k):
    raise AssertionError("_call_llm_scorer should not have been called")


NUT_ALLERGY = UserProfile.for_nuts(Severity.ALLERGY)
ALLERGY_FLOOR = _SEVERITY_TUNING[Severity.ALLERGY][1]


class HybridGuardrailTests(unittest.TestCase):
    def setUp(self):
        self._orig = sll._call_llm_scorer

    def tearDown(self):
        sll._call_llm_scorer = self._orig

    def test_llm_cannot_undercut_grounded_presence(self):
        # Allergen chart confirms peanut (deterministic AVOID). LLM tries 'likely_ok'.
        sll._call_llm_scorer = lambda b, **k: {
            "risk": 0.1, "tier": "likely_ok", "confidence": 0.9,
            "rationale": [{"claim": "looks fine to me", "evidence_ids": []}]}
        out = sll.score_restaurant_with_llm(
            NUT_ALLERGY, cuisines=["american"], region="US",
            menu_items=[_item("Satay", allergen_terms=["peanut"], method="allergen_matrix")],
            api_key="k")
        self.assertEqual(out.tier, Tier.AVOID.value)        # guardrail forced it back
        self.assertGreaterEqual(out.overall_risk, 0.9)

    def test_llm_can_refine_prior_downward(self):
        # Prior-only Thai (no grounded presence): LLM may lower the crude prior...
        det = score_restaurant_for_user(NUT_ALLERGY, cuisines=["thai"], region="US")
        sll._call_llm_scorer = lambda b, **k: {
            "risk": 0.15, "tier": "likely_ok", "confidence": 0.6,
            "rationale": [{"claim": "menu is mostly non-nut", "evidence_ids": ["E1"]}]}
        out = sll.score_restaurant_with_llm(NUT_ALLERGY, cuisines=["thai"], region="US", api_key="k")
        self.assertLess(out.overall_risk, det.overall_risk)        # refined down
        self.assertGreaterEqual(out.overall_risk, ALLERGY_FLOOR)   # but not below the floor

    def test_prior_only_cannot_become_grounded_avoid(self):
        # LLM tries 'avoid' on a pure cuisine guess -> capped at caution.
        sll._call_llm_scorer = lambda b, **k: {
            "risk": 0.95, "tier": "avoid", "confidence": 0.9,
            "rationale": [{"claim": "scary cuisine", "evidence_ids": ["E1"]}]}
        out = sll.score_restaurant_with_llm(NUT_ALLERGY, cuisines=["thai"], region="US", api_key="k")
        self.assertEqual(out.tier, Tier.CAUTION.value)

    def test_invalid_citations_dropped(self):
        sll._call_llm_scorer = lambda b, **k: {
            "risk": 0.3, "tier": "caution", "confidence": 0.5,
            "rationale": [{"claim": "uses the real one", "evidence_ids": ["E1", "E999"]}]}
        out = sll.score_restaurant_with_llm(NUT_ALLERGY, cuisines=["thai"], region="US", api_key="k")
        joined = " ".join(out.rationale)
        self.assertIn("E1", joined)
        self.assertNotIn("E999", joined)   # hallucinated citation dropped

    def test_never_below_severity_floor(self):
        sll._call_llm_scorer = lambda b, **k: {
            "risk": 0.0, "tier": "likely_ok", "confidence": 0.9, "rationale": []}
        out = sll.score_restaurant_with_llm(NUT_ALLERGY, cuisines=["japanese"], region="US", api_key="k")
        self.assertGreaterEqual(out.overall_risk, ALLERGY_FLOOR)

    def test_no_api_key_returns_deterministic(self):
        sll._call_llm_scorer = _raise
        det = score_restaurant_for_user(NUT_ALLERGY, cuisines=["thai"], region="US")
        out = sll.score_restaurant_with_llm(NUT_ALLERGY, cuisines=["thai"], region="US", api_key=None)
        self.assertEqual(out.tier, det.tier)
        self.assertEqual(out.overall_risk, det.overall_risk)

    def test_llm_failure_falls_back_to_deterministic(self):
        def boom(b, **k):
            raise RuntimeError("HTTP 429 quota")
        sll._call_llm_scorer = boom
        det = score_restaurant_for_user(NUT_ALLERGY, cuisines=["thai"], region="US")
        out = sll.score_restaurant_with_llm(NUT_ALLERGY, cuisines=["thai"], region="US", api_key="k")
        self.assertEqual(out.tier, det.tier)
        self.assertEqual(out.overall_risk, det.overall_risk)


class BatchScorerTests(unittest.TestCase):
    """One LLM call scores every restaurant; guardrails still apply per restaurant."""

    def setUp(self):
        self._orig = sll._call_llm_scorer_batch

    def tearDown(self):
        sll._call_llm_scorer_batch = self._orig

    def _reqs(self):
        return [
            {"id": "0", "profile": NUT_ALLERGY, "cuisines": ["thai"], "region": "US"},
            {"id": "1", "profile": NUT_ALLERGY, "cuisines": ["american"], "region": "US",
             "menu_items": [_item("Satay", allergen_terms=["peanut"], method="allergen_matrix")]},
        ]

    def test_single_call_scores_all_restaurants(self):
        calls = {"n": 0}

        def fake_batch(bundles, **k):
            calls["n"] += 1
            return {rid: {"id": rid, "risk": 0.2, "tier": "likely_ok", "confidence": 0.6,
                          "rationale": [{"claim": "ok", "evidence_ids": ["E1"]}]}
                    for rid in bundles}

        sll._call_llm_scorer_batch = fake_batch
        out = sll.score_restaurants_with_llm_batch(self._reqs(), api_key="k")
        self.assertEqual(calls["n"], 1)          # ONE call for the whole list
        self.assertEqual(set(out), {"0", "1"})

    def test_batch_guardrails_protect_grounded_restaurant(self):
        # The LLM lowballs BOTH, but #1 has a confirmed peanut chart -> stays AVOID.
        sll._call_llm_scorer_batch = lambda bundles, **k: {
            rid: {"id": rid, "risk": 0.05, "tier": "likely_ok", "confidence": 0.9,
                  "rationale": []}
            for rid in bundles}
        out = sll.score_restaurants_with_llm_batch(self._reqs(), api_key="k")
        self.assertEqual(out["1"].tier, Tier.AVOID.value)       # grounded floor held
        self.assertGreaterEqual(out["1"].overall_risk, 0.9)
        self.assertGreaterEqual(out["0"].overall_risk, ALLERGY_FLOOR)

    def test_missing_id_falls_back_to_deterministic(self):
        # The LLM only returns one of the two -> the other keeps its deterministic score.
        sll._call_llm_scorer_batch = lambda bundles, **k: {
            "1": {"id": "1", "risk": 0.95, "tier": "avoid", "rationale": []}}
        out = sll.score_restaurants_with_llm_batch(self._reqs(), api_key="k")
        det0 = score_restaurant_for_user(NUT_ALLERGY, cuisines=["thai"], region="US")
        self.assertEqual(out["0"].tier, det0.tier)
        self.assertEqual(out["0"].overall_risk, det0.overall_risk)

    def test_batch_failure_falls_back_to_deterministic(self):
        def boom(bundles, **k):
            raise RuntimeError("HTTP 429 quota")
        sll._call_llm_scorer_batch = boom
        out = sll.score_restaurants_with_llm_batch(self._reqs(), api_key="k")
        det0 = score_restaurant_for_user(NUT_ALLERGY, cuisines=["thai"], region="US")
        self.assertEqual(out["0"].overall_risk, det0.overall_risk)

    def test_no_key_returns_deterministic_without_calling(self):
        sll._call_llm_scorer_batch = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("should not call"))
        out = sll.score_restaurants_with_llm_batch(self._reqs(), api_key=None)
        self.assertEqual(set(out), {"0", "1"})


class ScenarioRoutingTests(unittest.TestCase):
    """The AI engine routes each restaurant: labeled (chart) / raw_menu / no_menu."""

    def setUp(self):
        self._orig = sll._call_llm_scorer

    def tearDown(self):
        sll._call_llm_scorer = self._orig

    def test_scenario_router(self):
        # comprehensive chart -> labeled
        charted = [_item(f"D{i}", method="allergen_matrix") for i in range(10)]
        self.assertEqual(sll._scenario(charted), "labeled")
        # plain menu, no chart -> raw_menu
        self.assertEqual(sll._scenario([_item(f"D{i}") for i in range(10)]), "raw_menu")
        # a couple stray tags don't make it "labeled" (rest would be assumed safe)
        mostly_plain = [_item("Satay", method="allergen_matrix")] + [_item(f"D{i}") for i in range(10)]
        self.assertEqual(sll._scenario(mostly_plain), "raw_menu")
        # nothing parsed -> no_menu
        self.assertEqual(sll._scenario([]), "no_menu")

    def _det(self, menu):
        from safeplate.allergen_score import score_restaurant_for_user
        return score_restaurant_for_user(NUT_ALLERGY, cuisines=["american"], region="US",
                                         menu_items=menu)

    def test_labeled_bundle_has_chart_summary_not_raw_menu(self):
        menu = [_item("Satay", allergen_terms=["peanut"], method="allergen_matrix")] + [
            _item(f"D{i}", method="allergen_matrix") for i in range(10)]
        b = sll._build_bundle(profile=NUT_ALLERGY, cuisines=["american"], region="US",
                              det=self._det(menu), signals=None, community=None, menu_items=menu)
        self.assertEqual(b["scenario"], "labeled")
        self.assertIn("chart_summary", b)
        self.assertNotIn("menu", b)
        self.assertEqual(b["chart_summary"]["dishes_with_allergen"], 1)

    def test_raw_menu_bundle_has_menu_not_chart(self):
        menu = [_item(f"Dish {i}") for i in range(20)] + [_item("Peanut Butter Pie")]
        b = sll._build_bundle(profile=NUT_ALLERGY, cuisines=["american"], region="US",
                              det=self._det(menu), signals=None, community=None, menu_items=menu)
        self.assertEqual(b["scenario"], "raw_menu")
        self.assertIn("menu", b)
        self.assertNotIn("chart_summary", b)
        self.assertIn("Peanut Butter Pie", b["menu"])

    def test_no_menu_bundle_has_neither(self):
        b = sll._build_bundle(profile=NUT_ALLERGY, cuisines=["thai"], region="US",
                              det=self._det([]), signals=None, community=None, menu_items=[])
        self.assertEqual(b["scenario"], "no_menu")
        self.assertNotIn("menu", b)
        self.assertNotIn("chart_summary", b)

    def test_compact_menu_inlines_terms_dedupes_and_caps(self):
        items = [_item("Satay", allergen_terms=["peanut"]), _item("Satay"),  # dup
                 _item("Rice")] + [_item(f"X{i}") for i in range(200)]
        menu = sll._compact_menu(items)
        self.assertIn("Satay [peanut]", menu)
        self.assertEqual(sum(1 for m in menu if m.startswith("Satay")), 1)   # deduped
        self.assertLessEqual(len(menu), sll._FULLMENU_MAX)                    # capped

    def test_routes_and_guardrails_hold(self):
        captured = {}
        def fake(bundle, **k):
            captured["scenario"] = bundle.get("scenario")
            captured["has_menu"] = "menu" in bundle
            return {"risk": 0.05, "tier": "likely_ok", "confidence": 0.6, "rationale": []}
        sll._call_llm_scorer = fake
        out = sll.score_restaurant_with_llm(
            NUT_ALLERGY, cuisines=["american"], region="US",
            menu_items=[_item("Pad Thai")] + [_item(f"D{i}") for i in range(10)], api_key="k")
        self.assertEqual(captured["scenario"], "raw_menu")
        self.assertTrue(captured["has_menu"])
        self.assertGreaterEqual(out.overall_risk, ALLERGY_FLOOR)   # guardrails still applied


class GeneralizedPromptTests(unittest.TestCase):
    """Task 7: the prompt + bundle must name the user's ACTUAL allergen, not nuts."""

    def setUp(self):
        self._orig = sll._call_llm_scorer

    def tearDown(self):
        sll._call_llm_scorer = self._orig

    def test_llm_prompt_names_actual_allergen(self):
        captured = {}

        def fake_call(bundle, *, api_key, model, system=None, **_kw):
            captured["bundle"] = bundle
            captured["system"] = system
            return {"risk": 0.5, "tier": "caution", "confidence": 0.6, "rationale": []}

        sll._call_llm_scorer = fake_call
        profile = UserProfile(allergens=(AllergenPref(allergen="milk", severity=Severity.ALLERGY),))
        sll.score_restaurant_with_llm(
            profile, cuisines=["italian"], region="US",
            menu_items=[], api_key="x", model="y",
        )
        self.assertIn("milk", str(captured["bundle"]).lower())
        self.assertNotIn("nuts", str(captured["bundle"]["user"]).lower())
        # The system prompt itself must name milk, not talk about nuts.
        self.assertIn("milk", captured["system"].lower())
        self.assertNotIn("nut", captured["system"].lower())


if __name__ == "__main__":
    unittest.main()
