from __future__ import annotations

import unittest

from safeplate.gemini_menu import (
    CANDIDATE_MENU_EVIDENCE_SCHEMA,
    GEMINI_CANDIDATE_VALIDATION_SYSTEM_INSTRUCTION,
    GeminiEvidenceSource,
    MENU_CANDIDATE_VALIDATION_SCHEMA,
    _build_gemini_payload,
    _parse_gemini_json_response,
    build_candidate_evidence_prompt,
    build_candidate_validation_prompt,
    build_menu_evidence_prompt,
    drop_ungrounded_evidence,
    group_menu_item_rows_by_restaurant,
    group_menu_text_rows_by_restaurant,
    menu_item_rows_to_candidates,
)


class GroundingGuardrailTests(unittest.TestCase):
    def test_drops_item_whose_quote_is_not_in_source(self) -> None:
        extraction = {
            "menu_items": [
                {"item_name": "Pecan Pie", "evidence_quote": "Pecan Pie $9"},
                {"item_name": "Potted Dirt Tea", "evidence_quote": "Potted Dirt Tea $9.25"},
            ],
        }
        result = drop_ungrounded_evidence(extraction, "Desserts. Pecan Pie $9. Coffee $3.")
        names = [i["item_name"] for i in result["menu_items"]]
        self.assertEqual(names, ["Pecan Pie"])
        self.assertTrue(any("grounding guardrail dropped" in w for w in result["extraction_warnings"]))

    def test_keeps_real_quote_despite_pdf_spacing_and_curly_quotes(self) -> None:
        # Source mimics PDF letter-spacing + curly quotes; the quote must survive.
        source = 'p l e a s e   n o t e :  our food is made in a facility that contains nuts'
        extraction = {
            "restaurant_notes": [
                {"note_type": "allergy_disclaimer",
                 "evidence_quote": "our food is made in a facility that contains nuts"},
            ],
        }
        result = drop_ungrounded_evidence(extraction, source)
        self.assertEqual(len(result["restaurant_notes"]), 1)

    def test_empty_source_does_not_drop(self) -> None:
        extraction = {"menu_items": [{"item_name": "X", "evidence_quote": "X $1"}]}
        result = drop_ungrounded_evidence(extraction, "")
        self.assertEqual(len(result["menu_items"]), 1)

    def test_disabled_guardrail_keeps_everything(self) -> None:
        extraction = {"menu_items": [{"item_name": "Made up", "evidence_quote": "nope"}]}
        result = drop_ungrounded_evidence(extraction, "real menu text", require_grounded_quotes=False)
        self.assertEqual(len(result["menu_items"]), 1)


class GeminiMenuExtractionTests(unittest.TestCase):
    def test_builds_prompt_with_source_urls_and_cleaned_text(self) -> None:
        prompt = build_menu_evidence_prompt(
            restaurant_name="Example Cafe",
            sources=[
                GeminiEvidenceSource(
                    menu_source_url="https://example.com/menu",
                    source_type="website_link",
                    extraction_method="html_visible_text",
                    char_count=40,
                    price_count=1,
                    extracted_text="Vegan Bowl tofu, greens $14",
                )
            ],
            max_chars=200,
        )

        self.assertIn("Restaurant: Example Cafe", prompt)
        self.assertIn("https://example.com/menu", prompt)
        self.assertIn("Vegan Bowl tofu, greens $14", prompt)
        self.assertIn("Extract as many clearly stated menu items", prompt)

    def test_groups_menu_text_rows_by_restaurant(self) -> None:
        groups = group_menu_text_rows_by_restaurant(
            [
                {
                    "restaurant_name": "Example Cafe",
                    "restaurant_source_id": "abc",
                    "extracted_text": "Falafel $12",
                },
                {
                    "restaurant_name": "Example Cafe",
                    "restaurant_source_id": "abc",
                    "extracted_text": "Allergy note",
                },
            ]
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], "Example Cafe")
        self.assertEqual(groups[0][1], "abc")
        self.assertEqual(len(groups[0][2]), 2)

    def test_groups_menu_item_rows_by_restaurant(self) -> None:
        groups = group_menu_item_rows_by_restaurant(
            [
                {
                    "restaurant_name": "Example Cafe",
                    "restaurant_source_id": "abc",
                    "item_name": "Falafel",
                    "price": "$12",
                },
                {
                    "restaurant_name": "Example Cafe",
                    "restaurant_source_id": "abc",
                    "item_name": "Hummus",
                    "price": "$8",
                },
            ]
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], "Example Cafe")
        self.assertEqual(groups[0][1], "abc")
        self.assertEqual(len(groups[0][2]), 2)

    def test_menu_item_rows_to_candidates_adds_stable_ids(self) -> None:
        candidates = menu_item_rows_to_candidates(
            [
                {
                    "menu_source_url": "https://example.com/menu",
                    "category": "Mains",
                    "item_name": "Vegan Bowl",
                    "description": "tofu, greens",
                    "price": "$14",
                    "dietary_terms": "vegan; vegetarian",
                    "allergen_terms": "soy",
                    "confidence": "0.85",
                    "raw_text": "Vegan Bowl tofu, greens $14",
                }
            ],
            max_candidates=10,
        )

        self.assertEqual(candidates[0]["candidate_id"], "c0001")
        self.assertEqual(candidates[0]["dietary_terms"], ["vegan", "vegetarian"])
        self.assertEqual(candidates[0]["allergen_terms"], ["soy"])
        self.assertEqual(candidates[0]["rule_parser_confidence"], 0.85)

    def test_builds_candidate_prompt_with_one_output_per_candidate_rule(self) -> None:
        prompt = build_candidate_evidence_prompt(
            restaurant_name="Example Cafe",
            candidates=[
                {
                    "candidate_id": "c0001",
                    "source_url": "https://example.com/menu",
                    "source_type": "website_link",
                    "extraction_method": "html_visible_text",
                    "rule_parser_confidence": 0.85,
                    "category": "Mains",
                    "item_name": "Vegan Bowl",
                    "description": "tofu, greens",
                    "price": "$14",
                    "dietary_terms": ["vegan"],
                    "allergen_terms": ["soy"],
                    "raw_text": "Vegan Bowl tofu, greens $14",
                }
            ],
            context_sources=[],
            max_context_chars=100,
        )

        self.assertIn("Restaurant: Example Cafe", prompt)
        self.assertIn("candidate_id: c0001", prompt)
        self.assertIn("Return exactly one menu_items object", prompt)
        self.assertIn("Vegan Bowl tofu, greens $14", prompt)

    def test_parses_gemini_json_response_text(self) -> None:
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"restaurant_name":"Example Cafe","menu_items":[],"restaurant_notes":[],"restaurant_signals":{"has_allergy_disclaimer":false,"has_cross_contact_warning":false,"mentions_staff_allergy_instruction":false,"has_vegan_options":false,"has_vegetarian_options":false,"has_gluten_free_options":false,"has_tofu_or_plant_protein_options":false,"evidence_confidence":0.4},"extraction_warnings":[]}'
                            }
                        ]
                    }
                }
            ]
        }

        parsed = _parse_gemini_json_response(payload)

        self.assertEqual(parsed["restaurant_name"], "Example Cafe")
        self.assertEqual(parsed["menu_items"], [])

    def test_payload_uses_gemini_rest_structured_output_fields(self) -> None:
        payload = _build_gemini_payload("Extract a menu.")
        config = payload["generationConfig"]

        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertIn("responseJsonSchema", config)
        self.assertNotIn("responseFormat", config)

    def test_candidate_payload_can_use_candidate_schema(self) -> None:
        payload = _build_gemini_payload(
            "Clean candidates.",
            schema=CANDIDATE_MENU_EVIDENCE_SCHEMA,
        )
        menu_item_properties = (
            payload["generationConfig"]["responseJsonSchema"]["properties"]
            ["menu_items"]["items"]["properties"]
        )

        self.assertIn("candidate_id", menu_item_properties)
        self.assertIn("is_menu_item", menu_item_properties)

    def test_builds_candidate_validation_prompt_without_enrichment_task(self) -> None:
        prompt = build_candidate_validation_prompt(
            restaurant_name="Example Cafe",
            candidates=[
                {
                    "candidate_id": "c0001",
                    "source_url": "https://example.com/menu",
                    "source_type": "website_link",
                    "extraction_method": "html_visible_text",
                    "rule_parser_confidence": 0.65,
                    "category": "Jobs",
                    "item_name": "Now Hiring",
                    "description": "Join our team",
                    "price": "",
                    "raw_text": "Now Hiring Join our team",
                }
            ],
        )

        self.assertIn("Restaurant: Example Cafe", prompt)
        self.assertIn("candidate_id: c0001", prompt)
        self.assertIn("Return exactly one validations object", prompt)
        self.assertIn("Do not add candidates", prompt)

    def test_validation_payload_is_candidate_id_only_gate(self) -> None:
        payload = _build_gemini_payload(
            "Validate candidates.",
            schema=MENU_CANDIDATE_VALIDATION_SCHEMA,
            system_instruction=GEMINI_CANDIDATE_VALIDATION_SYSTEM_INSTRUCTION,
        )
        validation_properties = (
            payload["generationConfig"]["responseJsonSchema"]["properties"]
            ["validations"]["items"]["properties"]
        )

        self.assertIn("candidate_id", validation_properties)
        self.assertIn("is_menu_item", validation_properties)
        self.assertNotIn("item_name", validation_properties)


if __name__ == "__main__":
    unittest.main()
