"""Regression tests for the correctness/safety/leak fixes from the codebase audit.

Each test pins a specific bug so it can't silently come back:
  #1  api_server rate-limiter map stays bounded (sweeps stale buckets)
  #3  api_server _write_body swallows client-disconnect errors
  #4  allergen_score._nut_terms_present catches generic nut phrasings (SAFETY)
  #5  extraction2.verify grounding no longer matches short substrings ("rice"/"price")
  #6  interpret_text / extract_menu flag a partial multi-chunk LLM read as incomplete
  #13 coerce.float_value/int_value keep a legitimate 0
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
import unittest
from unittest import mock

from safeplate import api_server
from safeplate.coerce import float_value, int_value
from safeplate.allergen_prior import NUTS, PEANUTS, TREE_NUTS
from safeplate.allergen_score import _families, _nut_terms_present
from safeplate.extraction2 import interpret_llm, pipeline
from safeplate.extraction2.schema import Payload, PayloadKind, Policy
from safeplate.extraction2.verify import _is_grounded, _collapse, _normalize


class NutTermRecognitionTest(unittest.TestCase):
    """#4 -- the deterministic scorer must recognize free-text nut mentions, since
    LLM extraction feeds raw allergen_mentions strings straight in. A missed term is
    a safety-asymmetric under-statement."""

    def setUp(self) -> None:
        self.families = _families(NUTS)  # {PEANUTS, TREE_NUTS}

    def test_generic_and_compound_nut_phrasings_are_caught(self) -> None:
        for term in ("nuts", "nut", "mixed nuts", "nut oil", "tree-nuts", "contains nuts"):
            with self.subTest(term=term):
                self.assertTrue(
                    _nut_terms_present([term], self.families),
                    f"{term!r} should count as a nut hit",
                )

    def test_named_nuts_still_match(self) -> None:
        self.assertTrue(_nut_terms_present(["almond"], self.families))
        self.assertTrue(_nut_terms_present(["cashew"], {TREE_NUTS}))
        self.assertTrue(_nut_terms_present(["peanut"], {PEANUTS}))

    def test_false_friends_do_not_fire(self) -> None:
        for term in ("coconut", "coconut milk", "butternut squash", "nutmeg", "doughnut"):
            with self.subTest(term=term):
                self.assertFalse(
                    _nut_terms_present([term], self.families),
                    f"{term!r} must not count as a nut hit",
                )

    def test_peanut_only_user_ignores_tree_nut_term(self) -> None:
        # A peanut-only family should not be triggered by an almond mention.
        self.assertEqual(_nut_terms_present(["almond"], {PEANUTS}), [])


class GroundingPrecisionTest(unittest.TestCase):
    """#5 -- short whitespace-stripped substrings must not ground; longer names keep
    the letter-spacing-proof fallback."""

    def test_short_name_does_not_ground_on_substring(self) -> None:
        source = "the price of service"
        norm, collapsed = _normalize(source), _collapse(source)
        self.assertFalse(_is_grounded("rice", source_norm=norm, source_collapsed=collapsed))
        self.assertFalse(_is_grounded("ice", source_norm=norm, source_collapsed=collapsed))

    def test_real_word_grounds_on_boundary(self) -> None:
        source = "Fried Rice with egg and scallion"
        norm, collapsed = _normalize(source), _collapse(source)
        self.assertTrue(_is_grounded("rice", source_norm=norm, source_collapsed=collapsed))
        self.assertTrue(
            _is_grounded("fried rice", source_norm=norm, source_collapsed=collapsed)
        )

    def test_letter_spaced_long_name_grounds_via_fallback(self) -> None:
        # A PDF that letter-spaces a long dish name still grounds (stripped fallback).
        source = "m a r g h e r i t a pizza"
        norm, collapsed = _normalize(source), _collapse(source)
        self.assertTrue(
            _is_grounded("margherita", source_norm=norm, source_collapsed=collapsed)
        )


class CoerceZeroTest(unittest.TestCase):
    """#13 -- a legitimate 0 must survive, not collapse to the default."""

    def test_zero_is_preserved(self) -> None:
        self.assertEqual(float_value(0), 0.0)
        self.assertEqual(float_value(0.0, default=5.0), 0.0)
        self.assertEqual(int_value(0, default=7), 0)
        self.assertEqual(int_value("0", default=7), 0)

    def test_empty_and_invalid_fall_back_to_default(self) -> None:
        self.assertEqual(float_value("", default=5.0), 5.0)
        self.assertEqual(float_value(None, default=5.0), 5.0)
        self.assertEqual(float_value("abc", default=5.0), 5.0)
        self.assertEqual(int_value(None, default=7), 7)

    def test_normal_values_parse(self) -> None:
        self.assertEqual(float_value("12.5"), 12.5)
        self.assertEqual(int_value("3"), 3)


class RateLimiterBoundTest(unittest.TestCase):
    """#1 -- the per-client map must not grow without bound; stale buckets are swept
    once the cap is crossed."""

    def test_stale_buckets_are_swept(self) -> None:
        limiter = api_server._RateLimiter(max_requests=100, window_seconds=60.0)
        with mock.patch.object(api_server, "_RATE_LIMIT_MAX_KEYS", 3):
            # Pre-load buckets whose only hit is ancient (timestamp 1.0 << now-60).
            for i in range(20):
                limiter._hits[f"old{i}"] = deque([1.0])
            self.assertEqual(limiter.check("active"), True)
            # The active key survives; the ancient ones are gone.
            self.assertIn("active", limiter._hits)
            self.assertLessEqual(len(limiter._hits), 4)

    def test_limit_still_enforced(self) -> None:
        limiter = api_server._RateLimiter(max_requests=2, window_seconds=60.0)
        self.assertTrue(limiter.check("a"))
        self.assertTrue(limiter.check("a"))
        self.assertFalse(limiter.check("a"))  # third within window -> blocked


class WriteBodyDisconnectTest(unittest.TestCase):
    """#3 -- a client disconnect mid-response must not escape as an unhandled error."""

    def test_broken_pipe_is_swallowed(self) -> None:
        handler_cls = api_server.create_app_handler()
        inst = handler_cls.__new__(handler_cls)  # bypass socket/BaseHTTPRequestHandler init

        class _Boom:
            def write(self, _data: bytes) -> None:
                raise BrokenPipeError("client went away")

        inst.wfile = _Boom()
        inst._write_body(b"payload")  # must not raise


class IncompleteExtractionTest(unittest.TestCase):
    """#6 -- a failed chunk in a multi-chunk LLM read marks the result incomplete so
    callers don't cache a silently-partial menu."""

    def test_interpret_text_flags_chunk_failure(self) -> None:
        with mock.patch.object(interpret_llm, "_readable_text", return_value="long menu text"), \
             mock.patch.object(interpret_llm, "_chunks", return_value=["chunk-a", "chunk-b"]), \
             mock.patch.object(
                 interpret_llm,
                 "_cached_or_call",
                 return_value={"page_had_menu": False, "menu_items": [], "_failed": True},
             ):
            items, incomplete = interpret_llm.interpret_text(
                SimpleNamespace(), api_key="k", model="m"
            )
        self.assertEqual(items, [])
        self.assertTrue(incomplete)

    def test_interpret_text_complete_when_no_failure(self) -> None:
        with mock.patch.object(interpret_llm, "_readable_text", return_value="long menu text"), \
             mock.patch.object(interpret_llm, "_chunks", return_value=["only-chunk"]), \
             mock.patch.object(
                 interpret_llm,
                 "_cached_or_call",
                 return_value={"page_had_menu": True, "menu_items": []},
             ):
            items, incomplete = interpret_llm.interpret_text(
                SimpleNamespace(), api_key="k", model="m"
            )
        self.assertFalse(incomplete)

    def test_extract_menu_propagates_incomplete(self) -> None:
        payload = Payload(url="https://x.test/menu", source_type="website_link",
                          kind=PayloadKind.TEXT, text="long menu text")
        with mock.patch.object(pipeline, "interpret_structured", return_value=[]), \
             mock.patch.object(
                 interpret_llm, "interpret_text", return_value=([], True)
             ):
            result = pipeline.extract_menu(
                [payload], policy=Policy.HYBRID, llm_enabled=True, gemini_api_key="k"
            )
        self.assertTrue(result.incomplete)

    def test_extract_menu_complete_by_default(self) -> None:
        payload = Payload(url="https://x.test/menu", source_type="website_link",
                          kind=PayloadKind.TEXT, text="long menu text")
        with mock.patch.object(pipeline, "interpret_structured", return_value=[]), \
             mock.patch.object(
                 interpret_llm, "interpret_text", return_value=([], False)
             ):
            result = pipeline.extract_menu(
                [payload], policy=Policy.HYBRID, llm_enabled=True, gemini_api_key="k"
            )
        self.assertFalse(result.incomplete)


if __name__ == "__main__":
    unittest.main()
