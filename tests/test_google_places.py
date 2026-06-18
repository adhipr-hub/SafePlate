from __future__ import annotations

import unittest

from safeplate.providers.google_places import _google_field_mask


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
