from __future__ import annotations

import unittest

from safeplate.menu_text import (
    MenuItemRecord,
    _dedupe_item_key,
    _looks_like_category,
    _price_count,
)


class DedupeKeyTests(unittest.TestCase):
    def test_same_item_on_different_pages_dedupes(self) -> None:
        item = MenuItemRecord(
            restaurant_name="Dish n' Dash",
            restaurant_source_id="place-1",
            menu_source_url="",
            category="",
            item_name="Chicken Shawarma",
            description="",
            price="$20.25",
            dietary_terms="",
            allergen_terms="",
            source_type="website_link",
            extraction_method="html_visible_text",
            confidence=0.8,
            raw_text="",
            fetched_at="",
        )
        row_menu = {"restaurant_source_id": "place-1", "restaurant_name": "Dish n' Dash", "candidate_url": "https://x.com/menu"}
        row_lunch = {"restaurant_source_id": "place-1", "restaurant_name": "Dish n' Dash", "candidate_url": "https://x.com/menu#lunch"}
        # Same dish/price across two crawled URLs -> identical dedupe key now.
        self.assertEqual(_dedupe_item_key(row_menu, item), _dedupe_item_key(row_lunch, item))

    def test_different_restaurants_do_not_collide(self) -> None:
        item = MenuItemRecord(
            restaurant_name="A", restaurant_source_id="", menu_source_url="",
            category="", item_name="Falafel", description="", price="$10",
            dietary_terms="", allergen_terms="", source_type="website_link",
            extraction_method="html_visible_text", confidence=0.8, raw_text="", fetched_at="",
        )
        row_a = {"restaurant_source_id": "", "restaurant_name": "A", "candidate_url": "https://a.com"}
        row_b = {"restaurant_source_id": "", "restaurant_name": "B", "candidate_url": "https://b.com"}
        self.assertNotEqual(_dedupe_item_key(row_a, item), _dedupe_item_key(row_b, item))


class MenuItemExtractionTests(unittest.TestCase):
    def test_counts_bare_prices_for_ocr_reporting(self) -> None:
        text = "Vegan Nachos 17\nSeoul Night lager abv 23%"

        self.assertEqual(_price_count(text, allow_bare_prices=True), 1)
        self.assertEqual(_price_count(text, allow_bare_prices=False), 0)

    def test_does_not_treat_item_with_category_word_as_category(self) -> None:
        self.assertTrue(_looks_like_category("Beer"))
        self.assertTrue(_looks_like_category("Juice + Lemonade + Soda"))
        self.assertFalse(_looks_like_category("Root Beer Float"))


class MultiCurrencyTests(unittest.TestCase):
    def test_name_gate_rejects_fragments_and_prose(self) -> None:
        from safeplate.menu_text import _looks_like_item_name
        self.assertFalse(_looks_like_item_name("sautéed with"))   # lowercase fragment
        self.assertFalse(_looks_like_item_name("Served with garlic and"))  # trailing connector
        self.assertFalse(_looks_like_item_name("私たちは、厚生労働省"))  # CJK prose
        self.assertTrue(_looks_like_item_name("Chicken Shawarma"))
        self.assertTrue(_looks_like_item_name("Pad Thai"))


class AllergenTermMatchingTests(unittest.TestCase):
    def test_false_friends_do_not_trigger_allergens(self) -> None:
        from safeplate.menu_text import _matched_terms, ALLERGEN_TERMS

        self.assertEqual(_matched_terms("Grilled eggplant parmesan", ALLERGEN_TERMS), [])
        self.assertEqual(_matched_terms("Veggie burger with fries", ALLERGEN_TERMS), [])
        self.assertEqual(_matched_terms("Toasted coconut sorbet", ALLERGEN_TERMS), [])
        self.assertEqual(_matched_terms("Glazed doughnuts", ALLERGEN_TERMS), [])
        self.assertEqual(_matched_terms("Buckwheat soba noodles", ALLERGEN_TERMS), [])

    def test_real_allergens_still_match_compounds_and_plurals(self) -> None:
        from safeplate.menu_text import _matched_terms, ALLERGEN_TERMS

        # Plurals and single-word compounds a naive \b boundary would have dropped.
        self.assertIn("egg", _matched_terms("Scrambled eggs and toast", ALLERGEN_TERMS))
        self.assertIn("egg", _matched_terms("Classic eggnog", ALLERGEN_TERMS))
        self.assertIn("milk", _matched_terms("Vanilla milkshake", ALLERGEN_TERMS))
        self.assertIn("walnut", _matched_terms("Candied walnuts", ALLERGEN_TERMS))
        self.assertIn("nuts", _matched_terms("Bowl of mixed nuts", ALLERGEN_TERMS))
        # Newly added common tree nuts (singular forms previously uncaught).
        self.assertIn("hazelnut", _matched_terms("Hazelnut gelato", ALLERGEN_TERMS))
        self.assertIn("pistachio", _matched_terms("Pistachio baklava", ALLERGEN_TERMS))

    def test_mixed_line_with_false_friend_and_real_allergen(self) -> None:
        from safeplate.menu_text import _matched_terms, ALLERGEN_TERMS

        # Eggplant should not add "egg", but the real cashew should still register.
        terms = _matched_terms("Eggplant curry with cashew sauce", ALLERGEN_TERMS)
        self.assertIn("cashew", terms)
        self.assertNotIn("egg", terms)


if __name__ == "__main__":
    unittest.main()
