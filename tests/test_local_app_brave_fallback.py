from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from safeplate.local_app import run_menu_extraction
from safeplate.schemas import MenuSourceRecord


class LocalAppBraveFallbackTests(unittest.TestCase):
    def test_brave_menu_search_is_not_used_when_normal_sources_exist(self) -> None:
        normal_source = _menu_source("https://example.com/menu")

        with _patched_menu_file_writes(), patch(
            "safeplate.legacy_extraction.get_brave_search_api_key",
            return_value="brave-key",
        ), patch(
            "safeplate.legacy_extraction.discover_menu_sources_for_url",
            return_value=[normal_source],
        ) as normal_discovery, patch(
            "safeplate.legacy_extraction.discover_menu_sources_with_brave"
        ) as brave_discovery, patch(
            "safeplate.legacy_extraction.extract_menu_text_from_sources",
            return_value=[],
        ), patch(
            "safeplate.legacy_extraction.extract_menu_items_from_sources",
            return_value=[],
        ):
            response = run_menu_extraction(
                {
                    "name": "Example Cafe",
                    "sourceId": "place-id",
                    "websiteUrl": "https://example.com/",
                    "address": "1 Main St, San Jose, CA",
                    "engine": "v1",  # this test covers the v1 brave-fallback path
                }
            )

        normal_discovery.assert_called_once()
        brave_discovery.assert_not_called()
        self.assertFalse(response["summary"]["braveFallbackUsed"])
        self.assertEqual(response["menuSources"][0]["candidate_url"], normal_source.candidate_url)

    def test_brave_recovers_website_when_clicking_restaurant_without_website(self) -> None:
        recovered_source = _menu_source("https://jennychinese.com/menu")

        with _patched_menu_file_writes(), patch(
            "safeplate.legacy_extraction.get_brave_search_api_key",
            return_value="brave-key",
        ), patch(
            "safeplate.legacy_extraction.recover_restaurant_website_with_brave",
            return_value={
                "status": "recovered",
                "website_url": "https://jennychinese.com/",
                "reason": "verified",
                "confidence": 1.0,
                "queries": [],
                "candidates": [],
            },
        ) as recovery, patch(
            "safeplate.legacy_extraction.discover_menu_sources_for_url",
            return_value=[recovered_source],
        ) as normal_discovery, patch(
            "safeplate.legacy_extraction.discover_menu_sources_with_brave"
        ) as brave_discovery, patch(
            "safeplate.legacy_extraction.extract_menu_text_from_sources",
            return_value=[],
        ), patch(
            "safeplate.legacy_extraction.extract_menu_items_from_sources",
            return_value=[],
        ):
            response = run_menu_extraction(
                {
                    "name": "Jenny's Kitchen",
                    "sourceId": "place-id",
                    "address": "5175 Moorpark Ave #5, San Jose, CA",
                    "phoneNumber": "(408) 996-1199",
                    "categories": ["chinese_restaurant"],
                    "engine": "v1",  # this test covers the v1 brave-fallback path
                }
            )

        recovery.assert_called_once()
        normal_discovery.assert_called_once()
        brave_discovery.assert_not_called()
        self.assertTrue(response["summary"]["braveFallbackUsed"])
        self.assertEqual(response["websiteUrl"], "https://jennychinese.com/")


def _menu_source(url: str) -> MenuSourceRecord:
    return MenuSourceRecord(
        restaurant_name="Example Cafe",
        restaurant_source_id="place-id",
        website_url="https://example.com/",
        candidate_url=url,
        source_type="website_link",
        link_text="Menu",
        confidence=0.9,
        evidence_grade="A",
        reason="test source",
        is_primary_menu_candidate=True,
        validation_status="validated",
        validation_reason="test source",
        fetched_at="2026-06-14T00:00:00+00:00",
        raw_payload={},
    )


def _patched_menu_file_writes():
    patches = [
        patch(
            "safeplate.legacy_extraction.build_menu_output_paths",
            return_value=(Path("menu_sources.json"), Path("menu_sources.csv")),
        ),
        patch(
            "safeplate.legacy_extraction.build_menu_text_output_paths",
            return_value=(Path("menu_text.json"), Path("menu_text.csv")),
        ),
        patch(
            "safeplate.legacy_extraction.build_menu_item_output_paths",
            return_value=(Path("menu_items.json"), Path("menu_items.csv")),
        ),
        patch("safeplate.legacy_extraction._build_local_validation_output_path", return_value=Path("validation.json")),
        patch("safeplate.legacy_extraction.write_menu_sources_json"),
        patch("safeplate.legacy_extraction.write_menu_sources_csv"),
        patch("safeplate.legacy_extraction.write_menu_text_json"),
        patch("safeplate.legacy_extraction.write_menu_text_csv"),
        patch("safeplate.legacy_extraction.write_menu_items_json"),
        patch("safeplate.legacy_extraction.write_menu_items_csv"),
        patch("safeplate.legacy_extraction._write_menu_validation_json"),
    ]
    return _PatchStack(patches)


class _PatchStack:
    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        for item in reversed(self._patches):
            item.stop()
        return False


if __name__ == "__main__":
    unittest.main()
