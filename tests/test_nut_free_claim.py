from __future__ import annotations

import unittest

import safeplate.extraction2.allergy_signals as asig
from safeplate.extraction2.schema import Payload, PayloadKind
from safeplate.allergen_score import (
    RestaurantSignals, Severity, UserProfile, score_restaurant_for_user,
)


def _payload(text):
    return Payload(url="https://x.test/about", source_type="website_link",
                   kind=PayloadKind.TEXT, text=text, content=b"")


def _mock(parsed):
    asig._cached_or_call = lambda text, *, api_key, model: parsed


class NutFreeDetectionTests(unittest.TestCase):
    def setUp(self):
        self._orig = asig._cached_or_call

    def tearDown(self):
        asig._cached_or_call = self._orig

    def _flags(self, **over):
        base = {"allergy_friendly_claim": False, "cross_contact_warning": False,
                "ask_staff": False, "allergen_menu_available": False,
                "nut_free_claim": False, "statements": []}
        base.update(over)
        return base

    def test_grounded_facility_claim_is_honored(self):
        src = "Welcome! We are a 100% nut-free facility. Everything is made without nuts."
        _mock(self._flags(nut_free_claim=True,
                          statements=["We are a 100% nut-free facility"]))
        sig = asig.extract_allergy_signals(_payload(src), api_key="k")
        self.assertIsNotNone(sig)
        self.assertTrue(sig.nut_free_claim)

    def test_nut_free_options_is_not_a_facility_claim(self):
        src = "We offer nut-free options on request for guests with allergies."
        _mock(self._flags(nut_free_claim=True,
                          statements=["We offer nut-free options on request"]))
        sig = asig.extract_allergy_signals(_payload(src), api_key="k")
        # 'option' phrasing -> not a nut-free KITCHEN -> down-signal withheld
        self.assertTrue(sig is None or not sig.nut_free_claim)

    def test_ungrounded_claim_is_dropped(self):
        src = "Fresh pastries baked daily. Visit us downtown."   # no nut-free wording
        _mock(self._flags(nut_free_claim=True,
                          statements=["We are a 100% nut-free facility"]))  # not in src
        sig = asig.extract_allergy_signals(_payload(src), api_key="k")
        self.assertTrue(sig is None or not sig.nut_free_claim)

    def test_maps_into_restaurant_signals(self):
        from safeplate.extraction2.schema import AllergySignal
        sig = AllergySignal(url="x", allergy_friendly_claim=False,
                            cross_contact_warning=False, ask_staff=False,
                            allergen_menu_available=False, statements=[],
                            confidence=0.5, nut_free_claim=True)
        rs = RestaurantSignals.from_allergy_signals([sig])
        self.assertTrue(rs.nut_free_claim)

    def test_nut_free_claim_lowers_score_end_to_end(self):
        profile = UserProfile.for_nuts(Severity.ALLERGY)
        baseline = score_restaurant_for_user(profile, cuisines=["american"], region="US")
        with_claim = score_restaurant_for_user(
            profile, cuisines=["american"], region="US",
            signals=RestaurantSignals(nut_free_claim=True))
        self.assertLess(with_claim.overall_risk, baseline.overall_risk)


if __name__ == "__main__":
    unittest.main()
