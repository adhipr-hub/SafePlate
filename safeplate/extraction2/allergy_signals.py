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
import time
from typing import Any

from safeplate.config import get_cache_dir
from safeplate.extraction2.interpret_llm import _call_with_retry, _readable_text
from safeplate.extraction2.schema import AllergySignal, Payload
from safeplate.gemini_menu import GeminiMenuError

DEFAULT_MODEL = "gemini-3.1-flash-lite"
_CACHE_TTL = 14 * 24 * 60 * 60

SYSTEM = (
    "You are reading ONE restaurant web page. Determine how the restaurant handles "
    "food ALLERGIES (not nutrition). Return four booleans, true only if the page "
    "actually says so: allergy_friendly_claim (claims to accommodate/handle "
    "allergies, e.g. 'allergy-friendly', 'we can adapt dishes'), cross_contact_warning "
    "(warns about cross-contamination / shared equipment / 'may contain'), ask_staff "
    "(tells guests to inform/ask staff about allergies), allergen_menu_available "
    "(mentions an allergen menu/guide/chart/matrix). Also return up to 5 VERBATIM "
    "statements (exact quotes from the page) about allergy handling. If the page is "
    "not about allergies, set all booleans false and statements empty."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "allergy_friendly_claim": {"type": "boolean"},
        "cross_contact_warning": {"type": "boolean"},
        "ask_staff": {"type": "boolean"},
        "allergen_menu_available": {"type": "boolean"},
        "statements": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "allergy_friendly_claim", "cross_contact_warning", "ask_staff",
        "allergen_menu_available", "statements",
    ],
}


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
    if not any(flags.values()) and not statements:
        return None
    return AllergySignal(
        url=payload.url,
        statements=statements,
        confidence=0.5,
        **flags,
    )


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if not ch.isspace())


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
