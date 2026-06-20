from __future__ import annotations

import unittest

import safeplate.allergy_registry as reg
from safeplate.allergen_score import (
    RestaurantSignals, Severity, UserProfile, score_restaurant_for_user,
)

FIXTURE = [
    {"name": "Safe Sweets Bakery", "city": "townsville", "domain": "safesweets.test",
     "nut_free": True, "allergy_dedicated": True,
     "source": "https://x.test/list", "verified": "2026-06-17"},
]


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self._orig = reg._ENTRIES
        reg._ENTRIES = list(FIXTURE)

    def tearDown(self):
        reg._ENTRIES = self._orig

    def test_match_by_domain(self):
        e = reg.lookup_registry("Totally Different Name", None, "https://www.safesweets.test/menu")
        self.assertIsNotNone(e)
        self.assertTrue(e["nut_free"])

    def test_match_by_name_and_city(self):
        e = reg.lookup_registry("Safe Sweets Bakery", "12 Main St, Townsville, CA", None)
        self.assertIsNotNone(e)

    def test_right_name_wrong_city_does_not_match(self):
        # Conservative: a name match alone (wrong city) must NOT credit a nut-free claim.
        self.assertIsNone(reg.lookup_registry("Safe Sweets Bakery", "Otherville, NY", None))

    def test_unrelated_no_match(self):
        self.assertIsNone(reg.lookup_registry("Joe's Diner", "Townsville, CA", "https://joes.test"))

    def test_apply_sets_signals_and_lowers_score(self):
        sig = RestaurantSignals()
        entry = reg.apply_registry(sig, "Safe Sweets Bakery", "Townsville, CA",
                                   "https://safesweets.test")
        self.assertIsNotNone(entry)
        self.assertTrue(sig.nut_free_claim)
        self.assertTrue(sig.allergy_disclaimer)
        profile = UserProfile.for_nuts(Severity.ALLERGY)
        base = score_restaurant_for_user(profile, cuisines=["bakery"], region="US")
        with_reg = score_restaurant_for_user(profile, cuisines=["bakery"], region="US", signals=sig)
        self.assertLess(with_reg.overall_risk, base.overall_risk)


if __name__ == "__main__":
    unittest.main()
