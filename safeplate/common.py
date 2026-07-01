"""Leaf utilities shared across the API server + search/menu services: env/coerce
helpers, request->profile/engine parsing, and small payload shapers. No dependency on
the service modules (keeps the import graph acyclic)."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from safeplate.config import (
    get_gemini_fallback_models,
    get_gemini_model,
    get_google_places_api_key,
    normalize_scoring_engine,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _severity_from_str(value: Any):
    from safeplate.allergen_score import Severity

    return {
        "avoid_preference": Severity.AVOID_PREFERENCE,
        "intolerance": Severity.INTOLERANCE,
        "allergy": Severity.ALLERGY,
        "anaphylaxis": Severity.ANAPHYLAXIS,
    }.get(str(value or "").lower(), Severity.ALLERGY)


def _scoring_engine_from_payload(payload: dict[str, Any]) -> str:
    """Which SCORING engine to use: 'ai' = label-routing LLM scorer (DEFAULT), 'rules'
    = deterministic. Per-request `scoringEngine`, else env SAFEPLATE_SCORING_ENGINE.
    Legacy 'v2'/'v3'/'ai_assisted'/'ai_full_menu' values are still accepted."""
    return normalize_scoring_engine(
        payload.get("scoringEngine") or os.environ.get("SAFEPLATE_SCORING_ENGINE", "")
    )


def _is_ai_engine(scoring_engine: str) -> bool:
    """True for the LLM scoring engine ('ai'); False for the deterministic 'rules'.
    The AI engine label-routes per restaurant (labeled / raw_menu / no_menu)."""
    return scoring_engine == "ai"


def _cross_contact_from_str(value: Any):
    """Map the UI's cross-contact choice to a CrossContactSensitivity. Returns None
    for unset/unknown so the scorer derives a sensible level from severity."""
    from safeplate.allergen_score import CrossContactSensitivity

    return {
        "not_concerned": CrossContactSensitivity.NOT_CONCERNED,
        "moderate": CrossContactSensitivity.MODERATE,
        "strict": CrossContactSensitivity.STRICT,
    }.get(str(value or "").lower())


def _diets_from_payload(payload: dict[str, Any]) -> tuple[frozenset, bool]:
    """Returns (real_diet_keys, gluten_free_requested). gluten_free is NOT a diet;
    it is consumed into a gluten allergen by the caller."""
    from safeplate.allergens import DIETS

    raw = payload.get("diets") or []
    diets = {str(d).lower() for d in raw}
    gf = "gluten_free" in diets
    return frozenset(d for d in diets if d in DIETS), gf


def _user_profile_from_payload(payload: dict[str, Any]):
    """Build a scorer UserProfile from the request. Supports the legacy nuts-only
    shape (``severity``/``crossContact``/``nutTypes``) as well as a multi-allergen
    ``allergens`` list (each with its own severity/crossContact) and ``diets`` flags.
    ``gluten_free`` is consumed into a ``gluten`` AllergenPref rather than surfaced
    as a diet. Severity is honoured per-allergen so the same risk trips a worse tier
    for an anaphylactic user than a mild-preference one, and cross-contact sensitivity
    is honoured INDEPENDENTLY of severity (trace tolerance vs ingestion reaction).
    ``nutTypes`` (a list of specific nuts the user reacts to) turns on per-nut scoring;
    absent/empty -> the family-level default."""
    from safeplate.allergen_prior import normalize_nut_types
    from safeplate.allergen_score import AllergenPref, Severity, UserProfile
    from safeplate.allergens import canonical

    diets, gluten_free = _diets_from_payload(payload)
    raw_allergens = payload.get("allergens")

    if not raw_allergens and not diets and not gluten_free:
        # legacy nuts-only path -- byte-identical to before
        return UserProfile.for_nuts(
            _severity_from_str(payload.get("severity")),
            cross_contact=_cross_contact_from_str(payload.get("crossContact")),
            nut_types=normalize_nut_types(payload.get("nutTypes")),
        )

    prefs = []
    for entry in raw_allergens or []:
        key = canonical(str(entry.get("allergen", "")))
        if key is None:
            # allow "nuts" family key through untouched
            if str(entry.get("allergen", "")).lower() in ("nuts", "peanuts", "tree_nuts"):
                key = str(entry.get("allergen")).lower()
            else:
                continue
        prefs.append(AllergenPref(
            allergen=key,
            severity=_severity_from_str(entry.get("severity")),
            cross_contact=_cross_contact_from_str(entry.get("crossContact")),
            nut_types=normalize_nut_types(entry.get("nutTypes")) if key == "nuts" else None,
        ))
    if gluten_free and not any(p.allergen == "gluten" for p in prefs):
        prefs.append(AllergenPref(allergen="gluten", severity=Severity.ALLERGY))
    return UserProfile(allergens=tuple(prefs), diets=diets)


def _is_gemini_model_fallback_error(message: str) -> bool:
    lower_message = message.lower()
    return any(
        marker in lower_message
        for marker in [
            "http 429",
            "http 503",
            "high demand",
            "unavailable",
            "resource_exhausted",
            "is not found for api version",
            "is not supported for generatecontent",
        ]
    )


def _menu_item_payloads(menu_items: list[Any]) -> list[dict[str, Any]]:
    payloads = []
    for index, item in enumerate(menu_items, start=1):
        payload = _safe_payload(item)
        payload["candidate_id"] = f"c{index:04d}"
        payloads.append(payload)
    return payloads


def _empty_validation_summary() -> dict[str, Any]:
    return {
        "enabled": False,
        "model": get_gemini_model(),
        "modelUsed": "",
        "fallbackModels": get_gemini_fallback_models(),
        "candidateRows": 0,
        "validatedRows": 0,
        "acceptedRows": 0,
        "rejectedRows": 0,
        "missingRows": 0,
        "warnings": [],
        "attemptErrors": [],
        "error": "",
    }


def _safe_payload(row: Any) -> dict[str, Any]:
    return asdict(row)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(";") if item.strip()]
    return []


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _default_provider() -> str:
    if get_google_places_api_key():
        return "google"
    return "osm"
