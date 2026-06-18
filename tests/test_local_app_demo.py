from __future__ import annotations

import unittest
from unittest.mock import patch

from safeplate.local_app import restaurant_signals_from_evidence
from safeplate.local_app import run_menu_extraction
from safeplate.local_app import run_restaurant_search
from safeplate.menu_text import MenuTextRecord


class LocalAppDemoTests(unittest.TestCase):
    def test_demo_search_uses_fixtures_not_live_provider(self) -> None:
        with patch(
            "safeplate.local_app._fetch_rows_for_provider",
            side_effect=AssertionError("live provider should not be called"),
        ):
            response = run_restaurant_search({"location": "ignored"}, demo_mode=True)

        self.assertTrue(response["demoMode"])
        self.assertEqual(response["provider"], "demo")
        self.assertEqual(len(response["rows"]), 3)
        self.assertEqual(response["rows"][0]["source_id"], "demo-thai-kitchen")

    def test_demo_menu_returns_menu_backed_risk_and_signals(self) -> None:
        response = run_menu_extraction(
            {
                "name": "Demo Thai Kitchen",
                "sourceId": "demo-thai-kitchen",
                "address": "101 Demo Ave, Cupertino, CA 95014",
                "categories": ["thai_restaurant", "restaurant"],
            },
            demo_mode=True,
        )

        summary = response["summary"]
        self.assertEqual(summary["coverageStatus"], "menu_backed")
        self.assertTrue(summary["menuBackedRisk"]["isMenuBacked"])
        self.assertGreaterEqual(summary["menuBackedRisk"]["risk"], 0.8)
        self.assertTrue(summary["restaurantSignals"]["mentions_staff_allergy_instruction"])

    def test_demo_menu_without_items_is_no_menu_found(self) -> None:
        response = run_menu_extraction(
            {
                "name": "Demo Garden Bistro",
                "sourceId": "demo-garden-bistro",
                "address": "202 Orchard St, Cupertino, CA 95014",
                "categories": ["american_restaurant"],
            },
            demo_mode=True,
        )

        summary = response["summary"]
        self.assertEqual(summary["coverageStatus"], "no_menu_found")
        self.assertFalse(summary["menuBackedRisk"]["isMenuBacked"])
        self.assertEqual(response["menuItems"], [])

    def test_restaurant_signals_detect_policy_language(self) -> None:
        text = MenuTextRecord(
            restaurant_name="Example",
            restaurant_source_id="example",
            menu_source_url="https://example.test/menu",
            source_type="website_link",
            extraction_method="test",
            char_count=100,
            price_count=0,
            dietary_terms=[],
            allergen_terms=[],
            fetched_at="2026-06-16T00:00:00+00:00",
            extracted_text=(
                "Food allergy notice: please inform your server. "
                "Shared fryers mean cross-contact may occur. "
                "The vinaigrette is nut-free."
            ),
        )

        signals = restaurant_signals_from_evidence([text], [])

        self.assertTrue(signals["has_allergy_disclaimer"])
        self.assertTrue(signals["has_cross_contact_warning"])
        self.assertTrue(signals["mentions_staff_allergy_instruction"])
        self.assertTrue(signals["has_nut_free_claim"])


if __name__ == "__main__":
    unittest.main()
