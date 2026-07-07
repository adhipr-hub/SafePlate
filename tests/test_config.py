from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import safeplate.config as config
from safeplate.config import get_gemini_fallback_models
from safeplate.common import _is_gemini_model_fallback_error


class ConfigTests(unittest.TestCase):
    def test_gemini_fallback_models_are_parsed_and_deduped(self) -> None:
        with patch.dict(
            os.environ,
            {"GEMINI_FALLBACK_MODELS": "gemini-a, gemini-b, gemini-a, ,gemini-c"},
        ):
            self.assertEqual(
                get_gemini_fallback_models(),
                ["gemini-a", "gemini-b", "gemini-c"],
            )

    def test_gemini_fallback_error_detection_is_narrow(self) -> None:
        self.assertTrue(
            _is_gemini_model_fallback_error(
                "Gemini request failed with HTTP 503: high demand"
            )
        )
        self.assertTrue(
            _is_gemini_model_fallback_error(
                "Gemini request failed with HTTP 429: RESOURCE_EXHAUSTED"
            )
        )
        self.assertTrue(
            _is_gemini_model_fallback_error(
                "models/gemini-x is not found for API version v1beta"
            )
        )
        self.assertFalse(
            _is_gemini_model_fallback_error(
                "Gemini request failed with HTTP 403: API key invalid"
            )
        )


def test_get_database_url_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert config.get_database_url() is None


def test_get_database_url_blank_is_none(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert config.get_database_url() is None


def test_get_database_url_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", " postgresql://u:p@h:5432/db ")
    assert config.get_database_url() == "postgresql://u:p@h:5432/db"


if __name__ == "__main__":
    unittest.main()
