"""Legacy ("v1") menu-extraction path, isolated from local_app.

The ORIGINAL prose-heuristic extraction pipeline. The product default is the
structured extraction2 engine + Layer-#5 scorer (see local_app); this module is a
dormant baseline kept for scripts/tests/back-compat. ``run_menu_extraction`` in
local_app dispatches here when the engine resolves to "legacy".

Shared low-level helpers (`_menu_summary`, `_safe_payload`, `_menu_item_payloads`,
`_empty_validation_summary`, `_string_list`, `_is_gemini_model_fallback_error`,
`_slugify`, `_chunks`, DATA_DIR, GEMINI_MENU_VALIDATION_CHUNK_SIZE) stay in local_app
(also used by demo/structured) and are imported below.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from safeplate.brave_search import (
    BraveSearchError,
    discover_menu_sources_with_brave,
    recover_restaurant_website_with_brave,
)
from safeplate.coerce import chunks as _chunks
from safeplate.coerce import optional_float as _optional_float
from safeplate.coerce import optional_int as _optional_int
from safeplate.config import (
    get_brave_search_api_key,
    get_gemini_api_key,
    get_gemini_fallback_models,
    get_gemini_model,
    get_user_agent,
)
from safeplate.gemini_menu import GeminiMenuError, validate_menu_candidates_with_gemini
from safeplate.menu_sources import (
    MenuSourceError,
    build_menu_output_paths,
    discover_menu_sources_for_url,
    write_menu_sources_csv,
    write_menu_sources_json,
)
from safeplate.menu_text import (
    build_menu_item_output_paths,
    build_menu_text_output_paths,
    extract_menu_items_from_sources,
    extract_menu_text_from_sources,
    write_menu_items_csv,
    write_menu_items_json,
    write_menu_text_csv,
    write_menu_text_json,
)
from safeplate.schemas import RestaurantRecord

from safeplate.textutil import slugify as _slugify

from safeplate.local_app import (
    DATA_DIR,
    GEMINI_MENU_VALIDATION_CHUNK_SIZE,
    _empty_validation_summary,
    _is_gemini_model_fallback_error,
    _menu_item_payloads,
    _menu_summary,
    _safe_payload,
    _string_list,
)


def run_legacy_menu_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    restaurant_name = str(payload.get("name") or "").strip()
    restaurant_source_id = str(payload.get("sourceId") or "").strip()
    website_url = str(payload.get("websiteUrl") or "").strip()
    address = str(payload.get("address") or "").strip()
    phone_number = str(payload.get("phoneNumber") or "").strip()
    categories = _string_list(payload.get("categories"))
    if not restaurant_name:
        raise ValueError("Restaurant name is required.")

    user_agent = get_user_agent()
    menu_source_errors = []
    website_recovery: dict[str, Any] | None = None
    brave_fallback_used = False
    menu_sources = []
    if website_url:
        menu_sources = _discover_menu_sources_for_website(
            website_url=website_url,
            restaurant_name=restaurant_name,
            restaurant_source_id=restaurant_source_id,
            user_agent=user_agent,
            address=address,
            errors=menu_source_errors,
        )

    brave_api_key = get_brave_search_api_key()
    if not menu_sources and brave_api_key:
        brave_fallback_used = True
        try:
            if not website_url:
                website_recovery = recover_restaurant_website_with_brave(
                    _restaurant_record_from_menu_payload(
                        restaurant_name=restaurant_name,
                        restaurant_source_id=restaurant_source_id,
                        website_url=website_url,
                        address=address,
                        phone_number=phone_number,
                        categories=categories,
                        payload=payload,
                    ),
                    api_key=brave_api_key,
                    user_agent=user_agent,
                    results_per_query=5,
                )
                if website_recovery.get("website_url"):
                    website_url = str(website_recovery["website_url"])
                    menu_sources = _discover_menu_sources_for_website(
                        website_url=website_url,
                        restaurant_name=restaurant_name,
                        restaurant_source_id=restaurant_source_id,
                        user_agent=user_agent,
                        address=address,
                        errors=menu_source_errors,
                    )

            if not menu_sources and website_url:
                brave_sources = discover_menu_sources_with_brave(
                    restaurant_name=restaurant_name,
                    restaurant_source_id=restaurant_source_id,
                    website_url=website_url,
                    address=address,
                    api_key=brave_api_key,
                    user_agent=user_agent,
                    limit=8,
                    fetch_mode="static",
                )
                menu_sources = _dedupe_menu_sources(brave_sources)[:16]
        except BraveSearchError as exc:
            menu_source_errors.append({"source": "brave_search", "error": str(exc)})
    elif not menu_sources and not brave_api_key and not website_url:
        menu_source_errors.append(
            {
                "source": "website_lookup",
                "error": "No website URL from the provider and Brave Search is not configured.",
            }
        )

    if not menu_sources and website_recovery and not website_recovery.get("website_url"):
        menu_source_errors.append(
            {
                "source": "brave_website_recovery",
                "error": website_recovery.get("reason", "No verified website recovered."),
            }
        )

    if not menu_sources:
        return {
            "restaurantName": restaurant_name,
            "websiteUrl": website_url,
            "websiteRecovery": website_recovery,
            "menuSources": [],
            "menuText": [],
            "menuItems": [],
            "rejectedMenuItems": [],
            "summary": _menu_summary(
                [],
                [],
                [],
                parsed_item_count=0,
                rejected_items=[],
                validation_summary=_empty_validation_summary(),
                menu_source_errors=menu_source_errors,
                website_url=website_url,
                website_recovery=website_recovery,
                brave_fallback_used=brave_fallback_used,
                restaurant_payload=payload,
            ),
            "files": {},
        }

    menu_source_rows = [asdict(row) for row in menu_sources]
    menu_text = extract_menu_text_from_sources(
        menu_source_rows=menu_source_rows,
        user_agent=user_agent,
        include_unvalidated=False,
        max_chars=12000,
        fetch_mode="static",
    )
    menu_items = extract_menu_items_from_sources(
        menu_source_rows=menu_source_rows,
        user_agent=user_agent,
        include_unvalidated=False,
        max_items_per_source=300,
        fetch_mode="static",
    )
    (
        displayed_menu_items,
        rejected_menu_items,
        all_menu_item_payloads,
        validation_summary,
    ) = _validate_menu_item_payloads(
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
        menu_items=menu_items,
    )

    label = f"local_app_{restaurant_name}"
    menu_sources_json, menu_sources_csv = build_menu_output_paths(label, DATA_DIR)
    menu_text_json, menu_text_csv = build_menu_text_output_paths(label, DATA_DIR)
    menu_items_json, menu_items_csv = build_menu_item_output_paths(label, DATA_DIR)
    menu_validation_json = _build_local_validation_output_path(label)
    write_menu_sources_json(menu_sources_json, menu_sources)
    write_menu_sources_csv(menu_sources_csv, menu_sources)
    write_menu_text_json(menu_text_json, menu_text)
    write_menu_text_csv(menu_text_csv, menu_text)
    write_menu_items_json(menu_items_json, menu_items)
    write_menu_items_csv(menu_items_csv, menu_items)
    _write_menu_validation_json(
        path=menu_validation_json,
        restaurant_name=restaurant_name,
        validation_summary=validation_summary,
        menu_items=all_menu_item_payloads,
        rejected_menu_items=rejected_menu_items,
    )

    return {
        "restaurantName": restaurant_name,
        "websiteUrl": website_url,
        "websiteRecovery": website_recovery,
        "menuSources": [_safe_payload(row) for row in menu_sources],
        "menuText": [_safe_payload(row) for row in menu_text],
        "menuItems": displayed_menu_items,
        "rejectedMenuItems": rejected_menu_items,
        "summary": _menu_summary(
            menu_sources,
            menu_text,
            displayed_menu_items,
            parsed_item_count=len(menu_items),
            rejected_items=rejected_menu_items,
            validation_summary=validation_summary,
            menu_source_errors=menu_source_errors,
            website_url=website_url,
            website_recovery=website_recovery,
            brave_fallback_used=brave_fallback_used,
            restaurant_payload=payload,
        ),
        "files": {
            "menuSourcesJson": str(menu_sources_json),
            "menuSourcesCsv": str(menu_sources_csv),
            "menuTextJson": str(menu_text_json),
            "menuTextCsv": str(menu_text_csv),
            "menuItemsJson": str(menu_items_json),
            "menuItemsCsv": str(menu_items_csv),
            "menuValidationJson": str(menu_validation_json),
        },
    }


def _discover_menu_sources_for_website(
    *,
    website_url: str,
    restaurant_name: str,
    restaurant_source_id: str,
    user_agent: str,
    address: str,
    errors: list[dict[str, str]],
):
    try:
        return discover_menu_sources_for_url(
            website_url=website_url,
            restaurant_name=restaurant_name,
            restaurant_source_id=restaurant_source_id,
            user_agent=user_agent,
            limit=12,
            validate=True,
            include_ordering_pages=True,
            include_images=True,
            crawl_depth=2,
            use_sitemap=True,
            location_hint=address or restaurant_name,
            fetch_mode="static",
            # If the site has no allergen page, let the seeker search the web for
            # an allergen PDF (Brave). No-op when no Brave key is configured.
            brave_api_key=get_brave_search_api_key(),
        )
    except MenuSourceError as exc:
        errors.append({"source": "website_crawl", "error": str(exc)})
        return []


def _restaurant_record_from_menu_payload(
    *,
    restaurant_name: str,
    restaurant_source_id: str,
    website_url: str,
    address: str,
    phone_number: str,
    categories: list[str],
    payload: dict[str, Any],
) -> RestaurantRecord:
    return RestaurantRecord(
        name=restaurant_name,
        address=address or None,
        latitude=_optional_float(payload.get("latitude")) or 0.0,
        longitude=_optional_float(payload.get("longitude")) or 0.0,
        distance_meters=_optional_float(payload.get("distanceMeters")) or 0.0,
        rating=_optional_float(payload.get("rating")),
        review_count=_optional_int(payload.get("reviewCount")),
        price_level=str(payload.get("priceLevel") or "").strip() or None,
        categories=categories,
        website_url=website_url or None,
        phone_number=phone_number or None,
        opening_hours=None,
        business_status=str(payload.get("businessStatus") or "").strip() or None,
        is_open_now=None,
        service_options={},
        source_last_updated=None,
        data_quality_score=0.0,
        source_name=str(payload.get("sourceName") or "").strip() or "local_app",
        source_id=restaurant_source_id,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        raw_payload={},
    )


def _validate_menu_item_payloads(
    *,
    restaurant_name: str,
    restaurant_source_id: str,
    menu_items: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    payloads = _menu_item_payloads(menu_items)
    validation_summary = _empty_validation_summary()
    validation_summary["candidateRows"] = len(payloads)

    api_key = get_gemini_api_key()
    if not api_key or not payloads:
        for payload in payloads:
            payload.update(
                {
                    "llm_validation_status": "not_configured"
                    if not api_key
                    else "no_candidates",
                    "llm_validated": False,
                    "llm_is_menu_item": None,
                    "llm_confidence": None,
                    "llm_rejection_reason": "",
                    "llm_evidence_quote": "",
                }
            )
        return payloads, [], payloads, validation_summary

    validation_summary["enabled"] = True
    validation_summary["model"] = get_gemini_model()
    validation_models = _gemini_validation_models()
    validation_summary["fallbackModels"] = validation_models[1:]
    validations_by_id: dict[str, dict[str, Any]] = {}
    validation_warnings: list[str] = []
    attempt_errors: list[dict[str, str]] = []
    try:
        for chunk in _chunks(
            _menu_validation_candidates(payloads),
            GEMINI_MENU_VALIDATION_CHUNK_SIZE,
        ):
            result, model_used, chunk_errors = _validate_gemini_chunk_with_fallback(
                restaurant_name=restaurant_name,
                restaurant_source_id=restaurant_source_id,
                candidates=chunk,
                api_key=api_key,
                models=validation_models,
            )
            validation_summary["modelUsed"] = model_used
            attempt_errors.extend(chunk_errors)
            validation = result.validation
            validation_warnings.extend(
                str(warning)
                for warning in validation.get("validation_warnings", [])
                if str(warning).strip()
            )
            for row in validation.get("validations", []):
                if not isinstance(row, dict):
                    continue
                candidate_id = str(row.get("candidate_id") or "").strip()
                if candidate_id:
                    validations_by_id[candidate_id] = row
    except GeminiMenuError as exc:
        validation_summary["error"] = str(exc)
        for payload in payloads:
            payload.update(
                {
                    "llm_validation_status": "error",
                    "llm_validated": False,
                    "llm_is_menu_item": None,
                    "llm_confidence": None,
                    "llm_rejection_reason": "",
                    "llm_evidence_quote": "",
                }
            )
        return payloads, [], payloads, validation_summary

    for payload in payloads:
        validation = validations_by_id.get(payload["candidate_id"])
        if not validation:
            payload.update(
                {
                    "llm_validation_status": "missing",
                    "llm_validated": False,
                    "llm_is_menu_item": None,
                    "llm_confidence": None,
                    "llm_rejection_reason": "",
                    "llm_evidence_quote": "",
                }
            )
            continue

        is_menu_item = validation.get("is_menu_item")
        if not isinstance(is_menu_item, bool):
            is_menu_item = None
        payload.update(
            {
                "llm_validation_status": "accepted"
                if is_menu_item is True
                else "rejected"
                if is_menu_item is False
                else "uncertain",
                "llm_validated": True,
                "llm_is_menu_item": is_menu_item,
                "llm_confidence": validation.get("confidence"),
                "llm_rejection_reason": str(
                    validation.get("rejection_reason") or ""
                ).strip(),
                "llm_evidence_quote": str(
                    validation.get("evidence_quote") or ""
                ).strip(),
            }
        )

    displayed = [
        payload
        for payload in payloads
        if payload.get("llm_is_menu_item") is not False
    ]
    rejected = [
        payload
        for payload in payloads
        if payload.get("llm_is_menu_item") is False
    ]
    validation_summary.update(
        {
            "validatedRows": sum(
                1 for payload in payloads if payload.get("llm_validated")
            ),
            "acceptedRows": sum(
                1 for payload in payloads if payload.get("llm_is_menu_item") is True
            ),
            "rejectedRows": len(rejected),
            "missingRows": sum(
                1
                for payload in payloads
                if payload.get("llm_validation_status") == "missing"
            ),
            "warnings": validation_warnings,
            "attemptErrors": attempt_errors,
        }
    )
    return displayed, rejected, payloads, validation_summary


def _validate_gemini_chunk_with_fallback(
    *,
    restaurant_name: str,
    restaurant_source_id: str,
    candidates: list[dict[str, Any]],
    api_key: str,
    models: list[str],
) -> tuple[Any, str, list[dict[str, str]]]:
    if not models:
        raise GeminiMenuError("No Gemini validation models are configured.")

    attempt_errors: list[dict[str, str]] = []
    last_error: GeminiMenuError | None = None
    for model in models:
        try:
            result = validate_menu_candidates_with_gemini(
                restaurant_name=restaurant_name,
                restaurant_source_id=restaurant_source_id,
                candidates=candidates,
                api_key=api_key,
                model=model,
            )
            return result, model, attempt_errors
        except GeminiMenuError as exc:
            last_error = exc
            message = str(exc)
            attempt_errors.append({"model": model, "error": message})
            if not _is_gemini_model_fallback_error(message):
                raise

    assert last_error is not None
    raise last_error


def _menu_validation_candidates(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": payload["candidate_id"],
            "source_url": payload.get("menu_source_url", ""),
            "source_type": payload.get("source_type", ""),
            "extraction_method": payload.get("extraction_method", ""),
            "rule_parser_confidence": payload.get("confidence", 0),
            "category": payload.get("category", ""),
            "item_name": payload.get("item_name", ""),
            "description": payload.get("description", ""),
            "price": payload.get("price", ""),
            "raw_text": payload.get("raw_text", ""),
        }
        for payload in payloads
    ]


def _gemini_validation_models() -> list[str]:
    models: list[str] = []
    for model in [get_gemini_model(), *get_gemini_fallback_models()]:
        cleaned = model.strip()
        if cleaned and cleaned not in models:
            models.append(cleaned)
    return models


def _build_local_validation_output_path(label: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H%M%S")
    return DATA_DIR / f"menu_validation_{_slugify(label)}_{stamp}.json"


def _write_menu_validation_json(
    *,
    path: Path,
    restaurant_name: str,
    validation_summary: dict[str, Any],
    menu_items: list[dict[str, Any]],
    rejected_menu_items: list[dict[str, Any]],
) -> None:
    path.write_text(
        json.dumps(
            {
                "restaurantName": restaurant_name,
                "validationSummary": validation_summary,
                "menuItems": menu_items,
                "rejectedMenuItems": rejected_menu_items,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _dedupe_menu_sources(rows: list[Any]) -> list[Any]:
    best_by_url: dict[str, Any] = {}
    for row in rows:
        candidate_url = str(getattr(row, "candidate_url", "") or "").strip()
        if not candidate_url:
            continue
        existing = best_by_url.get(candidate_url)
        if existing is None or getattr(row, "confidence", 0) > getattr(
            existing, "confidence", 0
        ):
            best_by_url[candidate_url] = row

    return sorted(
        best_by_url.values(),
        key=lambda row: (
            str(getattr(row, "evidence_grade", "Z") or "Z"),
            -float(getattr(row, "confidence", 0) or 0),
        ),
    )
