from __future__ import annotations

import unittest

from safeplate.demo_fixtures import DemoFixtureError
from safeplate.demo_fixtures import load_demo_menu
from safeplate.demo_fixtures import load_demo_search


class DemoFixtureTests(unittest.TestCase):
    def test_loads_search_fixture_restaurants(self) -> None:
        fixture = load_demo_search()

        self.assertEqual(fixture.default_location, "SafePlate Demo")
        self.assertEqual(len(fixture.restaurants), 3)
        self.assertEqual(fixture.restaurants[0].source_id, "demo-thai-kitchen")

    def test_loads_menu_fixture_records(self) -> None:
        fixture = load_demo_menu("demo-thai-kitchen")

        self.assertEqual(fixture.scenario, "menu_backed_nut_risk")
        self.assertEqual(len(fixture.menu_sources), 1)
        self.assertEqual(fixture.menu_items[0].item_name, "Pad Thai")

    def test_missing_menu_fixture_raises_clear_error(self) -> None:
        with self.assertRaises(DemoFixtureError):
            load_demo_menu("missing-demo")


if __name__ == "__main__":
    unittest.main()
