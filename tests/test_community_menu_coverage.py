"""Regression: review-scraped dishes (the community fallback used when NO real menu
was found) must never be presented as reviewed menu coverage.

Bug this locks: an Indian food truck ("Mr. Bombay") whose real menu failed to extract
had a single review-mentioned dish ("Fried Rice") fed in as the menu. Because the
scorer counted it toward `parsed_count`, the assessment came back with evidence_basis
"menu_coverage" and a coverage discount -> the UI showed "Menu reviewed / we read the
menu and found no nut dishes" over a menu it never actually read, and *lowered* the
risk on the strength of one review word. For a safety-asymmetric product that is the
wrong failure direction: fabricated coverage that under-warns.

The fix: community items (extraction_method "community_mention" / source_type
"community") are excluded from menu coverage in BOTH the scorer (`parsed_count`) and
`_coverage_status`. They still feed the dish-name prior, so a menu-less place still
beats a bare cuisine guess -- it just can't claim "menu reviewed".
"""

from __future__ import annotations

import unittest

from safeplate.allergen_score import (
    RestaurantSignals,
    Severity,
    UserProfile,
    score_restaurant_for_user,
)
from safeplate.menu_service import (
    _coverage_status,
    _structured_menu_response,
    _write_assessment_into_card,
)
from safeplate.menu_text import MenuItemRecord

NUT_ALLERGY = UserProfile.for_nuts(Severity.ALLERGY)


def _community_item(name: str) -> dict:
    """A dish scraped from reviews (the community fallback), tagged exactly as
    community_signals.fetch_community_signals emits it."""
    return {
        "item_name": name,
        "description": "",
        "allergen_terms": [],
        "extraction_method": "community_mention",
        "source_type": "community",
        "menu_source_url": "(mentioned in reviews)",
    }


def _real_item(name: str) -> dict:
    """A dish parsed from the restaurant's actual menu."""
    return {
        "item_name": name,
        "description": "",
        "allergen_terms": [],
        "extraction_method": "gemini_text",
        "source_type": "website_link",
        "menu_source_url": "http://example.test/menu",
    }


def _record(name: str, *, source_type: str, extraction_method: str) -> MenuItemRecord:
    return MenuItemRecord(
        restaurant_name="Mr. Bombay",
        restaurant_source_id="",
        menu_source_url="(mentioned in reviews)"
        if source_type == "community"
        else "http://example.test/menu",
        category="",
        item_name=name,
        description="",
        price="",
        dietary_terms=[],
        allergen_terms=[],
        source_type=source_type,
        extraction_method=extraction_method,
        confidence=0.3,
        raw_text=name,
        fetched_at="",
    )


def _community_record(name: str) -> MenuItemRecord:
    return _record(name, source_type="community", extraction_method="community_mention")


def _real_record(name: str) -> MenuItemRecord:
    return _record(name, source_type="website_link", extraction_method="gemini_text")


class CommunityIsNotMenuCoverageTests(unittest.TestCase):
    def test_community_only_dish_never_claims_menu_coverage(self) -> None:
        # The exact failing case: one benign review-scraped dish at a truck whose
        # cuisine we couldn't infer. It must NOT read as a reviewed menu.
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=[],
            region="US",
            menu_items=[_community_item("Fried Rice")],
            signals=RestaurantSignals(),
        )
        self.assertNotEqual(
            result.evidence_basis,
            "menu_coverage",
            "review-scraped dishes must not earn the 'menu reviewed' basis",
        )
        self.assertEqual(result.evidence_basis, "cuisine_prior")

    def test_community_only_coverage_status_is_not_menu_backed(self) -> None:
        self.assertEqual(
            _coverage_status([], [], [_community_item("Fried Rice")]),
            "cuisine_estimate",
        )

    def test_nut_risky_community_dish_still_raises_via_dish_prior(self) -> None:
        # The community signal must still WORK: a nut-named review dish raises risk
        # through the dish-name prior ("Inferred"), never through fake menu coverage.
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=["indian"],
            region="US",
            menu_items=[_community_item("Cashew Korma")],
            signals=RestaurantSignals(),
        )
        self.assertEqual(result.evidence_basis, "dish_prior")
        self.assertNotEqual(result.evidence_basis, "menu_coverage")

    def test_real_clean_menu_still_earns_menu_coverage(self) -> None:
        # Guard against over-correction: a genuinely extracted clean menu must STILL
        # get the coverage basis (unchanged behavior for real menus).
        result = score_restaurant_for_user(
            NUT_ALLERGY,
            cuisines=[],
            region="US",
            menu_items=[_real_item("Sev Puri")],
            signals=RestaurantSignals(),
        )
        self.assertEqual(result.evidence_basis, "menu_coverage")
        self.assertEqual(
            _coverage_status([], [], [_real_item("Sev Puri")]), "menu_backed"
        )

    def test_real_item_alongside_community_still_menu_backed(self) -> None:
        # A mix (shouldn't happen today, but be robust): any real extracted item makes
        # it menu-backed; community items alone never do.
        items = [_real_item("Sev Puri"), _community_item("Fried Rice")]
        self.assertEqual(_coverage_status([], [], items), "menu_backed")


class StructuredResponseCoverageTests(unittest.TestCase):
    """The UI-facing builders. The drawer (`/api/menu` via `_structured_menu_response`)
    and the search card (`_write_assessment_into_card`) read `coverageStatus` /
    `isMenuBacked` for the "Menu reviewed" trust badge -- these must apply the same
    community exclusion as `_coverage_status`, or the badge over-claims."""

    def _assessment(self, items):
        return score_restaurant_for_user(
            NUT_ALLERGY, cuisines=[], region="US", menu_items=items,
            signals=RestaurantSignals(),
        )

    def test_drawer_response_community_only_is_not_menu_backed(self) -> None:
        items = [_community_record("Fried Rice")]
        resp = _structured_menu_response(
            restaurant_name="Mr. Bombay", website_url="", address="",
            assessment=self._assessment(items), menu_items=items,
            allergy_signals=[], coverage=[], errors=[],
        )
        self.assertEqual(resp["coverageStatus"], "cuisine_estimate")
        self.assertEqual(resp["summary"]["coverageStatus"], "cuisine_estimate")
        self.assertFalse(resp["summary"]["menuBackedRisk"]["isMenuBacked"])

    def test_drawer_response_real_menu_stays_menu_backed(self) -> None:
        items = [_real_record("Sev Puri")]
        resp = _structured_menu_response(
            restaurant_name="Mr. Bombay", website_url="", address="",
            assessment=self._assessment(items), menu_items=items,
            allergy_signals=[], coverage=[], errors=[],
        )
        self.assertEqual(resp["coverageStatus"], "menu_backed")
        self.assertEqual(resp["summary"]["coverageStatus"], "menu_backed")
        self.assertTrue(resp["summary"]["menuBackedRisk"]["isMenuBacked"])

    def test_card_write_community_only_is_not_menu_backed(self) -> None:
        from safeplate.allergen_prior import score_restaurant_prior

        items = [_community_record("Fried Rice")]
        payload: dict = {}
        _write_assessment_into_card(
            payload, self._assessment(items),
            prior=score_restaurant_prior(cuisines=[], region="US", allergen="nuts"),
            cuisines=[], region="US", name="Mr. Bombay", website_url="",
            menu_items=items, allergy_signals=[], coverage=[], errors=[],
        )
        self.assertEqual(payload["coverageStatus"], "cuisine_estimate")
        # Cuisine-estimate cards embed no menuDetail (nothing real to show; the
        # drawer fetches fresh on open) -- community-only must take that branch.
        self.assertNotIn("menuDetail", payload)

    def test_card_write_real_menu_stays_menu_backed(self) -> None:
        from safeplate.allergen_prior import score_restaurant_prior

        items = [_real_record("Sev Puri")]
        payload: dict = {}
        _write_assessment_into_card(
            payload, self._assessment(items),
            prior=score_restaurant_prior(cuisines=[], region="US", allergen="nuts"),
            cuisines=[], region="US", name="Mr. Bombay", website_url="",
            menu_items=items, allergy_signals=[], coverage=[], errors=[],
        )
        self.assertEqual(payload["coverageStatus"], "menu_backed")
        self.assertIn("menuDetail", payload)


if __name__ == "__main__":
    unittest.main()
