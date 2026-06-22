from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any

from safeplate.http_client import HttpConnectionError, http_post
from safeplate.coerce import float_value as _float_value
from safeplate.coerce import int_value as _int_value
from safeplate.coerce import split_semicolon_terms as _split_terms
from safeplate.io import timestamped_output_paths
from safeplate.io import write_dataclass_json


GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

GEMINI_MENU_SYSTEM_INSTRUCTION = """
You are SafePlate's menu evidence extraction engine.

Extract only information explicitly present in the provided restaurant evidence.
Do not invent menu items, prices, ingredients, allergens, dietary labels, or safety claims.
Extract every clearly stated menu item you can identify, not only allergy-relevant
or dietary-relevant highlights. Include ordinary menu items too when name and price
or description are stated.
If a field is not stated, use an empty string, empty array, false, or low confidence.
Preserve uncertainty. If a dish may contain an allergen but the evidence is indirect,
record it as an allergen mention with a cautious note, not as a guaranteed fact.
Always include a short evidence_quote for each extracted item or note.
Prefer exact wording from the source for allergy disclaimers, cross-contact warnings,
server-notification instructions, vegan/vegetarian/gluten-free claims, tofu/substitution
options, and modification statements.
This is evidence extraction, not medical advice and not a safety guarantee.
Return JSON only.
""".strip()

GEMINI_CANDIDATE_SYSTEM_INSTRUCTION = """
You are SafePlate's menu candidate cleanup and evidence extraction engine.

Extract only information explicitly present in the provided candidate rows and
restaurant context. Do not invent menu items, prices, ingredients, allergens,
dietary labels, or safety claims.
The candidate rows are produced by a deterministic parser. Return exactly one
menu_items object for every provided candidate_id, using that same candidate_id.
Do not create extra menu_items objects. Do not omit candidate_ids.
If a candidate is not a real menu item, set is_menu_item to false and explain
briefly in cleanup_notes.
Use restaurant context only for restaurant_notes and restaurant_signals, not for
creating additional menu_items.
Always include a short evidence_quote for each extracted item or note.
This is evidence extraction, not medical advice and not a safety guarantee.
Return JSON only.
""".strip()

GEMINI_CANDIDATE_VALIDATION_SYSTEM_INSTRUCTION = """
You are SafePlate's menu candidate validation engine.

Your only job is to decide whether each deterministic parser candidate is a
real restaurant menu item, drink, add-on, modifier, topping, side, size option,
or other sellable menu option.
Use only the provided candidate row text. Do not add menu items. Do not rewrite
names, prices, descriptions, ingredients, allergens, dietary labels, or safety
claims. Do not infer hidden ingredients or allergy risk.
Return exactly one validation object for every provided candidate_id.
Set is_menu_item to false for hiring/jobs text, hours, policies, delivery app
UI, cart/checkout text, newsletter text, navigation, marketing blurbs, generic
restaurant descriptions, locations, unrelated events, or fragments that are not
sellable menu options.
If uncertain, keep is_menu_item true only when the row has a plausible
food/drink/menu-option name and either a price, description, or clear menu
category context. Otherwise set false and explain briefly.
Return JSON only.
""".strip()

MENU_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "restaurant_name": {"type": "string"},
        "menu_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "item_name": {"type": "string"},
                    "description": {"type": "string"},
                    "price": {"type": "string"},
                    "currency": {"type": "string"},
                    "dietary_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "allergen_mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "modification_options": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_quote": {"type": "string"},
                    "source_url": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "category",
                    "item_name",
                    "description",
                    "price",
                    "currency",
                    "dietary_tags",
                    "allergen_mentions",
                    "modification_options",
                    "evidence_quote",
                    "source_url",
                    "confidence",
                ],
            },
        },
        "restaurant_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "note_type": {
                        "type": "string",
                        "enum": [
                            "allergy_disclaimer",
                            "cross_contact_warning",
                            "staff_instruction",
                            "ingredient_warning",
                            "dietary_option",
                            "modification_option",
                            "menu_policy",
                            "other",
                        ],
                    },
                    "note_text": {"type": "string"},
                    "applies_to": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_quote": {"type": "string"},
                    "source_url": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "note_type",
                    "note_text",
                    "applies_to",
                    "evidence_quote",
                    "source_url",
                    "confidence",
                ],
            },
        },
        "restaurant_signals": {
            "type": "object",
            "properties": {
                "has_allergy_disclaimer": {"type": "boolean"},
                "has_cross_contact_warning": {"type": "boolean"},
                "mentions_staff_allergy_instruction": {"type": "boolean"},
                "has_vegan_options": {"type": "boolean"},
                "has_vegetarian_options": {"type": "boolean"},
                "has_gluten_free_options": {"type": "boolean"},
                "has_tofu_or_plant_protein_options": {"type": "boolean"},
                "evidence_confidence": {"type": "number"},
            },
            "required": [
                "has_allergy_disclaimer",
                "has_cross_contact_warning",
                "mentions_staff_allergy_instruction",
                "has_vegan_options",
                "has_vegetarian_options",
                "has_gluten_free_options",
                "has_tofu_or_plant_protein_options",
                "evidence_confidence",
            ],
        },
        "extraction_warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "restaurant_name",
        "menu_items",
        "restaurant_notes",
        "restaurant_signals",
        "extraction_warnings",
    ],
}

CANDIDATE_MENU_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "restaurant_name": {"type": "string"},
        "menu_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "is_menu_item": {"type": "boolean"},
                    "category": {"type": "string"},
                    "item_name": {"type": "string"},
                    "description": {"type": "string"},
                    "price": {"type": "string"},
                    "currency": {"type": "string"},
                    "dietary_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "allergen_mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "modification_options": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_quote": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_type": {"type": "string"},
                    "extraction_method": {"type": "string"},
                    "rule_parser_confidence": {"type": "number"},
                    "raw_text": {"type": "string"},
                    "cleanup_notes": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "candidate_id",
                    "is_menu_item",
                    "category",
                    "item_name",
                    "description",
                    "price",
                    "currency",
                    "dietary_tags",
                    "allergen_mentions",
                    "modification_options",
                    "evidence_quote",
                    "source_url",
                    "source_type",
                    "extraction_method",
                    "rule_parser_confidence",
                    "raw_text",
                    "cleanup_notes",
                    "confidence",
                ],
            },
        },
        "restaurant_notes": MENU_EVIDENCE_SCHEMA["properties"]["restaurant_notes"],
        "restaurant_signals": MENU_EVIDENCE_SCHEMA["properties"]["restaurant_signals"],
        "extraction_warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "restaurant_name",
        "menu_items",
        "restaurant_notes",
        "restaurant_signals",
        "extraction_warnings",
    ],
}

MENU_CANDIDATE_VALIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "restaurant_name": {"type": "string"},
        "validations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "is_menu_item": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "rejection_reason": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                },
                "required": [
                    "candidate_id",
                    "is_menu_item",
                    "confidence",
                    "rejection_reason",
                    "evidence_quote",
                ],
            },
        },
        "validation_warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "restaurant_name",
        "validations",
        "validation_warnings",
    ],
}

MENU_EVIDENCE_ITEM_CSV_FIELDS = [
    "restaurant_name",
    "candidate_id",
    "is_menu_item",
    "category",
    "item_name",
    "description",
    "price",
    "currency",
    "dietary_tags",
    "allergen_mentions",
    "modification_options",
    "evidence_quote",
    "source_url",
    "source_type",
    "extraction_method",
    "rule_parser_confidence",
    "raw_text",
    "cleanup_notes",
    "confidence",
]

MENU_EVIDENCE_NOTE_CSV_FIELDS = [
    "restaurant_name",
    "note_type",
    "note_text",
    "applies_to",
    "evidence_quote",
    "source_url",
    "confidence",
]


@dataclass(frozen=True)
class GeminiEvidenceSource:
    menu_source_url: str
    source_type: str
    extraction_method: str
    char_count: int
    price_count: int
    extracted_text: str


@dataclass(frozen=True)
class GeminiRestaurantEvidence:
    restaurant_name: str
    restaurant_source_id: str
    model: str
    extracted_at: str
    evidence_sources: list[dict[str, Any]]
    extraction: dict[str, Any]


@dataclass(frozen=True)
class GeminiCandidateValidation:
    restaurant_name: str
    restaurant_source_id: str
    model: str
    validated_at: str
    validation: dict[str, Any]


class GeminiMenuError(RuntimeError):
    """Raised when Gemini menu extraction fails."""


def extract_restaurant_evidence_with_gemini(
    *,
    restaurant_name: str,
    restaurant_source_id: str,
    sources: list[GeminiEvidenceSource],
    api_key: str,
    model: str,
    max_chars: int,
    require_grounded_quotes: bool = True,
) -> GeminiRestaurantEvidence:
    prompt = build_menu_evidence_prompt(
        restaurant_name=restaurant_name,
        sources=sources,
        max_chars=max_chars,
    )
    payload = _build_gemini_payload(prompt)
    response_payload = _post_gemini_generate_content(
        payload=payload,
        api_key=api_key,
        model=model,
    )
    extraction = _parse_gemini_json_response(response_payload)
    extraction.setdefault("restaurant_name", restaurant_name)
    extraction = drop_ungrounded_evidence(
        extraction,
        " ".join(source.extracted_text for source in sources),
        require_grounded_quotes=require_grounded_quotes,
    )

    return GeminiRestaurantEvidence(
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
        model=model,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        evidence_sources=[
            {
                "menu_source_url": source.menu_source_url,
                "source_type": source.source_type,
                "extraction_method": source.extraction_method,
                "char_count": source.char_count,
                "price_count": source.price_count,
            }
            for source in sources
        ],
        extraction=extraction,
    )


def extract_restaurant_candidate_evidence_with_gemini(
    *,
    restaurant_name: str,
    restaurant_source_id: str,
    candidates: list[dict[str, Any]],
    context_sources: list[GeminiEvidenceSource],
    api_key: str,
    model: str,
    max_context_chars: int,
    require_grounded_quotes: bool = True,
) -> GeminiRestaurantEvidence:
    prompt = build_candidate_evidence_prompt(
        restaurant_name=restaurant_name,
        candidates=candidates,
        context_sources=context_sources,
        max_context_chars=max_context_chars,
    )
    payload = _build_gemini_payload(
        prompt,
        schema=CANDIDATE_MENU_EVIDENCE_SCHEMA,
        system_instruction=GEMINI_CANDIDATE_SYSTEM_INSTRUCTION,
    )
    response_payload = _post_gemini_generate_content(
        payload=payload,
        api_key=api_key,
        model=model,
    )
    extraction = _parse_gemini_json_response(response_payload)
    extraction.setdefault("restaurant_name", restaurant_name)
    # Ground against the context text AND the deterministic candidates' own text,
    # so legitimately rule-parsed items are never dropped for lacking context.
    candidate_text = " ".join(
        f"{c.get('item_name', '')} {c.get('description', '')} {c.get('raw_text', '')}"
        for c in candidates
    )
    source_text = (
        " ".join(source.extracted_text for source in context_sources)
        + " "
        + candidate_text
    )
    extraction = drop_ungrounded_evidence(
        extraction, source_text, require_grounded_quotes=require_grounded_quotes
    )

    return GeminiRestaurantEvidence(
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
        model=model,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        evidence_sources=[
            {
                "menu_source_url": source.menu_source_url,
                "source_type": source.source_type,
                "extraction_method": source.extraction_method,
                "char_count": source.char_count,
                "price_count": source.price_count,
            }
            for source in context_sources
        ],
        extraction=extraction,
    )


def validate_menu_candidates_with_gemini(
    *,
    restaurant_name: str,
    restaurant_source_id: str,
    candidates: list[dict[str, Any]],
    api_key: str,
    model: str,
) -> GeminiCandidateValidation:
    prompt = build_candidate_validation_prompt(
        restaurant_name=restaurant_name,
        candidates=candidates,
    )
    payload = _build_gemini_payload(
        prompt,
        schema=MENU_CANDIDATE_VALIDATION_SCHEMA,
        system_instruction=GEMINI_CANDIDATE_VALIDATION_SYSTEM_INSTRUCTION,
    )
    response_payload = _post_gemini_generate_content(
        payload=payload,
        api_key=api_key,
        model=model,
    )
    validation = _parse_gemini_json_response(response_payload)
    validation.setdefault("restaurant_name", restaurant_name)

    return GeminiCandidateValidation(
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
        model=model,
        validated_at=datetime.now(timezone.utc).isoformat(),
        validation=validation,
    )


def build_menu_evidence_prompt(
    *,
    restaurant_name: str,
    sources: list[GeminiEvidenceSource],
    max_chars: int,
) -> str:
    blocks = []
    remaining_chars = max_chars
    for index, source in enumerate(sources, start=1):
        if remaining_chars <= 0:
            break
        text = _trim_text(source.extracted_text, remaining_chars)
        remaining_chars -= len(text)
        blocks.append(
            "\n".join(
                [
                    f"--- SOURCE {index} ---",
                    f"url: {source.menu_source_url}",
                    f"source_type: {source.source_type}",
                    f"extraction_method: {source.extraction_method}",
                    "cleaned_visible_text:",
                    text,
                ]
            )
        )

    return "\n\n".join(
        [
            f"Restaurant: {restaurant_name}",
            "Extract structured menu evidence from the sources below.",
            "Focus on menu items, prices, dietary labels, allergen mentions, allergy disclaimers, cross-contact warnings, staff-notification guidance, and modification options.",
            "Extract as many clearly stated menu items as the evidence supports. Do not summarize the menu down to only notable safety examples.",
            "Use the source URL from the source block for each item or note.",
            "\n\n".join(blocks),
        ]
    )


def build_candidate_evidence_prompt(
    *,
    restaurant_name: str,
    candidates: list[dict[str, Any]],
    context_sources: list[GeminiEvidenceSource],
    max_context_chars: int,
) -> str:
    candidate_blocks = [
        "\n".join(
            [
                f"candidate_id: {candidate['candidate_id']}",
                f"source_url: {candidate.get('source_url', '')}",
                f"source_type: {candidate.get('source_type', '')}",
                f"extraction_method: {candidate.get('extraction_method', '')}",
                f"rule_parser_confidence: {candidate.get('rule_parser_confidence', 0)}",
                f"category: {candidate.get('category', '')}",
                f"item_name: {candidate.get('item_name', '')}",
                f"description: {candidate.get('description', '')}",
                f"price: {candidate.get('price', '')}",
                f"dietary_terms: {', '.join(candidate.get('dietary_terms', []))}",
                f"allergen_terms: {', '.join(candidate.get('allergen_terms', []))}",
                f"raw_text: {candidate.get('raw_text', '')}",
            ]
        )
        for candidate in candidates
    ]

    context_blocks = []
    remaining_chars = max_context_chars
    for index, source in enumerate(context_sources, start=1):
        if remaining_chars <= 0:
            break
        text = _trim_text(source.extracted_text, remaining_chars)
        remaining_chars -= len(text)
        context_blocks.append(
            "\n".join(
                [
                    f"--- CONTEXT SOURCE {index} ---",
                    f"url: {source.menu_source_url}",
                    f"source_type: {source.source_type}",
                    f"extraction_method: {source.extraction_method}",
                    "cleaned_visible_text:",
                    text,
                ]
            )
        )

    return "\n\n".join(
        [
            f"Restaurant: {restaurant_name}",
            "Clean and enrich the deterministic menu item candidates below.",
            "Return exactly one menu_items object for every candidate_id listed.",
            "Keep the same candidate_id values. Do not add menu_items that do not have a listed candidate_id.",
            "Use candidate raw_text as the item evidence_quote unless a shorter exact quote is better.",
            "Use restaurant context only for restaurant_notes and restaurant_signals.",
            "--- CANDIDATE ROWS ---",
            "\n\n".join(candidate_blocks),
            "--- RESTAURANT CONTEXT ---",
            "\n\n".join(context_blocks) if context_blocks else "No extra context provided.",
        ]
    )


def build_candidate_validation_prompt(
    *,
    restaurant_name: str,
    candidates: list[dict[str, Any]],
) -> str:
    candidate_blocks = [
        "\n".join(
            [
                f"candidate_id: {candidate['candidate_id']}",
                f"source_url: {candidate.get('source_url', '')}",
                f"source_type: {candidate.get('source_type', '')}",
                f"extraction_method: {candidate.get('extraction_method', '')}",
                f"rule_parser_confidence: {candidate.get('rule_parser_confidence', 0)}",
                f"category: {candidate.get('category', '')}",
                f"item_name: {candidate.get('item_name', '')}",
                f"description: {candidate.get('description', '')}",
                f"price: {candidate.get('price', '')}",
                f"raw_text: {candidate.get('raw_text', '')}",
            ]
        )
        for candidate in candidates
    ]

    return "\n\n".join(
        [
            f"Restaurant: {restaurant_name}",
            "Validate the deterministic parser candidates below.",
            "Return exactly one validations object for every candidate_id listed.",
            "Keep the same candidate_id values. Do not add candidates and do not rewrite candidate fields.",
            "Use the candidate raw_text as evidence_quote unless an exact shorter quote is better.",
            "--- CANDIDATE ROWS ---",
            "\n\n".join(candidate_blocks),
        ]
    )


def menu_text_rows_to_sources(rows: list[dict[str, str]]) -> list[GeminiEvidenceSource]:
    return [
        GeminiEvidenceSource(
            menu_source_url=row.get("menu_source_url", ""),
            source_type=row.get("source_type", ""),
            extraction_method=row.get("extraction_method", ""),
            char_count=_int_value(row.get("char_count")),
            price_count=_int_value(row.get("price_count")),
            extracted_text=row.get("extracted_text", ""),
        )
        for row in rows
        if row.get("extracted_text", "").strip()
    ]


def menu_item_rows_to_candidates(
    rows: list[dict[str, str]],
    *,
    max_candidates: int,
) -> list[dict[str, Any]]:
    candidates = []
    for index, row in enumerate(rows[:max_candidates], start=1):
        candidates.append(
            {
                "candidate_id": f"c{index:04d}",
                "source_url": row.get("menu_source_url", ""),
                "source_type": row.get("source_type", ""),
                "extraction_method": row.get("extraction_method", ""),
                "rule_parser_confidence": _float_value(row.get("confidence")),
                "category": row.get("category", ""),
                "item_name": row.get("item_name", ""),
                "description": row.get("description", ""),
                "price": row.get("price", ""),
                "dietary_terms": _split_terms(row.get("dietary_terms", "")),
                "allergen_terms": _split_terms(row.get("allergen_terms", "")),
                "raw_text": row.get("raw_text", ""),
            }
        )
    return candidates


def group_menu_item_rows_by_restaurant(
    rows: list[dict[str, str]],
) -> list[tuple[str, str, list[dict[str, str]]]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        restaurant_name = row.get("restaurant_name", "").strip()
        restaurant_source_id = row.get("restaurant_source_id", "").strip()
        if not restaurant_name:
            continue
        key = (restaurant_source_id, restaurant_name)
        groups.setdefault(key, []).append(row)
    return [
        (restaurant_name, restaurant_source_id, group_rows)
        for (restaurant_source_id, restaurant_name), group_rows in groups.items()
    ]


def group_menu_text_rows_by_restaurant(
    rows: list[dict[str, str]],
) -> list[tuple[str, str, list[dict[str, str]]]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        restaurant_name = row.get("restaurant_name", "").strip()
        restaurant_source_id = row.get("restaurant_source_id", "").strip()
        if not restaurant_name:
            continue
        key = (restaurant_source_id, restaurant_name)
        groups.setdefault(key, []).append(row)
    return [
        (restaurant_name, restaurant_source_id, group_rows)
        for (restaurant_source_id, restaurant_name), group_rows in groups.items()
    ]


def build_gemini_output_paths(label: str, out_dir: Path) -> tuple[Path, Path, Path]:
    json_path, items_path, notes_path = timestamped_output_paths(
        label,
        out_dir,
        "gemini_menu_evidence",
        (".json", "_items.csv", "_notes.csv"),
    )
    return json_path, items_path, notes_path


def write_gemini_evidence_json(
    path: Path,
    rows: list[GeminiRestaurantEvidence],
) -> None:
    write_dataclass_json(path, rows)


def write_gemini_items_csv(
    path: Path,
    rows: list[GeminiRestaurantEvidence],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=MENU_EVIDENCE_ITEM_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            for item in row.extraction.get("menu_items", []):
                writer.writerow(
                    {
                        "restaurant_name": row.restaurant_name,
                        "candidate_id": item.get("candidate_id", ""),
                        "is_menu_item": item.get("is_menu_item", ""),
                        "category": item.get("category", ""),
                        "item_name": item.get("item_name", ""),
                        "description": item.get("description", ""),
                        "price": item.get("price", ""),
                        "currency": item.get("currency", ""),
                        "dietary_tags": "; ".join(item.get("dietary_tags", [])),
                        "allergen_mentions": "; ".join(item.get("allergen_mentions", [])),
                        "modification_options": "; ".join(
                            item.get("modification_options", [])
                        ),
                        "evidence_quote": item.get("evidence_quote", ""),
                        "source_url": item.get("source_url", ""),
                        "source_type": item.get("source_type", ""),
                        "extraction_method": item.get("extraction_method", ""),
                        "rule_parser_confidence": item.get(
                            "rule_parser_confidence", ""
                        ),
                        "raw_text": item.get("raw_text", ""),
                        "cleanup_notes": item.get("cleanup_notes", ""),
                        "confidence": item.get("confidence", ""),
                    }
                )


def write_gemini_notes_csv(
    path: Path,
    rows: list[GeminiRestaurantEvidence],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=MENU_EVIDENCE_NOTE_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            for note in row.extraction.get("restaurant_notes", []):
                writer.writerow(
                    {
                        "restaurant_name": row.restaurant_name,
                        "note_type": note.get("note_type", ""),
                        "note_text": note.get("note_text", ""),
                        "applies_to": "; ".join(note.get("applies_to", [])),
                        "evidence_quote": note.get("evidence_quote", ""),
                        "source_url": note.get("source_url", ""),
                        "confidence": note.get("confidence", ""),
                    }
                )


def _build_gemini_payload(
    prompt: str,
    *,
    schema: dict[str, Any] = MENU_EVIDENCE_SCHEMA,
    system_instruction: str = GEMINI_MENU_SYSTEM_INSTRUCTION,
) -> dict[str, Any]:
    return {
        "system_instruction": {
            "parts": [{"text": system_instruction}],
        },
        "contents": [
            {
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }


# Global ceiling on concurrent Gemini calls, shared by EVERY caller (menu text +
# its parallel chunks, vision matrices, the list's per-restaurant extractions, the
# discovery link-select). Without this, parallelizing chunks/sources would multiply
# into the free-tier RPM wall that caused ~20% silent 429 failures earlier. Sized by
# SAFEPLATE_GEMINI_CONCURRENCY (default 4); created lazily so config/env is read once.
_GEMINI_SEM: threading.BoundedSemaphore | None = None
_GEMINI_SEM_LOCK = threading.Lock()


def _gemini_semaphore() -> threading.BoundedSemaphore:
    global _GEMINI_SEM
    if _GEMINI_SEM is None:
        with _GEMINI_SEM_LOCK:
            if _GEMINI_SEM is None:
                from safeplate.config import get_gemini_concurrency

                _GEMINI_SEM = threading.BoundedSemaphore(max(1, get_gemini_concurrency()))
    return _GEMINI_SEM


def _post_gemini_generate_content(
    *,
    payload: dict[str, Any],
    api_key: str,
    model: str,
) -> dict[str, Any]:
    # Routed through the pooled keep-alive Session in http_client: this chokepoint
    # is hit many times concurrently against one host per run, so reusing the
    # connection (and negotiating gzip on the large JSON responses) avoids a fresh
    # TLS handshake every call. The global semaphore still caps in-flight calls.
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    url = GEMINI_GENERATE_URL.format(model=model)
    try:
        with _gemini_semaphore():
            response = http_post(url, data=body, headers=headers, timeout=90)
    except HttpConnectionError as exc:
        raise GeminiMenuError(f"Gemini request failed: {exc}") from exc

    if response.status >= 400:
        details = response.content.decode("utf-8", errors="replace")
        raise GeminiMenuError(
            f"Gemini request failed with HTTP {response.status}: {details}"
        )
    try:
        return json.loads(response.content.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise GeminiMenuError("Gemini returned non-JSON data") from exc


def _parse_gemini_json_response(payload: dict[str, Any]) -> dict[str, Any]:
    text = _response_text(payload)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiMenuError(f"Gemini returned non-JSON text: {text[:500]}") from exc
    if not isinstance(parsed, dict):
        raise GeminiMenuError("Gemini returned JSON that was not an object")
    return parsed


def _response_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        texts = [part.get("text", "") for part in parts if part.get("text")]
        if texts:
            return "".join(texts).strip()
    raise GeminiMenuError("Gemini response did not include output text")


def _normalize_for_grounding(text: str) -> str:
    # Lowercase, straighten curly quotes, and strip ALL whitespace so the check
    # survives PDF letter-spacing ("f a c i l i t y") and quote-style differences.
    text = (text or "").lower()
    for curly, straight in (
        ("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"),
    ):
        text = text.replace(curly, straight)
    return re.sub(r"\s+", "", text)


_ELLIPSIS_RE = re.compile(r"\.\.\.|…")


def _contains(needle: str, normalized_source: str) -> bool:
    if not needle:
        return False
    if needle in normalized_source:
        return True
    return len(needle) > 40 and needle[:40] in normalized_source


def _is_quote_grounded(quote: str, normalized_source: str) -> bool:
    # Models join non-adjacent spans with "..."; verify each substantial fragment
    # rather than the whole reflowed string as one contiguous substring.
    fragments = [
        _normalize_for_grounding(part) for part in _ELLIPSIS_RE.split(quote or "")
    ]
    substantial = [frag for frag in fragments if len(frag) >= 8]
    if substantial:
        return any(_contains(frag, normalized_source) for frag in substantial)
    return _contains(_normalize_for_grounding(quote), normalized_source)


def _is_record_grounded(
    record: dict[str, Any], normalized_source: str, *, name_key: str | None
) -> bool:
    # For menu items, the dish name appearing in the source is the strongest
    # "this is a real item" signal (robust to quote reflow). Notes have no name,
    # so they rely on the quote alone.
    if name_key:
        name = _normalize_for_grounding(record.get(name_key, ""))
        if len(name) >= 6 and _contains(name, normalized_source):
            return True
    return _is_quote_grounded(record.get("evidence_quote", ""), normalized_source)


def drop_ungrounded_evidence(
    extraction: dict[str, Any],
    source_text: str,
    *,
    require_grounded_quotes: bool = True,
) -> dict[str, Any]:
    """Drop menu items / notes whose evidence_quote is not in the source text.

    This is the hard guardrail against LLM fabrication: every retained record's
    quote must be a verbatim substring of the text we actually scraped. If there
    is no source text to verify against, nothing is dropped (we do not guess).
    """
    if not require_grounded_quotes:
        return extraction
    normalized_source = _normalize_for_grounding(source_text)
    if not normalized_source:
        return extraction

    dropped_items = 0
    kept_items = []
    for item in extraction.get("menu_items", []):
        if isinstance(item, dict) and _is_record_grounded(
            item, normalized_source, name_key="item_name"
        ):
            kept_items.append(item)
        else:
            dropped_items += 1
    if "menu_items" in extraction:
        extraction["menu_items"] = kept_items

    dropped_notes = 0
    kept_notes = []
    for note in extraction.get("restaurant_notes", []):
        if isinstance(note, dict) and _is_record_grounded(
            note, normalized_source, name_key=None
        ):
            kept_notes.append(note)
        else:
            dropped_notes += 1
    if "restaurant_notes" in extraction:
        extraction["restaurant_notes"] = kept_notes

    if dropped_items or dropped_notes:
        extraction.setdefault("extraction_warnings", []).append(
            f"grounding guardrail dropped {dropped_items} item(s) and "
            f"{dropped_notes} note(s) whose evidence quote was not found in the "
            f"source menu text"
        )
    return extraction


def _trim_text(text: str, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rsplit(" ", 1)[0].strip()



