from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Reuse v1's record so v2 output plugs straight into the allergen prior / scoring
# layer and is directly comparable in the eval harness.
from safeplate.menu_text import MenuItemRecord


class PayloadKind(str, Enum):
    STRUCTURED = "structured"  # machine-readable schema (JSON-LD, app JSON, HTML table)
    TEXT = "text"              # human prose: HTML body or extracted PDF text
    VISUAL = "visual"          # images, scanned / image-only PDFs
    EMPTY = "empty"            # nothing usable acquired


class Policy(str, Enum):
    # Free structured parse first; the LLM is only paid for the unstructured tail.
    HYBRID = "hybrid"
    # The LLM interprets all text/visual content; structured parse is a fallback.
    LLM_FIRST = "llm_first"
    # Run BOTH and union per source -- max recall (a dish found by either is kept).
    MERGE = "merge"


@dataclass
class Payload:
    """One acquired menu source, normalized to a single interpretable form.

    The pipeline routes on `kind`, but treats it as a *hint*, not a gate: the
    structured parser runs on any non-visual payload and simply returns nothing
    when there is no schema, so a misclassification degrades gracefully instead
    of producing garbage.
    """

    url: str
    source_type: str                      # provider hint: website_link/pdf/image/...
    kind: PayloadKind
    text: str = ""                        # HTML, or extracted PDF/plain text
    content: bytes | None = None          # raw bytes for VISUAL interpreters
    mime: str = ""
    restaurant_name: str | None = None
    restaurant_source_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class CoverageReport:
    """Honest per-source outcome. `found=False` is a first-class result with a
    reason -- never a silently swallowed failure or a fabricated item."""

    url: str
    found: bool
    payload_kind: str
    item_count: int
    interpreter: str                      # structured / llm_text / llm_visual / none
    confidence: float
    reason: str


@dataclass
class AllergySignal:
    """Restaurant-level allergy-handling evidence from a narrative page (e.g. an
    'allergy-friendly kitchen' / cross-contact / 'ask our staff' statement) -- the
    qualitative signal that matters even when no dish x allergen matrix exists."""

    url: str
    allergy_friendly_claim: bool
    cross_contact_warning: bool
    ask_staff: bool
    allergen_menu_available: bool
    statements: list[str]                 # verbatim, source-grounded quotes
    confidence: float
    # The kitchen/facility claims to be NUT-FREE (not merely "nut-free options"). A
    # strong DOWN-signal -- see allergy_signals for the strict, grounded detection.
    nut_free_claim: bool = False


@dataclass
class MenuExtractionResult:
    items: list[MenuItemRecord]
    coverage: list[CoverageReport]
    llm_calls: int = 0                    # cost accounting for hybrid-vs-llm-first
    allergy_signals: list[AllergySignal] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    # True when a transient LLM failure left the extraction knowingly partial (e.g. one
    # chunk of a multi-chunk menu failed). Callers must NOT cache an incomplete result
    # as complete -- a missing chunk could omit a risky dish and wrongly look safer.
    incomplete: bool = False
