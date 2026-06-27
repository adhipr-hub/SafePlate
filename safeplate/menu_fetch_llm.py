"""Option A: let Gemini fetch and read a menu URL, returning structured items.

Used only as a fallback when the deterministic parser and the embedded-JSON
scan both come up empty for a validated HTML source. Gemini's ``url_context``
tool fetches the page server-side, so this recovers menus our static fetch
cannot read -- at a few cents per restaurant. Results are cached on disk by URL.

Guardrails: every item must carry a verbatim ``evidence_quote``; these records
are tagged ``gemini_url_context`` and confidence-capped below verified static
HTML, because an LLM read is weaker evidence than parsed source text.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from safeplate.config import get_cache_dir
from safeplate.gemini_menu import (
    GeminiMenuError,
    _parse_gemini_json_response,
    _post_gemini_generate_content,
)
from safeplate.menu_text import MenuItemRecord

DEFAULT_MODEL = "gemini-3.1-flash-lite"
_CACHE_TTL_SECONDS = 14 * 24 * 60 * 60
_MAX_CONFIDENCE = 0.65  # LLM-read items never outrank verified static-HTML items.

URL_MENU_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_had_menu": {"type": "boolean"},
        "menu_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_name": {"type": "string"},
                    "category": {"type": "string"},
                    "description": {"type": "string"},
                    "price": {"type": "string"},
                    "dietary_tags": {"type": "array", "items": {"type": "string"}},
                    "allergen_mentions": {"type": "array", "items": {"type": "string"}},
                    "evidence_quote": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["item_name", "evidence_quote"],
            },
        },
    },
    "required": ["page_had_menu", "menu_items"],
}

SYSTEM_INSTRUCTION = (
    "You read a single restaurant web page and extract ONLY the menu items that "
    "actually appear on it. Never invent items, prices, ingredients, allergens, "
    "or dietary labels. For every item, copy an exact verbatim evidence_quote from "
    "the page. If the page shows no menu, set page_had_menu to false and return an "
    "empty menu_items list."
)


def extract_items_via_gemini_url(
    url: str,
    *,
    restaurant_name: str = "",
    restaurant_source_id: str = "",
    api_key: str | None,
    model: str = DEFAULT_MODEL,
    use_cache: bool = True,
) -> list[MenuItemRecord]:
    """Best-effort LLM menu recovery. Returns [] on any failure or missing key."""
    if not api_key or not url:
        return []

    parsed = _load_cache(url) if use_cache else None
    if parsed is None:
        try:
            response = _post_gemini_generate_content(
                payload=_build_payload(url), api_key=api_key, model=model
            )
            parsed = _parse_gemini_json_response(response)
        except GeminiMenuError:
            return []
        if use_cache:
            _save_cache(url, parsed)

    return _records_from_payload(parsed, url, restaurant_name, restaurant_source_id)


IMAGE_SYSTEM_INSTRUCTION = (
    "You are reading a photo or scan of a restaurant menu. Extract ONLY the menu "
    "items visible in the image. Never invent items, prices, or allergens. For "
    "each item, copy the exact visible text into evidence_quote. If the image is "
    "not a menu, set page_had_menu to false and return an empty list."
)


def extract_items_via_gemini_image(
    image_bytes: bytes,
    *,
    content_type: str,
    restaurant_name: str = "",
    restaurant_source_id: str = "",
    api_key: str | None,
    model: str = DEFAULT_MODEL,
    cache_key: str = "",
    use_cache: bool = True,
) -> list[MenuItemRecord]:
    """Read an image menu with Gemini vision. Returns [] on missing key/failure."""
    if not api_key or not image_bytes:
        return []
    key = cache_key or hashlib.sha1(image_bytes).hexdigest()
    parsed = _load_cache(f"img:{key}") if use_cache else None
    if parsed is None:
        mime = content_type.split(";", 1)[0].strip() or "image/jpeg"
        payload = {
            "system_instruction": {"parts": [{"text": IMAGE_SYSTEM_INSTRUCTION}]},
            "contents": [{"parts": [
                {"text": "Extract the menu items from this image."},
                {"inline_data": {"mime_type": mime,
                                 "data": base64.b64encode(image_bytes).decode("ascii")}},
            ]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseJsonSchema": URL_MENU_SCHEMA,
            },
        }
        try:
            response = _post_gemini_generate_content(payload=payload, api_key=api_key, model=model)
            parsed = _parse_gemini_json_response(response)
        except GeminiMenuError:
            return []
        if use_cache:
            _save_cache(f"img:{key}", parsed)
    return _records_from_payload(parsed, "", restaurant_name, restaurant_source_id,
                                 extraction_method="gemini_image")


ALLERGEN_MATRIX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dish": {"type": "string"},
                    "allergens": {"type": "array", "items": {"type": "string"}},
                    # Many charts list an orderable dish, then its component ingredient
                    # sub-rows. Mark those so the pipeline can fold them into the dish.
                    "is_component": {"type": "boolean"},
                    "of_dish": {"type": "string"},
                },
                "required": ["dish", "allergens"],
            },
        }
    },
    "required": ["rows"],
}

ALLERGEN_MATRIX_SYSTEM = (
    "You read a restaurant ALLERGEN MATRIX image: rows are dishes, columns are "
    "allergens (peanut, tree nut, milk/dairy, egg, soy, wheat/gluten, fish, "
    "shellfish/crustacean, sesame, mustard, celery, sulphites, lupin, molluscs). "
    "For each row, output the dish name and the list of allergens whose cell is marked "
    "present (X, tick, dot, filled cell, or icon). List ONLY allergens actually marked "
    "for that row. Never invent dishes or allergens.\n"
    "IMPORTANT -- components vs dishes: many charts list an ORDERABLE menu item (e.g. "
    "'ShackBurger') immediately followed by its COMPONENT ingredient rows (e.g. 'Burger "
    "Patty', 'American Cheese', 'ShackSauce', 'Bun'), often indented or grouped under "
    "it. For each such component row set is_component=true and of_dish to the exact name "
    "of the orderable dish it belongs to. Top-level orderable items have "
    "is_component=false. Still report every row's allergens either way. "
    "If the image is not an allergen grid, return an empty rows list."
)


# Render the same page span the text-grid parser reads (_MATRIX_PDF_MAX_PAGES).
# The old default of 6 silently dropped allergen rows on pages 7+ of long chain
# matrices -- a safety-asymmetric recall loss. The per-page fallback dedupes, so a
# higher cap never double-counts dishes.
_MATRIX_VISION_MAX_PAGES = 25


def extract_allergen_matrix_via_gemini_pdf(
    pdf_bytes: bytes,
    *,
    restaurant_name: str = "",
    restaurant_source_id: str = "",
    api_key: str | None,
    model: str = DEFAULT_MODEL,
    max_pages: int = _MATRIX_VISION_MAX_PAGES,
) -> list[MenuItemRecord]:
    """Render an allergen-matrix PDF and read its dish x allergen grid with Gemini.

    Robust to rotated/icon column headers and image PDFs that table parsers miss.
    Returns [] on missing key/renderer/failure.
    """
    if not api_key or not pdf_bytes:
        return []
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception:
        return []

    records: list[MenuItemRecord] = []
    seen: set[str] = set()
    try:
        _render_matrix_pages(pdf, max_pages, api_key, model, records, seen,
                             restaurant_name, restaurant_source_id)
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return records


def _render_matrix_pages(pdf, max_pages, api_key, model, records, seen,
                         restaurant_name, restaurant_source_id) -> None:
    import io

    images: list[bytes] = []
    for page_index in range(min(len(pdf), max_pages)):
        try:
            image = pdf[page_index].render(scale=2.2).to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            images.append(buffer.getvalue())
        except Exception:
            continue
    if not images:
        return records

    # Cost: try ONE batched multi-image call instead of one per page. Accuracy: if
    # the model TRUNCATES (large matrix) or the call fails, fall back to per-page so
    # no dishes are ever lost. Single-page PDFs go straight to the one-image path.
    if len(images) > 1:
        try:
            rows, truncated = _matrix_call(_matrix_images_payload(images), api_key, model)
            if rows and not truncated:
                _absorb_matrix_rows(rows, records, seen, restaurant_name, restaurant_source_id)
                return records
        except Exception:
            pass

    for image_bytes in images:
        try:
            rows, _truncated = _matrix_call(_matrix_image_payload(image_bytes), api_key, model)
        except Exception:
            continue
        _absorb_matrix_rows(rows, records, seen, restaurant_name, restaurant_source_id)
    return records


def _matrix_call(payload: dict[str, Any], api_key: str, model: str):
    """Return (rows, truncated). `truncated` flags a MAX_TOKENS cut-off so the
    caller can fall back to per-page extraction."""
    response = _post_gemini_generate_content(payload=payload, api_key=api_key, model=model)
    truncated = any(
        (candidate.get("finishReason") or "") == "MAX_TOKENS"
        for candidate in response.get("candidates", [])
    )
    rows = _parse_gemini_json_response(response).get("rows", [])
    return rows, truncated


def _absorb_matrix_rows(rows, records, seen, restaurant_name, restaurant_source_id) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        dish = str(row.get("dish", "")).strip()
        allergens = [str(a).strip() for a in (row.get("allergens") or []) if str(a).strip()]
        if not dish or dish.lower() in seen:
            continue
        seen.add(dish.lower())
        is_component = bool(row.get("is_component"))
        parent_item = str(row.get("of_dish", "")).strip() if is_component else ""
        records.append(
            MenuItemRecord(
                restaurant_name=restaurant_name,
                restaurant_source_id=restaurant_source_id,
                menu_source_url="",
                category="",
                item_name=dish,
                description="",
                price="",
                dietary_terms=[],
                allergen_terms=allergens,
                source_type="",
                extraction_method="gemini_allergen_matrix",
                confidence=min(_MAX_CONFIDENCE, 0.6),
                raw_text=(f"{dish} contains {', '.join(allergens)}" if allergens else dish),
                fetched_at="",
                is_component=is_component,
                parent_item=parent_item,
            )
        )


def _matrix_image_payload(image_bytes: bytes) -> dict[str, Any]:
    return {
        "system_instruction": {"parts": [{"text": ALLERGEN_MATRIX_SYSTEM}]},
        "contents": [{"parts": [
            {"text": "Extract the dish x allergen grid from this image."},
            {"inline_data": {"mime_type": "image/png",
                             "data": base64.b64encode(image_bytes).decode("ascii")}},
        ]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": ALLERGEN_MATRIX_SCHEMA,
        },
    }


def _matrix_images_payload(images: list[bytes]) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [
        {"text": "Extract the dish x allergen grid from these menu pages. Combine "
                 "ALL pages into one complete list of dishes; do not omit any."}
    ]
    for image_bytes in images:
        parts.append({"inline_data": {"mime_type": "image/png",
                                      "data": base64.b64encode(image_bytes).decode("ascii")}})
    return {
        "system_instruction": {"parts": [{"text": ALLERGEN_MATRIX_SYSTEM}]},
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": ALLERGEN_MATRIX_SCHEMA,
            "maxOutputTokens": 8192,
        },
    }


def _build_payload(url: str) -> dict[str, Any]:
    prompt = (
        f"Read the restaurant menu at this URL and extract its menu items:\n{url}"
    )
    return {
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"url_context": {}}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": URL_MENU_SCHEMA,
        },
    }


def _records_from_payload(
    parsed: dict[str, Any],
    url: str,
    restaurant_name: str,
    restaurant_source_id: str,
    extraction_method: str = "gemini_url_context",
) -> list[MenuItemRecord]:
    records: list[MenuItemRecord] = []
    for item in parsed.get("menu_items", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("item_name", "")).strip()
        quote = str(item.get("evidence_quote", "")).strip()
        # Enforce the evidence-quote guardrail: no quote, no record.
        if not name or not quote:
            continue
        confidence = item.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.5
        records.append(
            MenuItemRecord(
                restaurant_name=restaurant_name,
                restaurant_source_id=restaurant_source_id,
                menu_source_url=url,
                category=str(item.get("category", "")).strip(),
                item_name=name,
                description=str(item.get("description", "")).strip(),
                price=str(item.get("price", "")).strip(),
                dietary_terms="; ".join(_as_terms(item.get("dietary_tags"))),
                allergen_terms="; ".join(_as_terms(item.get("allergen_mentions"))),
                source_type="",
                extraction_method=extraction_method,
                confidence=min(_MAX_CONFIDENCE, max(0.0, confidence)),
                raw_text=quote,
                fetched_at="",
            )
        )
    return records


def _as_terms(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(term).strip() for term in value if str(term).strip()]


def _cache_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return get_cache_dir() / "llm_menu" / f"{digest}.json"


def _load_cache(url: str) -> dict[str, Any] | None:
    path = _cache_path(url)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - payload.get("fetched_at", 0) > _CACHE_TTL_SECONDS:
        return None
    extraction = payload.get("extraction")
    return extraction if isinstance(extraction, dict) else None


def _save_cache(url: str, extraction: dict[str, Any]) -> None:
    path = _cache_path(url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"fetched_at": time.time(), "extraction": extraction}),
            encoding="utf-8",
        )
    except OSError:
        pass
