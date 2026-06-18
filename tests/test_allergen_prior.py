from __future__ import annotations

import unittest

from safeplate.allergen_prior import (
    normalize_cuisine,
    region_from_address,
    restaurant_nut_risk,
    score_menu_item_prior,
    score_restaurant_prior,
)


class CuisineAndRegionNormalizationTests(unittest.TestCase):
    def test_normalizes_osm_and_google_cuisine_styles(self) -> None:
        self.assertEqual(normalize_cuisine(["cuisine:indian"]), ["indian"])
        self.assertEqual(normalize_cuisine(["indian_restaurant", "food"]), ["indian"])
        self.assertEqual(
            normalize_cuisine(["primary_type:thai_restaurant"]), ["thai"]
        )

    def test_normalizes_aliases_and_multivalue(self) -> None:
        self.assertEqual(normalize_cuisine(["cuisine:lebanese"]), ["middle_eastern"])
        self.assertEqual(
            normalize_cuisine(["cuisine:indian;thai"]), ["indian", "thai"]
        )

    def test_kebab_and_other_real_osm_tags_normalize(self) -> None:
        # Regression: Cupertino's Dish n' Dash is tagged cuisine:kebab.
        self.assertEqual(
            normalize_cuisine(["amenity:restaurant", "cuisine:kebab"]),
            ["middle_eastern"],
        )
        self.assertEqual(normalize_cuisine(["cuisine:udon"]), ["japanese"])
        self.assertEqual(normalize_cuisine(["cuisine:donut"]), ["bakery"])
        self.assertEqual(normalize_cuisine(["cuisine:coffee_shop"]), ["cafe"])

    def test_kebab_restaurant_gets_middle_eastern_prior(self) -> None:
        cuisines = normalize_cuisine(["cuisine:kebab"])
        prior = score_restaurant_prior(cuisines=cuisines, region="US")
        # Should reflect Middle Eastern (~0.55), not the 0.30 default.
        self.assertGreater(prior.risk, 0.45)

    def test_detects_region_from_address(self) -> None:
        self.assertEqual(
            region_from_address("10 Wolfe Rd, Cupertino, CA 95014, USA"), "US"
        )
        self.assertEqual(region_from_address("MG Road, Bengaluru, India"), "IN")
        # US state code without explicit country still resolves to US.
        self.assertEqual(region_from_address("5175 Moorpark Ave, San Jose, CA"), "US")
        self.assertEqual(region_from_address(None), "unknown")


class CuisinePriorTests(unittest.TestCase):
    def test_middle_eastern_outranks_american(self) -> None:
        me = score_restaurant_prior(cuisines=["middle_eastern"], region="US")
        american = score_restaurant_prior(cuisines=["american"], region="US")
        self.assertGreater(me.risk, american.risk)

    def test_location_home_region_raises_prior(self) -> None:
        india = score_restaurant_prior(cuisines=["indian"], region="IN")
        usa = score_restaurant_prior(cuisines=["indian"], region="US")
        self.assertGreater(india.risk, usa.risk)
        self.assertIn("home region", " ".join(india.rationale))

    def test_labeling_trust_higher_in_regulated_region(self) -> None:
        usa = score_restaurant_prior(cuisines=["thai"], region="US")
        other = score_restaurant_prior(cuisines=["thai"], region="TH")
        self.assertGreater(usa.labeling_trust, other.labeling_trust)

    def test_no_cuisine_uses_default_with_low_confidence(self) -> None:
        prior = score_restaurant_prior(cuisines=[], region="unknown")
        self.assertEqual(prior.basis, "default")
        self.assertLess(prior.confidence, 0.3)


class DishPriorTests(unittest.TestCase):
    def test_hidden_peanut_dish_flagged_without_allergen_text(self) -> None:
        # The menu line never says "peanut" — the prior must still catch it.
        prior = score_menu_item_prior(
            item_name="Pad Thai",
            description="rice noodles, tamarind, bean sprouts",
            cuisines=["thai"],
            region="US",
        )
        self.assertEqual(prior.basis, "dish_knowledge")
        self.assertGreaterEqual(prior.risk, 0.8)

    def test_pesto_flags_tree_nuts(self) -> None:
        prior = score_menu_item_prior(
            item_name="Basil Pesto Pasta", cuisines=["italian"], region="US"
        )
        self.assertEqual(prior.basis, "dish_knowledge")
        self.assertGreaterEqual(prior.risk, 0.8)

    def test_named_nut_ingredient_is_high_risk(self) -> None:
        prior = score_menu_item_prior(item_name="Walnut Brownie")
        self.assertGreaterEqual(prior.risk, 0.9)

    def test_plain_item_falls_back_to_cuisine_baseline(self) -> None:
        prior = score_menu_item_prior(
            item_name="Cheeseburger", cuisines=["american"], region="US"
        )
        self.assertEqual(prior.basis, "cuisine_baseline")

    def test_nut_free_claim_lowers_prior(self) -> None:
        prior = score_menu_item_prior(
            item_name="Brownie",
            description="made in a nut-free kitchen",
            cuisines=["american"],
        )
        self.assertEqual(prior.basis, "nut_free_claim")
        self.assertLess(prior.risk, 0.2)


class WorldwideRobustnessTests(unittest.TestCase):
    def test_world_cuisines_recognized_not_silently_defaulted(self) -> None:
        for raw in ["cuisine:georgian", "cuisine:moroccan", "cuisine:filipino",
                    "cuisine:peruvian", "cuisine:taiwanese", "cuisine:uzbek"]:
            self.assertTrue(normalize_cuisine([raw]), f"{raw} not recognized")

    def test_walnut_forward_georgian_is_high(self) -> None:
        prior = score_restaurant_prior(cuisines=["georgian"], region="GE")
        self.assertGreater(prior.risk, 0.5)

    def test_world_addresses_resolve(self) -> None:
        cases = {
            "Av. Paulista, São Paulo, Brazil": "BR",
            "Jemaa el-Fnaa, Marrakesh, Morocco": "MA",
            "Rustaveli Avenue, Tbilisi, Georgia": "GE",
            "Orchard Road, Singapore": "SG",
        }
        for address, expected in cases.items():
            self.assertEqual(region_from_address(address), expected)

    def test_multilingual_nut_ingredients_flagged(self) -> None:
        for name in ["Pollo con Almendras", "花生鶏", "Gâteau aux Amandes", "팟타이"]:
            prior = score_menu_item_prior(item_name=name)
            self.assertEqual(prior.basis, "dish_knowledge", f"{name} not flagged")

    def test_coconut_and_nutmeg_not_flagged_as_nuts(self) -> None:
        for name in ["Coconut Curry", "Butternut Squash Soup", "Nutmeg Custard",
                     "Water Chestnut Stir-Fry"]:
            prior = score_menu_item_prior(item_name=name)
            self.assertNotEqual(prior.basis, "dish_knowledge", f"{name} false-positive")


class RestaurantAggregateTests(unittest.TestCase):
    def test_riskiest_dish_drives_restaurant_risk(self) -> None:
        summary = restaurant_nut_risk(
            cuisines=["american"],
            region="US",
            menu_items=[
                {"item_name": "House Salad"},
                {"item_name": "Pecan Pie"},
            ],
        )
        self.assertGreaterEqual(summary.risk, 0.9)
        self.assertEqual(summary.riskiest_items[0][0], "Pecan Pie")

    def test_no_menu_falls_back_to_prior(self) -> None:
        summary = restaurant_nut_risk(cuisines=["thai"], region="US", menu_items=[])
        self.assertGreater(summary.risk, 0.4)
        self.assertIn("cuisine/location prior only", " ".join(summary.rationale))


if __name__ == "__main__":
    unittest.main()
