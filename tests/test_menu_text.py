from __future__ import annotations

import unittest

from safeplate.menu_text import (
    MenuItemRecord,
    _dedupe_item_key,
    _extract_menu_items_from_html,
    _extract_schema_org_menu_items_from_html,
    _extract_menu_items_from_text,
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
    def test_extracts_price_linked_menu_item(self) -> None:
        html = """
        <html>
          <body>
            <h2>Entrees</h2>
            <p>Falafel Plate - Chickpea fritters with hummus and salad $14.95</p>
          </body>
        </html>
        """

        rows = _extract_menu_items_from_html(html)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].category, "Entrees")
        self.assertEqual(rows[0].item_name, "Falafel Plate")
        self.assertEqual(rows[0].price, "$14.95")
        self.assertIn("Chickpea fritters", rows[0].description)
        self.assertEqual(rows[0].extraction_method, "html_visible_text")

    def test_extracts_ocr_style_bare_price_menu_items(self) -> None:
        text = """
        Shared Plates
        Korean Fried Chicken Wings tossed in garlic gochujang glaze 17
        Vegan Nachos cashew nacho cheese and salsa macha 17
        """

        rows = _extract_menu_items_from_text(text)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].category, "Shared Plates")
        self.assertEqual(rows[0].item_name, "Korean Fried Chicken Wings")
        self.assertEqual(rows[0].price, "17")
        self.assertEqual(rows[1].item_name, "Vegan Nachos")
        self.assertEqual(rows[1].price, "17")
        self.assertIn("vegan", rows[1].dietary_terms)

    def test_ignores_bare_non_price_numbers(self) -> None:
        text = "Drinks\nSeoul Night lager abv 23% crisp rice finish"

        rows = _extract_menu_items_from_text(text)

        self.assertEqual(rows, [])

    def test_ignores_corporate_report_numbers_as_bare_prices(self) -> None:
        text = """
        This statement is made 54 pursuant to the requirements of Section
        Kingdom (UK) Modern Slavery Act 9 2015; Section
        More than 20 years ago, in partnership with Conservation International
        During FY25, Starbucks 10 operated
        SEC, including our most 10 recently filed periodic reports on Form 10-K
        Retail Industry Leaders Association (RILA) 20 For more than
        Coffee Canada, Inc. 11 pursuant to Section
        """

        rows = _extract_menu_items_from_text(text)

        self.assertEqual(rows, [])

    def test_ignores_cart_total_ui_text(self) -> None:
        html = "<html><body><div>Your cart (0) total $0.00</div></body></html>"

        rows = _extract_menu_items_from_html(html)

        self.assertEqual(rows, [])

    def test_counts_bare_prices_for_ocr_reporting(self) -> None:
        text = "Vegan Nachos 17\nSeoul Night lager abv 23%"

        self.assertEqual(_price_count(text, allow_bare_prices=True), 1)
        self.assertEqual(_price_count(text, allow_bare_prices=False), 0)

    def test_does_not_treat_item_with_category_word_as_category(self) -> None:
        self.assertTrue(_looks_like_category("Beer"))
        self.assertTrue(_looks_like_category("Juice + Lemonade + Soda"))
        self.assertFalse(_looks_like_category("Root Beer Float"))

    def test_html_fallback_blocks_do_not_inherit_stale_category(self) -> None:
        html = """
        <html>
          <body>
            <h2>Homemade Desserts</h2>
            <p>Root Beer Float</p>
            <div>
              <span>Fresh Squeezed Orange Juice</span>
              <span>$ 8</span>
            </div>
          </body>
        </html>
        """

        rows = _extract_menu_items_from_html(html)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].item_name, "Fresh Squeezed Orange Juice")
        self.assertEqual(rows[0].category, "")

    def test_extracts_schema_org_menu_items_from_json_ld(self) -> None:
        html = """
        <html>
          <head>
            <script type="application/ld+json">
            {
              "@context": "https://schema.org",
              "@type": "Menu",
              "@id": "https://example.com/menu#menu",
              "name": "Dinner",
              "hasMenuSection": [
                {
                  "@type": "MenuSection",
                  "name": "Entrees",
                  "hasMenuItem": [
                    {
                      "@type": "MenuItem",
                      "name": "Vegan Bowl",
                      "description": "tofu, greens, sesame dressing",
                      "offers": {"@type": "Offer", "price": "14.00"}
                    }
                  ]
                }
              ]
            }
            </script>
          </head>
        </html>
        """

        rows = _extract_schema_org_menu_items_from_html(html)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].category, "Entrees")
        self.assertEqual(rows[0].item_name, "Vegan Bowl")
        self.assertEqual(rows[0].description, "tofu, greens, sesame dressing")
        self.assertEqual(rows[0].price, "14")
        self.assertEqual(rows[0].extraction_method, "schema_org_menu_item")
        self.assertIn("vegan", rows[0].dietary_terms)
        self.assertIn("sesame", rows[0].allergen_terms)

    def test_extracts_schema_org_menu_item_price_from_menu_add_on(self) -> None:
        html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Menu",
          "name": "Drinks",
          "hasMenuSection": {
            "@type": "MenuSection",
            "name": "Wine",
            "hasMenuItem": {
              "@type": "MenuItem",
              "name": "Prosecco",
              "description": "Franco Amoroso; Italy",
              "menuAddOn": {
                "@type": "MenuSection",
                "hasMenuItem": {
                  "@type": "MenuItem",
                  "name": "$12/$45"
                }
              }
            }
          }
        }
        </script>
        """

        rows = _extract_schema_org_menu_items_from_html(html)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].category, "Wine")
        self.assertEqual(rows[0].item_name, "Prosecco")
        self.assertEqual(rows[0].price, "$12/$45")
        self.assertIn("$12/$45", rows[0].raw_text)

class MultiCurrencyTests(unittest.TestCase):
    def test_extracts_world_currencies(self) -> None:
        text = (
            "Pad Thai ฿120\nMargherita Pizza €8,50\nTruffle Pasta 14,00€\n"
            "Ramen ¥1,200\nPaneer Tikka ₹350\nFish and Chips £12.50\nTacos USD 9\n"
        )
        by_name = {r.item_name: r.price for r in _extract_menu_items_from_text(text)}
        self.assertEqual(by_name.get("Pad Thai"), "฿120")
        self.assertEqual(by_name.get("Margherita Pizza"), "€8,50")
        self.assertEqual(by_name.get("Truffle Pasta"), "14,00€")
        self.assertEqual(by_name.get("Ramen"), "¥1,200")
        self.assertEqual(by_name.get("Paneer Tikka"), "₹350")
        self.assertEqual(by_name.get("Fish and Chips"), "£12.50")

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
