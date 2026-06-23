"""Turbo parity + speed: read up to 4 chunks (matching production's 96k-char
budget, not 3), and process a source's chunks concurrently like production."""

from __future__ import annotations

import inspect
from unittest import mock

import pytest

pytest.importorskip("httpx")
pytest.importorskip("selectolax")
pytest.importorskip("trafilatura")

from safeplate.extraction2.schema import Payload, PayloadKind
from safeplate.turbo import extract as turbo_extract


def test_max_chunks_default_is_four():
    assert inspect.signature(
        turbo_extract.extract_restaurant
    ).parameters["max_chunks"].default == 4


def test_turbo_llm_processes_all_chunks_and_counts_calls():
    payload = Payload(url="https://x.test/m", source_type="website_link",
                      kind=PayloadKind.TEXT, text="t")

    def fake_parsed(c, *, api_key, model):
        return {"menu_items": [{"item_name": f"dish-{c}", "evidence_quote": "q"}]}

    with mock.patch.object(turbo_extract.interpret_llm, "_chunks",
                           return_value=["c1", "c2", "c3"]), \
         mock.patch.object(turbo_extract.interpret_llm, "_cached_or_call",
                           side_effect=fake_parsed), \
         mock.patch.object(turbo_extract, "verify",
                           side_effect=lambda items, gp, require_grounding: (items, [])):
        kept, n_calls = turbo_extract._turbo_llm("text", payload, "k", "m", 4)

    assert n_calls == 3
    assert {r.item_name for r in kept} == {"dish-c1", "dish-c2", "dish-c3"}
