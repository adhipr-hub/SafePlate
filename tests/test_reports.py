from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from safeplate.menu_text import MENU_ITEM_CSV_FIELDS, MENU_TEXT_CSV_FIELDS
from safeplate.reports import write_menu_extraction_report


class MenuExtractionReportTests(unittest.TestCase):
    def test_writes_extraction_contribution_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            text_csv = root / "menu_text.csv"
            item_csv = root / "menu_items.csv"
            html_path = root / "report.html"

            with text_csv.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=MENU_TEXT_CSV_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "restaurant_name": "Example",
                        "restaurant_source_id": "example-id",
                        "menu_source_url": "https://example.com/menu.pdf",
                        "source_type": "pdf",
                        "extraction_method": "pdf_text",
                        "char_count": "42",
                        "price_count": "2",
                        "dietary_terms": "vegetarian",
                        "allergen_terms": "milk",
                        "fetched_at": "2026-06-09T00:00:00+00:00",
                        "extracted_text": "Example menu text",
                    }
                )

            with item_csv.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=MENU_ITEM_CSV_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "restaurant_name": "Example",
                        "restaurant_source_id": "example-id",
                        "menu_source_url": "https://example.com/menu.pdf",
                        "category": "Entrees",
                        "item_name": "Pasta",
                        "description": "Tomato sauce",
                        "price": "$12",
                        "dietary_terms": "vegetarian",
                        "allergen_terms": "milk",
                        "source_type": "pdf",
                        "extraction_method": "pdf_text",
                        "confidence": "0.85",
                        "raw_text": "Pasta Tomato sauce $12",
                        "fetched_at": "2026-06-09T00:00:00+00:00",
                    }
                )

            write_menu_extraction_report(
                text_csv_path=text_csv,
                item_csv_path=item_csv,
                html_path=html_path,
                title="Example Extraction",
            )

            html = html_path.read_text(encoding="utf-8")
            self.assertIn("Text Contribution By Method", html)
            self.assertIn("pdf_text", html)
            self.assertIn("Menu Items By Restaurant", html)


if __name__ == "__main__":
    unittest.main()
