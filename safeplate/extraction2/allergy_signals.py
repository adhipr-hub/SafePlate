"""Extract restaurant-level allergy-HANDLING signals from a narrative page.

A restaurant may carry no dish x allergen matrix yet still say a lot about how it
handles allergies -- "our kitchen is allergy-friendly", "all dishes are prepared
in an environment where allergens are present", "please tell your server about any
allergies", "ask for our allergen guide". That qualitative signal feeds the risk
score and the UI's allergy-awareness section. The LLM reads the page; every quote
is then grounded against the source text so nothing is invented.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from safeplate.config import get_cache_dir
from safeplate.extraction2.interpret_llm import _call_with_retry, _readable_text
from safeplate.extraction2.schema import AllergySignal, Payload
from safeplate.gemini_menu import GeminiMenuError
from safeplate.textutil import strip_ws

DEFAULT_MODEL = "gemini-3.1-flash-lite"
_CACHE_TTL = 14 * 24 * 60 * 60

SYSTEM = (
    "You are reading ONE restaurant web page. Determine how the restaurant handles "
    "food ALLERGIES (not nutrition). Return booleans, true only if the page actually "
    "says so: allergy_friendly_claim (claims to accommodate/handle allergies, e.g. "
    "'allergy-friendly', 'we can adapt dishes'), cross_contact_warning (warns about "
    "cross-contamination / shared equipment / 'may contain'), ask_staff (tells guests "
    "to inform/ask staff about allergies), allergen_menu_available (mentions an "
    "allergen menu/guide/chart/matrix), and nut_free_claim. Set nut_free_claim TRUE "
    "ONLY when the restaurant states its KITCHEN or FACILITY is nut-free / that it "
    "does NOT use nuts at all (e.g. '100% nut-free facility', 'no nuts in our "
    "kitchen', 'we are a peanut- and tree-nut-free bakery'). Set it FALSE for merely "
    "offering 'nut-free options/items', being 'allergy-friendly', or having some "
    "nut-free products -- those do NOT make the kitchen nut-free. Also return up to 5 "
    "VERBATIM statements (exact quotes) about allergy handling; for nut_free_claim, "
    "INCLUDE the exact quote that supports it. If the page is not about allergies, set "
    "all booleans false and statements empty."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "allergy_friendly_claim": {"type": "boolean"},
        "cross_contact_warning": {"type": "boolean"},
        "ask_staff": {"type": "boolean"},
        "allergen_menu_available": {"type": "boolean"},
        "nut_free_claim": {"type": "boolean"},
        "statements": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "allergy_friendly_claim", "cross_contact_warning", "ask_staff",
        "allergen_menu_available", "nut_free_claim", "statements",
    ],
}

# Despaced nut-free phrases used to GROUND a nut_free_claim: the LLM's boolean is only
# honoured if a source-grounded statement actually contains nut-free wording (and not
# the weaker "nut-free option" phrasing). Belt-and-suspenders against over-crediting a
# powerful down-signal.
_NUT_FREE_PHRASES = (
    "nutfree", "nutsfree", "nonuts", "withoutnuts", "freeofnuts", "freefromnuts",
    "donotusenuts", "peanutfree", "treenutfree",
)


def extract_allergy_signals(
    payload: Payload,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> AllergySignal | None:
    """Return an AllergySignal for the page, or None if it says nothing about
    allergy handling. Statements are dropped unless grounded in the source text."""
    if not api_key:
        return None
    text = _readable_text(payload)
    if not text.strip():
        return None
    parsed = _cached_or_call(text, api_key=api_key, model=model or DEFAULT_MODEL)
    if parsed is None:
        return None

    source_norm = _normalize(text)
    statements = [
        s.strip() for s in parsed.get("statements", [])
        if isinstance(s, str) and s.strip()
        and not s.strip().endswith("?")        # drop FAQ questions -- not handling claims
        and _normalize(s) in source_norm        # keep only source-grounded quotes
    ][:5]
    flags = {k: bool(parsed.get(k)) for k in
             ("allergy_friendly_claim", "cross_contact_warning", "ask_staff", "allergen_menu_available")}
    # Honor nut_free_claim only when a source-grounded statement carries real nut-free
    # wording (and not the weaker "...option") -- a strong down-signal must be earned.
    nut_free = bool(parsed.get("nut_free_claim")) and any(
        any(p in _alnum(s) for p in _NUT_FREE_PHRASES) and "option" not in s.lower()
        for s in statements
    )
    if not any(flags.values()) and not nut_free and not statements:
        return None
    return AllergySignal(
        url=payload.url,
        statements=statements,
        confidence=0.5,
        nut_free_claim=nut_free,
        **flags,
    )


# Letter-spacing-proof grounding key (lowercase, strip ALL whitespace); via textutil.
_normalize = strip_ws


def _alnum(text: str) -> str:
    """Lowercase, keep only a-z0-9 -- so 'nut-free' / 'nut free' both match 'nutfree'."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _cached_or_call(text: str, *, api_key: str, model: str) -> dict[str, Any] | None:
    key = hashlib.sha1(f"allergysig:{model}:{text}".encode("utf-8")).hexdigest()
    path = get_cache_dir() / "extraction2_allergy" / f"{key}.json"
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - blob.get("at", 0) <= _CACHE_TTL:
            return blob["parsed"]
    except (OSError, ValueError, KeyError):
        pass

    request = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": "Page text:\n\n" + text}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": SCHEMA,
        },
    }
    try:
        parsed = _call_with_retry(request, api_key=api_key, model=model)
    except GeminiMenuError:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": time.time(), "parsed": parsed}), encoding="utf-8")
    except OSError:
        pass
    return parsed
