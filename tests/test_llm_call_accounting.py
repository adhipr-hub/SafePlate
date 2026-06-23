"""Step 0 measurement: llm_calls must reflect the REAL number of Gemini chunk
calls, not a payload-level 1. A long source split into N chunks makes N calls;
the bench's apples-to-apples comparison depends on this being honest."""

import unittest
from types import SimpleNamespace
from unittest import mock

from safeplate.extraction2 import interpret_llm, pipeline
from safeplate.extraction2.schema import Payload, PayloadKind, Policy


class LlmCallAccountingTest(unittest.TestCase):
    def test_interpret_text_reports_chunk_call_count(self) -> None:
        with mock.patch.object(interpret_llm, "_readable_text", return_value="x"), \
             mock.patch.object(interpret_llm, "_chunks", return_value=["a", "b", "c"]), \
             mock.patch.object(
                 interpret_llm,
                 "_cached_or_call",
                 return_value={"page_had_menu": True, "menu_items": []},
             ):
            items, incomplete, llm_calls = interpret_llm.interpret_text(
                SimpleNamespace(), api_key="k", model="m"
            )
        self.assertEqual(items, [])
        self.assertFalse(incomplete)
        self.assertEqual(llm_calls, 3)

    def test_interpret_text_single_chunk_is_one_call(self) -> None:
        with mock.patch.object(interpret_llm, "_readable_text", return_value="x"), \
             mock.patch.object(interpret_llm, "_chunks", return_value=["only"]), \
             mock.patch.object(
                 interpret_llm,
                 "_cached_or_call",
                 return_value={"page_had_menu": True, "menu_items": []},
             ):
            _items, _incomplete, llm_calls = interpret_llm.interpret_text(
                SimpleNamespace(), api_key="k", model="m"
            )
        self.assertEqual(llm_calls, 1)

    def test_extract_menu_counts_real_chunk_calls(self) -> None:
        payload = Payload(url="https://x.test/menu", source_type="website_link",
                          kind=PayloadKind.TEXT, text="long menu text")
        with mock.patch.object(pipeline, "interpret_structured", return_value=[]), \
             mock.patch.object(
                 interpret_llm, "interpret_text", return_value=([], False, 3)
             ):
            result = pipeline.extract_menu(
                [payload], policy=Policy.HYBRID, llm_enabled=True, gemini_api_key="k"
            )
        self.assertEqual(result.llm_calls, 3)


if __name__ == "__main__":
    unittest.main()
