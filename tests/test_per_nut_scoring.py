"""Per-nut scoring: when a user narrows their allergy to SPECIFIC nuts, only those
nuts drive the contains/avoid verdict (strict per-nut), other nuts add only a small
cross-contact allowance, and family-level evidence we can't disaggregate (a chart's
'tree nut' column, a generic 'nuts' mention) still counts for any tree-nut selection.

The DEFAULT (all nuts) path must stay byte-identical to the family-level behavior --
that invariant is what keeps the calibrated quality gate unchanged.
"""
from __future__ import annotations

import unittest

from safeplate.allergen_prior import (
    ALMOND, CASHEW, HAZELNUT, PEANUTS, NUT_TYPES,
    families_for_nut_types, normalize_nut_types, specific_tree_nuts,
)
from safeplate.allergen_score import (
    CrossContactSensitivity, Severity, UserProfile, Tier,
    _split_nut_terms, score_restaurant_for_user,
)

PEANUT = PEANUTS
_MATRIX_COLS = ("peanut", "tree nut", "milk", "egg")


def _text_item(name, terms):
    return {"item_name": name, "description": "", "allergen_terms": terms,
            "extraction_method": "gemini_text"}


def _matrix_item(name, terms, *, cols=_MATRIX_COLS, url="https://r.test/chart"):
    return {"item_name": name, "description": "", "allergen_terms": terms,
            "extraction_method": "gemini_allergen_matrix",
            "menu_source_url": url, "matrix_allergen_columns": cols}


def _score(profile, items, *, cuisines=None, region="US"):
    return score_restaurant_for_user(
        profile, cuisines=cuisines or ["american"], region=region, menu_items=items
    )


class NormalizeNutTypesTests(unittest.TestCase):
    def test_none_and_all_collapse_to_default(self):
        self.assertIsNone(normalize_nut_types(None))
        self.assertIsNone(normalize_nut_types([]))
        self.assertIsNone(normalize_nut_types(list(NUT_TYPES)))        # everything -> default
        self.assertIsNone(normalize_nut_types(["tree nut", "peanut"]))  # expands to all

    def test_subset_and_aliases(self):
        self.assertEqual(normalize_nut_types(["almond"]), frozenset({ALMOND}))
        self.assertEqual(normalize_nut_types(["Almond", "CASHEW"]), frozenset({ALMOND, CASHEW}))
        self.assertEqual(normalize_nut_types(["pine"]), frozenset({"pine_nut"}))
        self.assertEqual(normalize_nut_types(["brazil"]), frozenset({"brazil_nut"}))
        self.assertEqual(normalize_nut_types(["peanut"]), frozenset({PEANUT}))
        self.assertEqual(normalize_nut_types(["garbage"]), None)


class TermClassificationTests(unittest.TestCase):
    def test_specific_tree_nuts_mapping(self):
        self.assertEqual(specific_tree_nuts("marzipan"), frozenset({ALMOND}))
        self.assertEqual(specific_tree_nuts("gianduja"), frozenset({HAZELNUT}))
        self.assertEqual(specific_tree_nuts("cashew"), frozenset({CASHEW}))
        self.assertEqual(specific_tree_nuts("tree nut"), frozenset())  # unspecified

    def test_default_split_is_family_behavior(self):
        # wanted_nuts=None: every nut term is 'contains', nothing is 'other'.
        contains, other = _split_nut_terms(["almond", "cashew", "peanut"], {PEANUT, "tree_nuts"}, None)
        self.assertEqual(set(contains), {"almond", "cashew", "peanut"})
        self.assertEqual(other, [])

    def test_strict_almond_split(self):
        fams = families_for_nut_types(frozenset({ALMOND}))
        contains, other = _split_nut_terms(
            ["almond", "cashew", "walnut"], fams, frozenset({ALMOND})
        )
        self.assertEqual(contains, ["almond"])
        self.assertEqual(set(other), {"cashew", "walnut"})

    def test_unspecified_and_generic_count_for_any_tree_selection(self):
        fams = families_for_nut_types(frozenset({ALMOND}))
        contains, other = _split_nut_terms(["tree nut", "nuts"], fams, frozenset({ALMOND}))
        self.assertEqual(set(contains), {"tree nut", "nuts"})  # can't disaggregate -> safe
        self.assertEqual(other, [])

    def test_peanut_only_treats_tree_nuts_as_other(self):
        fams = families_for_nut_types(frozenset({PEANUT}))
        contains, other = _split_nut_terms(["peanut", "almond"], fams, frozenset({PEANUT}))
        self.assertEqual(contains, ["peanut"])
        self.assertEqual(other, ["almond"])


class PerNutScoringTests(unittest.TestCase):
    def test_default_flags_any_tree_nut(self):
        prof = UserProfile.for_nuts(Severity.ALLERGY)
        a = _score(prof, [_text_item("Cashew Chicken", ["cashew"]),
                          _text_item("Garden Salad", [])])
        self.assertGreaterEqual(Tier(a.tier).rank, Tier.CAUTION.rank)
        self.assertTrue(any("cashew" in r.lower() for r in a.rationale)
                        or a.per_allergen[0].menu_flagged >= 1)

    def test_almond_user_not_flagged_by_cashew_dish(self):
        almond = UserProfile.for_nuts(Severity.ALLERGY, nut_types=frozenset({ALMOND}))
        a = _score(almond, [_text_item("Cashew Chicken", ["cashew"]),
                            _text_item("Grilled Fish", []),
                            _text_item("House Salad", []),
                            _text_item("Steak Frites", [])])
        # Cashew is NOT the user's nut: it must not be a grounded 'contains' presence.
        self.assertEqual(a.per_allergen[0].menu_flagged, 0)
        self.assertNotEqual(a.tier, Tier.AVOID.value)

    def test_almond_user_flagged_by_almond_dish(self):
        almond = UserProfile.for_nuts(Severity.ALLERGY, nut_types=frozenset({ALMOND}))
        a = _score(almond, [_text_item("Marzipan Tart", ["marzipan"]),
                            _text_item("Grilled Fish", [])])
        self.assertGreaterEqual(a.per_allergen[0].menu_flagged, 1)
        self.assertTrue(any("almond" in r.lower() for r in a.rationale))

    def test_matrix_tree_nut_column_still_counts_for_almond_user(self):
        # A chart marks a dish 'tree nut' (unspecified which) -> we can't rule out
        # almond, so it MUST still count for an almond-only user (safety-first).
        almond = UserProfile.for_nuts(Severity.ALLERGY, nut_types=frozenset({ALMOND}))
        a = _score(almond, [_matrix_item("Mystery Cake", ["tree nut"]),
                            _matrix_item("Plain Rice", [])])
        self.assertGreaterEqual(a.per_allergen[0].menu_flagged, 1)

    def test_cross_contact_bump_small_and_never_avoid_alone(self):
        # Cashew-heavy kitchen, user reacts only to almond, no almond present.
        items = [_text_item("Cashew Chicken", ["cashew"]),
                 _text_item("Walnut Salad", ["walnut"]),
                 _text_item("Plain Rice", []), _text_item("Fish", [])]
        almond_strict = UserProfile.for_nuts(
            Severity.ALLERGY, cross_contact=CrossContactSensitivity.STRICT,
            nut_types=frozenset({ALMOND}),
        )
        almond_relaxed = UserProfile.for_nuts(
            Severity.ALLERGY, cross_contact=CrossContactSensitivity.NOT_CONCERNED,
            nut_types=frozenset({ALMOND}),
        )
        strict = _score(almond_strict, items)
        relaxed = _score(almond_relaxed, items)
        # The bump exists and is sensitivity-scaled, but never escalates to AVOID alone.
        self.assertGreaterEqual(strict.overall_risk, relaxed.overall_risk)
        self.assertNotEqual(strict.tier, Tier.AVOID.value)
        self.assertLessEqual(strict.overall_risk, 0.6)

    def test_all_selected_matches_default(self):
        items = [_text_item("Cashew Chicken", ["cashew"]), _text_item("Fish", [])]
        default = _score(UserProfile.for_nuts(Severity.ALLERGY), items)
        all_sel = _score(
            UserProfile.for_nuts(Severity.ALLERGY, nut_types=frozenset(NUT_TYPES)), items
        )
        self.assertEqual(default.overall_risk, all_sel.overall_risk)
        self.assertEqual(default.tier, all_sel.tier)


if __name__ == "__main__":
    unittest.main()
