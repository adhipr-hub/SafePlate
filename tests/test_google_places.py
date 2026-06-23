from __future__ import annotations

import os
import unittest
from unittest import mock

from safeplate.config import get_google_rank_preference
from safeplate.providers.google_places import _google_field_mask, _google_search_body


class GooglePlacesRankPreferenceTests(unittest.TestCase):
    """Regression: searchNearby must rank by DISTANCE. The API default (POPULARITY)
    returns the most prominent places in the radius and omits genuinely-close ones
    (e.g. Olivia Aker Brygge, 12 m away, was missing from a 1800 m search)."""

    def test_body_defaults_to_distance_ranking(self) -> None:
        body = _google_search_body(
            latitude=59.91, longitude=10.73, radius_meters=1800, limit=12,
            included_types=["restaurant"],
        )
        self.assertEqual(body["rankPreference"], "DISTANCE")
        self.assertEqual(body["maxResultCount"], 12)

    def test_config_default_is_distance(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SAFEPLATE_GOOGLE_RANK", None)
            self.assertEqual(get_google_rank_preference(), "DISTANCE")

    def test_config_honors_valid_override_and_ignores_junk(self) -> None:
        with mock.patch.dict(os.environ, {"SAFEPLATE_GOOGLE_RANK": "popularity"}):
            self.assertEqual(get_google_rank_preference(), "POPULARITY")
        with mock.patch.dict(os.environ, {"SAFEPLATE_GOOGLE_RANK": "nonsense"}):
            self.assertEqual(get_google_rank_preference(), "DISTANCE")


class GooglePlacesFieldMaskTests(unittest.TestCase):
    def test_default_field_mask_excludes_atmosphere_fields(self) -> None:
        field_mask = _google_field_mask(include_atmosphere_fields=False)

        self.assertIn("places.websiteUri", field_mask)
        self.assertIn("places.currentOpeningHours", field_mask)
        self.assertNotIn("places.takeout", field_mask)
        self.assertNotIn("places.servesVegetarianFood", field_mask)

    def test_can_opt_into_atmosphere_fields(self) -> None:
        field_mask = _google_field_mask(include_atmosphere_fields=True)

        self.assertIn("places.takeout", field_mask)
        self.assertIn("places.servesVegetarianFood", field_mask)


if __name__ == "__main__":
    unittest.main()
