"""Pin thinkingLevel=minimal for Gemini 3.x calls (floors thinking-token latency;
verbatim extraction + verify() grounding are unaffected). Older 2.x/2.5 models do
not accept thinkingLevel, so it must be omitted there -- and thinkingLevel and the
legacy thinkingBudget must never appear together (that is a hard 400)."""

from safeplate.gemini_menu import (
    _apply_thinking_config,
    _cached_token_count,
    _thinking_config,
)


def test_cached_token_count_reads_usage_metadata():
    assert _cached_token_count(
        {"usageMetadata": {"cachedContentTokenCount": 42}}
    ) == 42
    assert _cached_token_count({}) == 0
    assert _cached_token_count({"usageMetadata": {}}) == 0


def test_gemini_3x_gets_minimal_thinking():
    assert _thinking_config("gemini-3.1-flash-lite") == {"thinkingLevel": "minimal"}


def test_gemini_25_omits_thinking_level():
    assert _thinking_config("gemini-2.5-flash-lite") is None
    assert _thinking_config("gemini-2.0-flash-lite") is None


def test_apply_injects_for_3x_and_never_pairs_with_budget():
    payload = {"generationConfig": {"temperature": 0}}
    _apply_thinking_config(payload, "gemini-3.1-flash-lite")
    tc = payload["generationConfig"]["thinkingConfig"]
    assert tc == {"thinkingLevel": "minimal"}
    assert "thinkingBudget" not in tc


def test_apply_noop_for_25():
    payload = {"generationConfig": {"temperature": 0}}
    _apply_thinking_config(payload, "gemini-2.5-flash-lite")
    assert "thinkingConfig" not in payload["generationConfig"]


def test_apply_does_not_override_explicit_thinking_config():
    payload = {"generationConfig": {"thinkingConfig": {"thinkingLevel": "high"}}}
    _apply_thinking_config(payload, "gemini-3.1-flash-lite")
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}
