from __future__ import annotations

import unittest
from unittest.mock import patch

from safeplate.menu_sources import _records_from_schema_org
from safeplate.menu_sources import (
    MenuSourceError,
    _extract_links_and_images,
    _has_allergen_candidate,
    _record_from_link,
    _record_from_image,
    _score_image_menu_page,
    _seek_allergen_pages,
    _seek_allergen_pdfs,
)
from safeplate.schemas import MenuSourceRecord


class AllergenSeekerTests(unittest.TestCase):
    def test_has_allergen_candidate(self) -> None:
        rec = _record_from_link(
            href="https://x.com/allergens", text="Allergens",
            base_url="https://x.com/", website_url="https://x.com/",
            restaurant_name="X", restaurant_source_id="id",
            fetched_at="2026-06-16T00:00:00+00:00", location_hint=None,
        )
        self.assertEqual(rec.source_type, "nutrition_or_allergen_page")
        self.assertTrue(_has_allergen_candidate([rec]))
        self.assertFalse(_has_allergen_candidate([None]))

    def test_seeker_probes_paths_and_keeps_only_allergen_pages(self) -> None:
        def fake_fetch(url, *, user_agent, fetch_mode="static"):
            if url.endswith("/allergens"):
                return "<html><body>Our allergen information table</body></html>"
            if url.endswith("/nutrition"):
                return "<html><body>Welcome to our homepage</body></html>"  # soft-404
            raise MenuSourceError("404")

        with patch("safeplate.menu_sources._fetch_text", side_effect=fake_fetch):
            found = _seek_allergen_pages(
                "https://x.com/", user_agent="ua", fetch_mode="static", max_workers=4
            )
        urls = [u for u, _ in found]
        self.assertIn("https://x.com/allergens", urls)
        self.assertNotIn("https://x.com/nutrition", urls)  # no allergen keyword

    def test_pdf_seeker_keeps_only_real_pdfs(self) -> None:
        def fake_bytes(url, user_agent):
            if url.endswith("/allergens.pdf"):
                return (b"%PDF-1.4 allergen matrix", "application/pdf")
            if url.endswith("/nutrition.pdf"):
                return (b"<html>404</html>", "text/html")  # soft-404, not a PDF
            raise MenuSourceError("404")

        with patch("safeplate.menu_sources._fetch_bytes", side_effect=fake_bytes):
            found = _seek_allergen_pdfs("https://x.com/", user_agent="ua", max_workers=4)
        self.assertIn("https://x.com/allergens.pdf", found)
        self.assertNotIn("https://x.com/nutrition.pdf", found)

    def test_linked_allergen_pdf_is_recognized_as_allergen_source(self) -> None:
        rec = _record_from_link(
            href="https://x.com/docs/allergen-guide.pdf", text="Allergen Guide",
            base_url="https://x.com/", website_url="https://x.com/",
            restaurant_name="X", restaurant_source_id="id",
            fetched_at="2026-06-16T00:00:00+00:00", location_hint=None,
        )
        self.assertEqual(rec.source_type, "pdf")
        self.assertTrue(_has_allergen_candidate([rec]))


class SchemaOrgMenuSourceTests(unittest.TestCase):
    def test_extracts_has_menu_url_from_json_ld(self) -> None:
        html = """
        <html>
          <head>
            <script type="application/ld+json">
              {
                "@context": "https://schema.org",
                "@type": "Restaurant",
                "name": "Example Cafe",
                "hasMenu": "/menu"
              }
            </script>
          </head>
        </html>
        """

        rows = _records_from_schema_org(
            html=html,
            page_url="https://example.com/",
            website_url="https://example.com/",
            restaurant_name="Example Cafe",
            restaurant_source_id="example-id",
            fetched_at="2026-06-09T00:00:00+00:00",
            location_hint=None,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].candidate_url, "https://example.com/menu")
        self.assertEqual(rows[0].source_type, "schema_org_menu")
        self.assertTrue(rows[0].is_primary_menu_candidate)
        self.assertIn("Schema.org menu URL", rows[0].reason)

    def test_image_context_promotes_generic_menu_images(self) -> None:
        html = """
        <html>
          <body>
            <div class="menu">
              <img src="/images/a1.jpg" alt="">
            </div>
          </body>
        </html>
        """

        _links, images = _extract_links_and_images(html)
        row = _record_from_image(
            src=images[0][0],
            alt=images[0][1],
            base_url="https://example.com/menu",
            website_url="https://example.com/",
            restaurant_name="Example Cafe",
            restaurant_source_id="example-id",
            fetched_at="2026-06-09T00:00:00+00:00",
            location_hint=None,
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.candidate_url, "https://example.com/images/a1.jpg")
        self.assertEqual(row.source_type, "image")
        self.assertTrue(row.is_primary_menu_candidate)

    def test_image_heavy_menu_page_can_validate_without_price_text(self) -> None:
        html = """
        <html>
          <body>
            <a href="#Wedding Banquet Menu">Wedding Banquet Menu</a>
            <a href="#Menu">Menu</a>
            <div class="menu">
              <img src="/images/a1.jpg" alt="">
              <img src="/images/a2.jpg" alt="">
              <img src="/images/a3.jpg" alt="">
            </div>
          </body>
        </html>
        """
        record = MenuSourceRecord(
            restaurant_name="Example Cafe",
            restaurant_source_id="example-id",
            website_url="https://example.com/",
            candidate_url="https://example.com/menu",
            source_type="website_link",
            link_text="Menu",
            confidence=0.45,
            evidence_grade="C",
            reason="strict keywords: menu",
            is_primary_menu_candidate=True,
            validation_status="unvalidated",
            validation_reason="candidate not fetched yet",
            fetched_at="2026-06-09T00:00:00+00:00",
            raw_payload={},
        )

        score, reasons = _score_image_menu_page(record, html)

        self.assertGreaterEqual(score, 0.3)
        self.assertIn("image candidates in menu context", "; ".join(reasons))

    def test_beyondmenu_order_link_is_ordering_page_candidate(self) -> None:
        row = _record_from_link(
            href="https://www.beyondmenu.com/51733/san-jose/jenny-s-kitchen-san-jose-95129.aspx",
            text="Order online",
            base_url="https://jennychinese.com/",
            website_url="https://jennychinese.com/",
            restaurant_name="Jenny's Kitchen",
            restaurant_source_id="example-id",
            fetched_at="2026-06-09T00:00:00+00:00",
            location_hint="5175 Moorpark Ave #5, San Jose, CA",
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.source_type, "ordering_page")
        self.assertIn("known ordering/menu platform", row.reason)


if __name__ == "__main__":
    unittest.main()
