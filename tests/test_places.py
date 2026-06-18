from __future__ import annotations

import unittest

from safeplate.places import is_food_place


class FoodPlaceFilterTests(unittest.TestCase):
    def test_real_restaurants_pass(self) -> None:
        self.assertTrue(is_food_place("primary_type:restaurant; restaurant; food"))
        self.assertTrue(is_food_place(["italian_restaurant", "restaurant", "food"]))
        self.assertTrue(is_food_place("primary_type:cafe; cafe"))
        self.assertTrue(is_food_place("amenity:restaurant; cuisine:thai"))

    def test_non_food_pois_rejected(self) -> None:
        self.assertFalse(is_food_place("primary_type:shopping_mall; restaurant; food"))
        self.assertFalse(is_food_place("primary_type:movie_theater; restaurant"))
        self.assertFalse(is_food_place("primary_type:hotel; banquet_hall; restaurant"))
        self.assertFalse(is_food_place("primary_type:department_store; cosmetics_store"))
        self.assertFalse(is_food_place("primary_type:museum; tourist_attraction"))

    def test_empty_is_not_food(self) -> None:
        self.assertFalse(is_food_place(""))
        self.assertFalse(is_food_place([]))


if __name__ == "__main__":
    unittest.main()
