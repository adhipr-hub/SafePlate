"""SafePlate menu extraction v2 (clean-architecture rebuild).

v1 (`safeplate.menu_sources` + `safeplate.menu_text`) makes a *semantic* decision
-- "is this string a dish?", "is this PDF a menu?" -- using *lexical/structural*
proxies (price regexes, title-case grammar gates, keyword blocklists, nav
detection). Semantics cannot be captured by a finite rule list, so every new
site surfaces new junk that needs a new rule. v2 draws a principled line:

  * deterministic parsing ONLY for machine-readable schemas (JSON-LD, app JSON,
    HTML allergen tables) -- the schema is ground truth, no guessing;
  * the LLM interprets unstructured content (prose HTML, PDF text, images) --
    it inherently knows "Modern Slavery Act" is not a dish;
  * rules survive only as a VERIFICATION guardrail (grounding, allergen vocab,
    confidence) -- a fixed contract that does not grow per-site.

v1 is left fully intact as the comparison baseline (see eval/compare_engines.py).
"""

from safeplate.extraction2.pipeline import extract_menu
from safeplate.extraction2.schema import (
    CoverageReport,
    MenuExtractionResult,
    Payload,
    PayloadKind,
    Policy,
)

__all__ = [
    "extract_menu",
    "CoverageReport",
    "MenuExtractionResult",
    "Payload",
    "PayloadKind",
    "Policy",
]
