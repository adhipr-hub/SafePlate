from __future__ import annotations

from types import SimpleNamespace
import unittest

from safeplate.allergen_prior import NUTS, PEANUTS, TREE_NUTS
from safeplate.allergen_score import (
    CommunitySignal,
    CrossContactSensitivity,
    RestaurantSignals,
    Severity,
    Tier,
    UserProfile,
    AllergenPref,
    assess_restaurant_record,
    rank_restaurants_for_user,
    score_restaurant_for_user,
)


# A realistic allergen matrix has a column per tracked allergen, INCLUDING nuts -- the
# scorer only treats a clean chart as vouching for nut-absence when a nut column exists.
_DEFAULT_MATRIX_COLUMNS = ("peanut", "tree nut", "milk", "egg", "soy", "gluten")


def _item(name, *, allergen_terms=None, method="gemini_text", description="", matrix_columns=None):
    item = {
        "item_name": name,
        "description": description,
        "allergen_terms": allergen_terms or [],
        "extraction_method": method,
    }
    if "matrix" in method:
        item["matrix_allergen_columns"] = tuple(
            matrix_columns if matrix_columns is not None else _DEFAULT_MATRIX_COLUMNS
        )
    return item


NUT_ALLERGY = UserProfile.for_nuts(Severity.ALLERGY)
NUT_ANAPHYLAXIS = UserProfile.for_nuts(Severity.ANAPHYLAXIS)
NUT_PREF = UserProfile.for_nuts(Severity.AVOID_PREFERENCE)


def _matrix_item(name, *, allergen_terms=None, url="", matrix_columns=None):
    return {
        "item_name": name,
        "description": "",
        "allergen_terms": allergen_terms or [],
        "extraction_method": "gemini_allergen_matrix",
        "menu_source_url": url,
        "matrix_allergen_columns": tuple(
            matrix_columns if matrix_columns is not None else _DEFAULT_MATRIX_COLUMNS
        ),
    }


class ProvenanceWeightingTests(unittest.TestCase):
    """A clean 'no nuts listed' chart from a stale/off-site copy must NOT pull risk
    down as hard as an official, recent one -- safety-conservative on absence."""

    def _risk(self, url: str) -> float:
        # Thai (elevated prior) + a matrix that lists NO nuts -> clean down-pull.
        return score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["thai"],
            region="US",
            menu_items=[_matrix_item("Pad Thai", allergen_terms=["soy"], url=url)],
            official_domain="official.test",
        ).overall_risk

    def test_stale_offsite_chart_keeps_more_risk_than_official_recent(self) -> None:
        official_recent = self._risk("https://official.test/allergens-2026.pdf")
        stale_offsite = self._risk("https://allergyblog.example.com/2012/02/menu.pdf")
        self.assertGreater(stale_offsite, official_recent)

    def test_presence_hit_is_not_discounted_by_provenance(self) -> None:
        # A stale off-site chart that MARKS nuts present is still a confirmed hit.
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["thai"],
            region="US",
            menu_items=[_matrix_item("Satay", allergen_terms=["peanut"],
                                     url="https://allergyblog.example.com/2012/02/menu.pdf")],
            official_domain="official.test",
        )
        self.assertGreaterEqual(result.overall_risk, 0.9)


class PerDishGuidanceTests(unittest.TestCase):
    """A complete chart with a few nut dishes among many safe ones is navigable
    (CAUTION + name the dishes), not a blanket AVOID -- except anaphylaxis/pervasive."""

    def _matrix(self, nut_dishes, safe_dishes):
        items = [_matrix_item(n, allergen_terms=["peanut"], url="https://official.test/a.pdf")
                 for n in nut_dishes]
        items += [_matrix_item(n, allergen_terms=["soy"], url="https://official.test/a.pdf")
                  for n in safe_dishes]
        return items

    def test_navigable_matrix_is_caution_and_names_dishes(self) -> None:
        items = self._matrix(["Satay"], ["Rice", "Salad", "Soup", "Curry", "Bowl"])
        r = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["thai"], region="US",
            menu_items=items, official_domain="official.test",
        )
        self.assertEqual(r.tier, Tier.CAUTION.value)
        names = [x["itemName"] for x in r.per_allergen[0].riskiest_items]
        self.assertIn("Satay", names)
        self.assertNotIn("Rice", names)

    def test_anaphylaxis_stays_avoid_even_when_navigable(self) -> None:
        items = self._matrix(["Satay"], ["Rice", "Salad", "Soup", "Curry", "Bowl"])
        r = score_restaurant_for_user(
            NUT_ANAPHYLAXIS, cuisines=["thai"], region="US",
            menu_items=items, official_domain="official.test",
        )
        self.assertEqual(r.tier, Tier.AVOID.value)

    def test_pervasive_nuts_is_avoid(self) -> None:
        items = self._matrix(["A", "B", "C", "D"], ["Rice"])  # 4/5 dishes have nuts
        r = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["thai"], region="US",
            menu_items=items, official_domain="official.test",
        )
        self.assertEqual(r.tier, Tier.AVOID.value)


class CrossContactDecouplingTests(unittest.TestCase):
    """Cross-contact concern is INDEPENDENT of ingestion severity: an anaphylactic
    user who isn't worried about traces can navigate a mostly-safe menu, while a
    trace-sensitive user is locked out even at a milder severity."""

    def _navigable_matrix(self):
        items = [_matrix_item("Satay", allergen_terms=["peanut"], url="https://official.test/a.pdf")]
        items += [_matrix_item(n, allergen_terms=["soy"], url="https://official.test/a.pdf")
                  for n in ("Rice", "Salad", "Soup", "Curry", "Bowl")]
        return items

    def test_anaphylaxis_low_cross_contact_navigates_to_caution(self) -> None:
        # The headline case: anaphylactic to ingestion, but traces aren't a concern.
        profile = UserProfile.for_nuts(
            Severity.ANAPHYLAXIS, cross_contact=CrossContactSensitivity.NOT_CONCERNED
        )
        r = score_restaurant_for_user(
            profile, cuisines=["thai"], region="US",
            menu_items=self._navigable_matrix(), official_domain="official.test",
        )
        self.assertEqual(r.tier, Tier.CAUTION.value)
        # Navigable: the explanation tells the user they can avoid the flagged dishes.
        self.assertTrue(any("eat safely" in x.lower() for x in r.per_allergen[0].rationale))

    def test_default_anaphylaxis_still_avoids(self) -> None:
        # No explicit cross-contact -> derives STRICT -> AVOID (back-compat preserved).
        r = score_restaurant_for_user(
            NUT_ANAPHYLAXIS, cuisines=["thai"], region="US",
            menu_items=self._navigable_matrix(), official_domain="official.test",
        )
        self.assertEqual(r.tier, Tier.AVOID.value)

    def test_strict_cross_contact_blocks_navigation_for_allergy(self) -> None:
        profile = UserProfile.for_nuts(
            Severity.ALLERGY, cross_contact=CrossContactSensitivity.STRICT
        )
        r = score_restaurant_for_user(
            profile, cuisines=["thai"], region="US",
            menu_items=self._navigable_matrix(), official_domain="official.test",
        )
        self.assertEqual(r.tier, Tier.AVOID.value)

    def test_not_concerned_ignores_may_contain_warning(self) -> None:
        profile = UserProfile.for_nuts(
            Severity.ANAPHYLAXIS, cross_contact=CrossContactSensitivity.NOT_CONCERNED
        )
        warned = score_restaurant_for_user(
            profile, cuisines=["japanese"], region="US",
            signals=RestaurantSignals(cross_contact_warning=True),
        )
        baseline = score_restaurant_for_user(
            profile, cuisines=["japanese"], region="US",
        )
        # A 'may contain' warning must not raise risk for a trace-tolerant user.
        self.assertAlmostEqual(warned.overall_risk, baseline.overall_risk, places=6)

    def test_strict_user_is_floored_by_may_contain_warning(self) -> None:
        profile = UserProfile.for_nuts(
            Severity.ALLERGY, cross_contact=CrossContactSensitivity.STRICT
        )
        r = score_restaurant_for_user(
            profile, cuisines=["japanese"], region="US",
            signals=RestaurantSignals(cross_contact_warning=True),
        )
        self.assertGreaterEqual(r.overall_risk, 0.45 - 1e-9)


class VeganCueTests(unittest.TestCase):
    """Vegan/vegetarian kitchens lean on nuts (cashew cheese, nut milks) -- the type
    must be recognized and carry an elevated baseline, not be mistaken for 'safe'."""

    def test_vegan_type_recognized_and_elevated(self):
        from safeplate.allergen_prior import normalize_cuisine, score_restaurant_prior
        cz = normalize_cuisine(["primary_type:vegan_restaurant", "japanese"])
        self.assertIn("vegan", cz)
        prior = score_restaurant_prior(cuisines=cz, region="US", allergen="nuts")
        self.assertGreaterEqual(prior.risk, 0.4)

    def test_vegan_kitchen_is_caution_not_likely_ok(self):
        from safeplate.allergen_prior import normalize_cuisine
        cz = normalize_cuisine(["primary_type:vegan_restaurant", "japanese"])
        r = score_restaurant_for_user(NUT_ALLERGY, cuisines=cz, region="US")
        self.assertEqual(r.tier, Tier.CAUTION.value)


class TierContractTests(unittest.TestCase):
    def test_lowest_tier_is_never_safe(self) -> None:
        # The whole design forbids a bare "safe" verdict.
        self.assertEqual({t.value for t in Tier}, {"likely_ok", "caution", "avoid"})
        result = score_restaurant_for_user(
            NUT_PREF, cuisines=["japanese"], region="US"
        )
        self.assertNotIn(result.tier, {"safe"})
        self.assertEqual(result.tier, Tier.LIKELY_OK.value)


class PriorOnlyTests(unittest.TestCase):
    def test_high_nut_cuisine_no_menu_caps_at_caution(self) -> None:
        # Thai in Thailand: strong prior, but no grounded evidence -> CAUTION, not AVOID.
        result = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["thai"], region="TH"
        )
        self.assertEqual(result.tier, Tier.CAUTION.value)
        self.assertEqual(result.evidence_basis, "cuisine_prior")

    def test_low_nut_cuisine_preference_user_is_likely_ok(self) -> None:
        result = score_restaurant_for_user(
            NUT_PREF, cuisines=["japanese"], region="US"
        )
        self.assertEqual(result.tier, Tier.LIKELY_OK.value)

    def test_dish_name_prior_caps_at_caution(self) -> None:
        # "pad thai" implies peanuts but is not a CONFIRMED disclosure.
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["thai"],
            region="US",
            menu_items=[_item("Pad Thai")],
        )
        self.assertEqual(result.tier, Tier.CAUTION.value)
        self.assertEqual(result.evidence_basis, "dish_prior")


class GroundedPresenceTests(unittest.TestCase):
    def test_matrix_marks_peanut_present_is_avoid(self) -> None:
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["american"],
            region="US",
            menu_items=[_item("House Salad", allergen_terms=["peanut"], method="allergen_matrix")],
        )
        self.assertEqual(result.tier, Tier.AVOID.value)
        self.assertEqual(result.evidence_basis, "allergen_matrix")
        self.assertGreaterEqual(result.overall_risk, 0.9)

    def test_matrix_presence_is_avoid_even_for_mild_preference(self) -> None:
        # A confirmed chart hit overrides severity-based thresholds.
        result = score_restaurant_for_user(
            NUT_PREF,
            cuisines=["american"],
            region="US",
            menu_items=[_item("Brownie", allergen_terms=["tree nut"], method="gemini_pdf_matrix")],
        )
        self.assertEqual(result.tier, Tier.AVOID.value)

    def test_free_text_mention_raises_to_avoid_for_allergy(self) -> None:
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["italian"],
            region="US",
            menu_items=[_item("Pesto Pasta", allergen_terms=["walnut"], method="gemini_text")],
        )
        self.assertEqual(result.tier, Tier.AVOID.value)
        self.assertEqual(result.evidence_basis, "menu_evidence")


class CleanSignalGatingTests(unittest.TestCase):
    def test_clean_matrix_lowers_risk_for_allergy_user(self) -> None:
        # A complete chart that lists NO nut is the clean down-signal.
        clean = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["american"],
            region="US",
            menu_items=[_item("Burger", allergen_terms=["milk", "egg"], method="allergen_matrix")],
        )
        baseline = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["american"], region="US"
        )
        self.assertLess(clean.overall_risk, baseline.overall_risk)
        self.assertEqual(clean.evidence_basis, "allergen_matrix")
        self.assertEqual(clean.tier, Tier.LIKELY_OK.value)

    def test_clean_matrix_floors_higher_for_anaphylaxis(self) -> None:
        # Same clean chart, anaphylactic user: cross-contact is never ruled out,
        # so risk floors at the severity floor and stays at CAUTION.
        clean = score_restaurant_for_user(
            NUT_ANAPHYLAXIS,
            cuisines=["american"],
            region="US",
            menu_items=[_item("Burger", allergen_terms=["milk", "egg"], method="allergen_matrix")],
        )
        self.assertGreaterEqual(clean.overall_risk, 0.20 - 1e-9)
        self.assertEqual(clean.tier, Tier.CAUTION.value)

    def test_low_labeling_trust_pulls_down_less(self) -> None:
        high = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["american"],
            region="US",  # high labeling trust
            menu_items=[_item("Burger", allergen_terms=["milk"], method="allergen_matrix")],
        )
        low = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["american"],
            region="unknown",  # low labeling trust
            menu_items=[_item("Burger", allergen_terms=["milk"], method="allergen_matrix")],
        )
        self.assertLess(high.overall_risk, low.overall_risk)

    def test_nut_free_claim_lowers_risk(self) -> None:
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["american"],
            region="US",
            signals=RestaurantSignals(nut_free_claim=True),
        )
        baseline = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["american"], region="US"
        )
        self.assertLess(result.overall_risk, baseline.overall_risk)
        self.assertEqual(result.evidence_basis, "restaurant_signal")

    def test_cross_contact_warning_raises_floor(self) -> None:
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["japanese"],  # low base prior
            region="US",
            signals=RestaurantSignals(cross_contact_warning=True),
        )
        self.assertGreaterEqual(result.overall_risk, 0.35 - 1e-9)
        self.assertEqual(result.tier, Tier.CAUTION.value)
        self.assertTrue(result.handling.cross_contact_warning)


class CommunityTierTests(unittest.TestCase):
    def test_adverse_report_raises_risk_and_flags_provenance(self) -> None:
        with_community = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["japanese"],
            region="US",
            community=[CommunitySignal(type="adverse_event", allergen=NUTS, quote="peanut reaction", age_days=30)],
        )
        baseline = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["japanese"], region="US"
        )
        self.assertGreater(with_community.overall_risk, baseline.overall_risk)
        self.assertTrue(with_community.community_reported)
        self.assertTrue(any("Community-reported" in r for r in with_community.rationale))

    def test_strong_consistent_adverse_reaches_avoid(self) -> None:
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["thai"],  # already elevated prior
            region="US",
            community=[
                CommunitySignal(type="adverse_event", allergen=NUTS, quote="sent my son to the ER", age_days=20),
                CommunitySignal(type="adverse_event", allergen=NUTS, quote="nut reaction here too", age_days=60),
            ],
        )
        self.assertEqual(result.tier, Tier.AVOID.value)
        self.assertTrue(result.community_reported)

    def test_positive_review_never_lowers_risk_only_handling(self) -> None:
        baseline = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["thai"], region="US"
        )
        good = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["thai"],
            region="US",
            community=[CommunitySignal(type="good_handling", quote="amazing with my nut allergy")],
        )
        self.assertEqual(good.overall_risk, baseline.overall_risk)
        self.assertTrue(good.handling.allergy_aware)
        self.assertEqual(good.handling.community_praise, 1)

    def test_allergen_mismatch_does_not_escalate_user_tier(self) -> None:
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["japanese"],
            region="US",
            community=[CommunitySignal(type="adverse_event", allergen="shellfish", quote="shellfish reaction", age_days=10)],
        )
        baseline = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["japanese"], region="US"
        )
        self.assertEqual(result.overall_risk, baseline.overall_risk)
        self.assertFalse(result.community_reported)
        self.assertTrue(any("Other diners reported" in r for r in result.rationale))

    def test_no_community_is_a_noop(self) -> None:
        a = score_restaurant_for_user(NUT_ALLERGY, cuisines=["thai"], region="US")
        b = score_restaurant_for_user(NUT_ALLERGY, cuisines=["thai"], region="US", community=[])
        self.assertEqual(a.overall_risk, b.overall_risk)
        self.assertFalse(a.community_reported)


class AggregationAndRankingTests(unittest.TestCase):
    def test_worst_allergen_drives_overall_tier(self) -> None:
        profile = UserProfile(
            allergens=(
                AllergenPref(allergen=PEANUTS, severity=Severity.ALLERGY),
                AllergenPref(allergen=TREE_NUTS, severity=Severity.ANAPHYLAXIS),
            )
        )
        result = score_restaurant_for_user(
            profile,
            cuisines=["american"],
            region="US",
            menu_items=[_item("Satay Skewers", allergen_terms=["peanut"], method="allergen_matrix")],
        )
        self.assertEqual(result.tier, Tier.AVOID.value)
        self.assertEqual(len(result.per_allergen), 2)
        peanut = next(a for a in result.per_allergen if a.allergen == PEANUTS)
        self.assertEqual(peanut.tier, Tier.AVOID.value)

    def test_ranking_orders_safest_first(self) -> None:
        avoid = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["american"], region="US",
            menu_items=[_item("Cookie", allergen_terms=["peanut"], method="allergen_matrix")],
        )
        caution = score_restaurant_for_user(NUT_ALLERGY, cuisines=["thai"], region="TH")
        likely_ok = score_restaurant_for_user(NUT_PREF, cuisines=["japanese"], region="US")
        rows = [
            {"name": "avoid", "a": avoid, "q": 0.9},
            {"name": "caution", "a": caution, "q": 0.5},
            {"name": "ok", "a": likely_ok, "q": 0.1},
        ]
        ranked = rank_restaurants_for_user(
            rows, get_assessment=lambda r: r["a"], get_quality=lambda r: r["q"]
        )
        self.assertEqual([r["name"] for r in ranked], ["ok", "caution", "avoid"])


class ConvenienceWrapperTests(unittest.TestCase):
    def test_assess_record_derives_cuisine_and_region(self) -> None:
        record = SimpleNamespace(
            categories=["primary_type:thai_restaurant"],
            address="123 Main St, Bangkok, Thailand",
            latitude=13.7,
            longitude=100.5,
        )
        result = assess_restaurant_record(record, NUT_ALLERGY)
        self.assertEqual(result.tier, Tier.CAUTION.value)
        self.assertEqual(result.evidence_basis, "cuisine_prior")


class SuspectedNutsTests(unittest.TestCase):
    """Option-2 recall: dishes whose TYPE often hides nuts are flagged as an
    assumption -- moderate risk, LOW confidence -- not treated as clearly safe."""

    NUT = UserProfile.for_nuts(Severity.ALLERGY, cross_contact=CrossContactSensitivity.MODERATE)

    def _score(self, menu, cuisines=("american",)):
        return score_restaurant_for_user(self.NUT, cuisines=list(cuisines), region="US",
                                         menu_items=menu)

    def test_suspected_dish_flips_clean_menu_to_caution_at_low_confidence(self):
        clean = self._score([_item("Burger")] + [_item(f"Dish {i}") for i in range(20)])
        withb = self._score([_item("Chocolate Brownie")] + [_item(f"Dish {i}") for i in range(20)])
        self.assertEqual(clean.tier, Tier.LIKELY_OK.value)
        self.assertEqual(withb.tier, Tier.CAUTION.value)          # assumption made
        self.assertGreater(withb.overall_risk, clean.overall_risk)
        self.assertLessEqual(withb.per_allergen[0].confidence, 0.45)  # but low confidence
        self.assertEqual(withb.per_allergen[0].menu_suspected, 1)

    def test_named_nut_keeps_high_confidence_vs_suspected(self):
        named = self._score([_item("Peanut Noodles")] + [_item(f"Dish {i}") for i in range(20)])
        susp = self._score([_item("Brownie")] + [_item(f"Dish {i}") for i in range(20)])
        self.assertGreater(named.per_allergen[0].confidence, susp.per_allergen[0].confidence)
        self.assertEqual(named.per_allergen[0].menu_flagged, 1)

    def test_suspected_only_stays_in_caution_band_not_avoid(self):
        r = self._score([_item(n) for n in ["Brownie", "Cookie Sundae", "Carrot Cake", "Gelato"]])
        self.assertEqual(r.tier, Tier.CAUTION.value)              # never likely_ok, never avoid
        self.assertEqual(r.evidence_basis, "suspected_nuts")

    def test_suspected_never_lowers_risk_below_clean(self):
        clean = self._score([_item(f"Dish {i}") for i in range(20)]).overall_risk
        susp = self._score([_item("Curry")] + [_item(f"Dish {i}") for i in range(20)]).overall_risk
        self.assertGreaterEqual(susp, clean)                     # recall raises, never lowers

    def test_vegan_dairy_analogue_is_suspected(self):
        r = score_restaurant_for_user(
            self.NUT, cuisines=["vegan"], region="US",
            menu_items=[_item("House Cheese Plate")] + [_item(f"Dish {i}") for i in range(10)])
        # 'cheese' at a vegan kitchen -> suspected cashew (the hidden-nut trap)
        self.assertTrue(any(it.get("suspected") for it in r.per_allergen[0].riskiest_items))


class NavigabilityTests(unittest.TestCase):
    """Phase F: the score answers 'can I eat safely here?', not 'any nuts in the
    kitchen?'. A labeled chain with a few avoidable nut dishes must rank SAFER than
    an unlabeled high-nut independent -- transparency is rewarded, not punished."""

    NUT = UserProfile.for_nuts(
        Severity.ANAPHYLAXIS, cross_contact=CrossContactSensitivity.MODERATE
    )

    def _menu(self, safe_n, nutty_names, *, method="gemini_text"):
        items = [_item(f"House Dish {i}") for i in range(safe_n)]
        terms = ["peanut"] if method != "gemini_text" else []
        items += [_item(n, allergen_terms=terms, method=method) for n in nutty_names]
        return items

    def _risk(self, **kw):
        return score_restaurant_for_user(self.NUT, **kw)

    def test_labeled_chain_ranks_below_unlabeled_independent(self) -> None:
        bjs = self._risk(
            cuisines=["american"], region="US",
            menu_items=self._menu(88, ["Peanut Butter S'mores", "Macadamia Nut Pizookie"]),
            signals=RestaurantSignals(allergy_disclaimer=True, ask_staff=True),
        )
        indian = self._risk(cuisines=["indian"], region="US")  # unlabeled, no menu
        self.assertLess(bjs.overall_risk, indian.overall_risk)   # the headline fix
        self.assertEqual(bjs.tier, Tier.CAUTION.value)
        self.assertLess(bjs.overall_risk, 0.45)                  # not pinned at 0.97

    def test_confirmed_chart_beats_dishname_beats_unlabeled(self) -> None:
        chart = self._risk(
            cuisines=["american"], region="US",
            menu_items=self._menu(88, ["Satay"], method="gemini_allergen_matrix"))
        names = self._risk(
            cuisines=["american"], region="US",
            menu_items=self._menu(88, ["Peanut Satay"]))
        unlabeled = self._risk(cuisines=["american"], region="US")
        self.assertLess(chart.overall_risk, names.overall_risk)  # confirmed labels safest

    def test_pervasive_nuts_stays_high(self) -> None:
        r = self._risk(
            cuisines=["thai"], region="US",
            menu_items=[_item(n) for n in
                        ["Pad Thai", "Satay", "Peanut Curry", "Cashew Chicken", "Massaman"]]
            + [_item("Jasmine Rice")])
        self.assertGreater(r.overall_risk, 0.7)   # nuts unavoidable -> high

    def test_trace_sensitive_user_gets_high_score(self) -> None:
        strict = UserProfile.for_nuts(
            Severity.ANAPHYLAXIS, cross_contact=CrossContactSensitivity.STRICT)
        r = score_restaurant_for_user(
            strict, cuisines=["american"], region="US",
            menu_items=self._menu(88, ["Satay"], method="gemini_allergen_matrix"))
        self.assertEqual(r.tier, Tier.AVOID.value)   # nut kitchen disqualifying for traces

    def test_handling_signals_lower_the_score(self) -> None:
        menu = self._menu(88, ["Peanut Butter Pie"])
        bare = self._risk(cuisines=["american"], region="US", menu_items=menu)
        aware = self._risk(cuisines=["american"], region="US", menu_items=menu,
                           signals=RestaurantSignals(allergy_disclaimer=True, ask_staff=True))
        self.assertLess(aware.overall_risk, bare.overall_risk)


class CoverageDeQuantizationTests(unittest.TestCase):
    """Phase A: same-cuisine places must diverge by how much menu we actually
    parsed, instead of every same-cuisine restaurant snapping to one constant."""

    def _risk(self, menu):
        return score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["chinese"], region="US", menu_items=menu
        ).overall_risk

    def test_parsed_clean_menu_ranks_below_no_menu_same_cuisine(self) -> None:
        no_menu = self._risk([])
        parsed = self._risk([_item(f"Dish {i}") for i in range(20)])
        self.assertLess(parsed, no_menu)  # the tie is broken
        self.assertGreater(parsed, 0.12)  # but never "safe" -- floored

    def test_more_coverage_lowers_risk_continuously(self) -> None:
        small = self._risk([_item(f"Dish {i}") for i in range(3)])
        large = self._risk([_item(f"Dish {i}") for i in range(20)])
        no_menu = self._risk([])
        # Monotone: more clean coverage -> lower risk, all below the no-menu prior.
        self.assertLess(large, small)
        self.assertLess(small, no_menu)

    def test_evidence_ladder_orders_correctly(self) -> None:
        clean_menu = [_item(f"Dish {i}") for i in range(20)]
        clean_chart = [_matrix_item(f"Dish {i}", allergen_terms=["milk"]) for i in range(20)]
        nut_dish = [_item("Kung Pao Chicken with peanuts", allergen_terms=["peanut"],
                          method="gemini_text")]
        chart_risk = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["chinese"], region="US", menu_items=clean_chart
        ).overall_risk
        # Confirmed clean chart < informal menu review < no menu < confirmed nut dish.
        self.assertLess(chart_risk, self._risk(clean_menu))
        self.assertLess(self._risk(clean_menu), self._risk([]))
        self.assertLess(self._risk([]), self._risk(nut_dish))

    def test_coverage_discount_does_not_apply_when_nut_dish_present(self) -> None:
        # A named nut dish keeps the dish_prior basis -- no clean-coverage discount.
        result = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["chinese"], region="US",
            menu_items=[_item("Pad Thai with peanuts")]
            + [_item(f"Dish {i}") for i in range(20)],
        )
        self.assertNotEqual(result.evidence_basis, "menu_coverage")

    def test_mandate_region_clean_menu_reassures_more_than_us(self) -> None:
        menu = [_item(f"Dish {i}") for i in range(20)]
        us = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["chinese"], region="US", menu_items=menu
        ).overall_risk
        gb = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["chinese"], region="GB", menu_items=menu
        ).overall_risk
        # UK mandates restaurant allergen disclosure -> a clean menu means more.
        self.assertLess(gb, us)

    def test_parsed_clean_menu_sets_menu_coverage_basis(self) -> None:
        result = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["chinese"], region="US",
            menu_items=[_item(f"Dish {i}") for i in range(20)],
        )
        self.assertEqual(result.evidence_basis, "menu_coverage")


class RiskiestItemsPrecisionTests(unittest.TestCase):
    """The drawer's 'items likely contain nuts' list must only surface dishes with a
    real PER-DISH nut signal -- named/confirmed, or suspected-by-type (flagged). A plain
    dish that only sits on a high-nut CUISINE baseline (e.g. Jasmine Rice at a Thai
    place) has no per-dish signal and must not be listed as a nut item."""

    def _riskiest(self):
        items = [
            _item("Jasmine Rice"), _item("Steamed Edamame"),   # plain, cuisine-floor only
            _item("Walnut Prawn"),                              # named nut
            _item("Chocolate Brownie"), _item("Green Curry"),  # suspected by dish type
        ]
        r = score_restaurant_for_user(
            NUT_ALLERGY, cuisines=["thai"], region="US", menu_items=items
        )
        return {x["itemName"]: x for x in r.per_allergen[0].riskiest_items}

    def test_cuisine_floor_dishes_are_not_listed(self) -> None:
        ri = self._riskiest()
        self.assertNotIn("Jasmine Rice", ri)
        self.assertNotIn("Steamed Edamame", ri)

    def test_named_nut_dish_listed_as_confirmed(self) -> None:
        ri = self._riskiest()
        self.assertIn("Walnut Prawn", ri)
        self.assertFalse(ri["Walnut Prawn"]["suspected"])

    def test_suspected_dish_listed_but_flagged(self) -> None:
        ri = self._riskiest()
        self.assertIn("Chocolate Brownie", ri)
        self.assertTrue(ri["Chocolate Brownie"]["suspected"])


if __name__ == "__main__":
    unittest.main()
