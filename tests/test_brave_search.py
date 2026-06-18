from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from safeplate.brave_search import (
    BraveSearchResult,
    _evaluate_website_candidate,
    _menu_source_record_from_result,
    _search_results_from_payload,
    _website_recovery_queries,
)
from safeplate.page_fetch import HtmlPage
from safeplate.schemas import RestaurantRecord


class BraveSearchTests(unittest.TestCase):
    def test_search_results_are_normalized_from_payload(self) -> None:
        payload = {
            "web": {
                "results": [
                    {
                        "title": "Example Menu",
                        "url": "https://example.com/menu",
                        "description": "Dinner menu",
                        "extra_snippets": ["Lunch menu"],
                    },
                    {"title": "Bad", "url": "mailto:bad@example.com"},
                ]
            }
        }

        rows = _search_results_from_payload(payload)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].url, "https://example.com/menu")
        self.assertEqual(rows[0].extra_snippets, ["Lunch menu"])

    def test_website_candidate_requires_restaurant_and_location_match(self) -> None:
        row = _restaurant_row()
        result = BraveSearchResult(
            title="Jenny's Kitchen Official Website",
            url="https://jennyskitchensanjose.com/",
            description="Chinese restaurant at 5175 Moorpark Ave in San Jose.",
            extra_snippets=["Call (408) 996-1199 for takeout."],
            raw_payload={},
        )

        with patch(
            "safeplate.brave_search.fetch_html_page",
            return_value=HtmlPage(
                requested_url=result.url,
                final_url=result.url,
                html="<html><body>Jenny's Kitchen 5175 Moorpark Ave San Jose (408) 996-1199</body></html>",
                fetch_method="static_html",
            ),
        ):
            candidate = _evaluate_website_candidate(
                row=row,
                result=result,
                query='"Jenny\'s Kitchen" "5175 Moorpark Ave"',
                user_agent="SafePlate test",
            )

        self.assertTrue(candidate["accepted"])
        self.assertGreaterEqual(candidate["confidence"], 0.62)
        self.assertIn("address match", candidate["reason"])

    def test_website_candidate_rejects_known_listing_hosts(self) -> None:
        row = _restaurant_row()
        result = BraveSearchResult(
            title="Jenny's Kitchen - Yelp",
            url="https://www.yelp.com/biz/jennys-kitchen-san-jose",
            description="5175 Moorpark Ave",
            extra_snippets=["(408) 996-1199"],
            raw_payload={},
        )

        candidate = _evaluate_website_candidate(
            row=row,
            result=result,
            query='"Jenny\'s Kitchen" "5175 Moorpark Ave"',
            user_agent="SafePlate test",
        )

        self.assertFalse(candidate["accepted"])
        self.assertIn("third-party", candidate["rejection_reason"])

    def test_website_candidate_rejects_directory_listing_with_matching_address(self) -> None:
        row = _restaurant_row()
        result = BraveSearchResult(
            title="Jenny's Kitchen - Santa Clara County Restaurants",
            url="https://www.city-data.com/santa-clara-county-restaurants/jenny-s-kitchen.html",
            description="Jenny's Kitchen, 5175 Moorpark Ave, San Jose, CA",
            extra_snippets=["Phone (408) 996-1199"],
            raw_payload={},
        )

        candidate = _evaluate_website_candidate(
            row=row,
            result=result,
            query='"Jenny\'s Kitchen" "5175 Moorpark Ave"',
            user_agent="SafePlate test",
        )

        self.assertFalse(candidate["accepted"])
        self.assertIn("third-party", candidate["rejection_reason"])

    def test_website_candidate_rejects_name_subdomain_on_wrapper_host(self) -> None:
        row = _restaurant_row()
        result = BraveSearchResult(
            title="Jenny's Kitchen",
            url="https://jennys-kitchen.wheree.com/",
            description="Jenny's Kitchen at 5175 Moorpark Ave",
            extra_snippets=["(408) 996-1199"],
            raw_payload={},
        )

        candidate = _evaluate_website_candidate(
            row=row,
            result=result,
            query='"Jenny\'s Kitchen" "5175 Moorpark Ave"',
            user_agent="SafePlate test",
        )

        self.assertFalse(candidate["accepted"])
        self.assertIn("third-party", candidate["rejection_reason"])

    def test_menu_source_record_preserves_brave_provenance(self) -> None:
        result = BraveSearchResult(
            title="Grand Dynasty Seafood Menu",
            url="https://granddynastyseafood.com/index.php/welcome/menu",
            description="Menu page",
            extra_snippets=[],
            raw_payload={},
        )

        record = _menu_source_record_from_result(
            result=result,
            query='"Grand Dynasty Seafood" menu',
            website_url="https://granddynastyseafood.com/",
            restaurant_name="Grand Dynasty Seafood",
            restaurant_source_id="place-id",
            address="10123 N Wolfe Rd, Cupertino, CA",
            fetched_at="2026-06-14T00:00:00+00:00",
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.source_type, "website_link")
        self.assertIn("Brave Search candidate", record.reason)
        self.assertEqual(record.raw_payload["discovered_by"], "brave_search")

    def test_allergen_pdf_search_keeps_allergen_pdf_rejects_listing(self) -> None:
        from safeplate.brave_search import discover_allergen_pdfs_with_brave

        results = [
            BraveSearchResult(
                title="Allergen Guide",
                url="https://cdn.example.com/docs/allergen-guide-2025.pdf",
                description="dish allergen matrix",
                extra_snippets=[],
                raw_payload={},
            ),
            BraveSearchResult(
                title="Example - Yelp",
                url="https://www.yelp.com/biz/example",
                description="reviews",
                extra_snippets=[],
                raw_payload={},
            ),
        ]
        with patch("safeplate.brave_search.brave_web_search", return_value=results):
            recs = discover_allergen_pdfs_with_brave(
                restaurant_name="Example", restaurant_source_id="id",
                website_url="https://example.com/", address="123 Main St, Springfield, IL",
                api_key="k", user_agent="ua",
            )
        urls = [r.candidate_url for r in recs]
        self.assertIn("https://cdn.example.com/docs/allergen-guide-2025.pdf", urls)
        self.assertNotIn("https://www.yelp.com/biz/example", urls)
        self.assertEqual(recs[0].source_type, "pdf")

    def test_website_queries_include_broader_cuisine_city_fallback(self) -> None:
        row = replace(
            _restaurant_row(),
            categories=["amenity:restaurant", "cuisine:chinese"],
        )

        queries = [query.lower() for query in _website_recovery_queries(row)]

        self.assertIn("jenny chinese restaurant san jose website", queries)


def _restaurant_row() -> RestaurantRecord:
    return RestaurantRecord(
        name="Jenny's Kitchen",
        address="5175 Moorpark Ave #5, San Jose, CA 95129, USA",
        latitude=37.0,
        longitude=-122.0,
        distance_meters=100.0,
        rating=4.1,
        review_count=406,
        price_level=None,
        categories=["restaurant"],
        website_url=None,
        phone_number="(408) 996-1199",
        opening_hours=None,
        business_status="OPERATIONAL",
        is_open_now=None,
        service_options={},
        source_last_updated=None,
        data_quality_score=0.0,
        source_name="google_places",
        source_id="place-id",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        raw_payload={},
    )


if __name__ == "__main__":
    unittest.main()
