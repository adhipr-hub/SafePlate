from __future__ import annotations

import unittest

from eval.datasets import labeled_restaurants as ds
from safeplate.allergen_score import (
    Severity, UserProfile, score_restaurant_for_user,
)

NUT = UserProfile.for_nuts(Severity.ALLERGY)


class LabeledBenchmarkTests(unittest.TestCase):
    def test_dataset_is_well_formed(self):
        self.assertGreaterEqual(len(ds.LABELED), 40)
        for c in ds.LABELED:
            self.assertIn(c["truth"], ("pos", "neg"))
            self.assertTrue(c["cuisines"])
            self.assertIn("region", c)
        self.assertTrue(ds.positives() and ds.negatives())

    def test_score_kwargs_only_passes_scorer_args(self):
        sample = next(c for c in ds.LABELED if c.get("menu_items"))
        kw = ds.score_kwargs(sample)
        self.assertLessEqual(set(kw), {"cuisines", "region", "menu_items", "signals", "community"})
        # The kwargs must actually drive the scorer without error.
        score_restaurant_for_user(NUT, **kw)

    def test_deterministic_scorer_has_no_false_negatives_on_benchmark(self):
        # A POSITIVE scored 'likely_ok' is a dangerous miss; the safety-asymmetric
        # design should never do that on the labeled set.
        misses = []
        for c in ds.positives():
            res = score_restaurant_for_user(NUT, **ds.score_kwargs(c))
            if res.tier == "likely_ok":
                misses.append(c["name"])
        self.assertEqual(misses, [], f"false negatives: {misses}")


class CalibrationToolTests(unittest.TestCase):
    def test_spearman_handles_ties_and_ordering(self):
        from eval.calibrate_priors import _spearman
        # Perfectly correlated -> 1.0; perfectly inverted -> -1.0.
        self.assertAlmostEqual(_spearman([(1, 1), (2, 2), (3, 3)]), 1.0, places=6)
        self.assertAlmostEqual(_spearman([(1, 3), (2, 2), (3, 1)]), -1.0, places=6)


if __name__ == "__main__":
    unittest.main()
