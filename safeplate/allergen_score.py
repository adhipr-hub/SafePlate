"""Layer #5: per-user allergen risk scoring + ranking.

Fuses the deterministic prior (cuisine x location x dish-name knowledge, from
``allergen_prior``) with GROUNDED menu evidence (allergen matrices / extracted
``allergen_terms``) and restaurant-level allergy signals into a single per-user
assessment, then ranks restaurants safest-first.

Design contract (inherited from the prior layer, non-negotiable):
- Absence of an allergen mention is NEVER treated as absence. The fused risk has
  a severity-dependent FLOOR; the lowest tier is "likely_ok", never "safe".
- Presence dominates. One dish whose allergen chart marks the user's allergen
  present makes the restaurant AVOID for that allergen, no matter how clean the
  rest looks. A clean signal can only pull risk DOWN -- gated by the region's
  labeling trust and the user's severity -- and can never erase confirmed
  presence.
- Evidence outranks inference. AVOID requires grounded evidence (a matrix hit, an
  explicit menu mention, or consistent community reports). A high cuisine/dish
  PRIOR alone caps at "caution": we infer the allergen is likely, we have not
  confirmed it.

Tier C (community / anecdotal review signals -- "a diner reported a reaction") is
a PARALLEL, provenance-tagged MODIFIER, not a precedence rung. The pure,
asymmetric, bounded delta math lives here (``_apply_community``). Community is
populated LIVE in the drawer flow via ``community_signals.py`` (Places-reviews
fetch + LLM classification); other callers may still pass ``community=None`` and
the seam degrades cleanly. Per the locked design, community is SAFETY-
ASYMMETRIC: adverse reports raise risk, positive reports only improve the
handling signal and never lower dish risk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

from safeplate.allergen_prior import (
    NUTS,
    NUT_TYPES,
    PEANUTS,
    TREE_NUTS,
    TREE_NUT_TYPES,
    absence_inference_factor,
    clamp_risk as _clamp,
    families_for_nut_types,
    labeling_trust_for_region,
    normalize_cuisine,
    region_from_address,
    restaurant_nut_risk,
    score_restaurant_prior,
    specific_tree_nuts,
)

DISCLAIMER = (
    "Estimated allergen risk, not a guarantee -- always confirm directly with the "
    "restaurant before ordering."
)


# --------------------------------------------------------------------------- #
# User profile
# --------------------------------------------------------------------------- #
class Severity(Enum):
    """How careful the user needs the assessment to be for one allergen. The
    ordinal drives the precautionary floor and the tier thresholds: a more severe
    allergy trips a worse tier at the same underlying risk."""

    AVOID_PREFERENCE = 0  # dislikes / mild preference
    INTOLERANCE = 1       # discomfort, non-immune
    ALLERGY = 2           # immune reaction
    ANAPHYLAXIS = 3       # life-threatening

    @property
    def rank(self) -> int:
        return self.value


class CrossContactSensitivity(Enum):
    """How much TRACE / shared-equipment exposure matters to this user, INDEPENDENT
    of ingestion severity. Someone anaphylactic to eating nuts may still tolerate a
    kitchen that handles nuts (low cross-contact concern); someone with a milder
    reaction can be extremely trace-sensitive. This drives per-dish navigability and
    how hard a 'may contain' warning weighs -- it does NOT lower the ingestion floor."""

    NOT_CONCERNED = 0  # reacts to ingestion only; traces / shared equipment tolerated
    MODERATE = 1       # standard caution
    STRICT = 2         # trace-sensitive; shared fryer / 'may contain' is disqualifying

    @property
    def rank(self) -> int:
        return self.value


@dataclass(frozen=True)
class AllergenPref:
    allergen: str             # canonical key, e.g. "nuts" / "peanuts" / "tree_nuts"
    severity: Severity = Severity.ALLERGY
    # None -> derive a sensible cross-contact level from ``severity`` (back-compat).
    cross_contact: CrossContactSensitivity | None = None
    # The SPECIFIC nuts this user reacts to (subset of ``allergen_prior.NUT_TYPES``).
    # None -> the whole family (the calibrated default). A strict subset turns on
    # per-nut scoring: only dishes/evidence naming a selected nut drive the verdict,
    # while non-selected nuts add only a small cross-contact allowance.
    nut_types: frozenset[str] | None = None


@dataclass(frozen=True)
class UserProfile:
    allergens: tuple[AllergenPref, ...] = ()

    @classmethod
    def for_nuts(
        cls,
        severity: Severity = Severity.ALLERGY,
        cross_contact: CrossContactSensitivity | None = None,
        nut_types: frozenset[str] | None = None,
    ) -> "UserProfile":
        """Convenience for the nuts build. ``nut_types`` (a subset of the selectable
        nuts) turns on per-nut scoring; None keeps the family-level default."""
        return cls(allergens=(
            AllergenPref(
                allergen=NUTS, severity=severity,
                cross_contact=cross_contact, nut_types=nut_types,
            ),
        ))


# --------------------------------------------------------------------------- #
# Restaurant-level inputs
# --------------------------------------------------------------------------- #
@dataclass
class RestaurantSignals:
    """First-party, restaurant-level allergy-handling signals (booleans). These
    come from the menu-evidence stage; ``allergen_menu_available`` / ``ask_staff``
    are HANDLING signals (they don't change dish risk), while ``nut_free_claim``
    is a clean down-signal and ``cross_contact_warning`` raises the floor."""

    nut_free_claim: bool = False
    cross_contact_warning: bool = False
    ask_staff: bool = False
    allergen_menu_available: bool = False
    allergy_disclaimer: bool = False

    @classmethod
    def from_evidence_dict(cls, data: dict[str, bool] | None) -> "RestaurantSignals":
        """Map ``local_app.restaurant_signals_from_evidence`` output."""
        data = data or {}
        return cls(
            nut_free_claim=bool(data.get("has_nut_free_claim")),
            cross_contact_warning=bool(data.get("has_cross_contact_warning")),
            ask_staff=bool(data.get("mentions_staff_allergy_instruction")),
            allergy_disclaimer=bool(data.get("has_allergy_disclaimer")),
        )

    @classmethod
    def from_allergy_signals(cls, signals: Sequence[Any] | None) -> "RestaurantSignals":
        """Map v2 ``AllergySignal`` objects (duck-typed, no import coupling). Note:
        an 'allergy-friendly' claim is a HANDLING signal, not a nut-free claim."""
        out = cls()
        for sig in signals or []:
            if getattr(sig, "cross_contact_warning", False):
                out.cross_contact_warning = True
            if getattr(sig, "ask_staff", False):
                out.ask_staff = True
            if getattr(sig, "allergen_menu_available", False):
                out.allergen_menu_available = True
            if getattr(sig, "allergy_friendly_claim", False):
                out.allergy_disclaimer = True
            if getattr(sig, "nut_free_claim", False):
                out.nut_free_claim = True
        return out


@dataclass
class CommunitySignal:
    """One classified community / anecdotal review signal (Tier C). The data
    source (Google Places reviews + LLM classification) is NOT built yet -- this
    is the typed seam the scorer already understands. ``quote`` is expected to be
    a verbatim, source-grounded excerpt once the fetch path exists."""

    type: str                      # adverse_event | poor_handling | good_handling | allergen_presence | none
    allergen: str | None = None    # canonical key if named, else None (unspecified)
    quote: str = ""
    rating: float | None = None
    age_days: int | None = None    # how old the review is, for recency decay
    source: str = "google_reviews"
    url: str = ""


# --------------------------------------------------------------------------- #
# Outputs
# --------------------------------------------------------------------------- #
class Tier(Enum):
    LIKELY_OK = "likely_ok"   # lowest tier; deliberately NOT "safe"
    CAUTION = "caution"
    AVOID = "avoid"

    @property
    def rank(self) -> int:
        return {"likely_ok": 0, "caution": 1, "avoid": 2}[self.value]


@dataclass
class Handling:
    """Restaurant-level allergy competence, surfaced SEPARATELY from dish risk: a
    place can be high-risk AND highly allergy-aware (so the user knows to ask)."""

    allergy_aware: bool = False
    cross_contact_warning: bool = False
    ask_staff: bool = False
    allergen_menu: bool = False
    nut_free_claim: bool = False
    community_praise: int = 0
    community_concern: int = 0


@dataclass
class AllergenAssessment:
    allergen: str
    severity: str
    risk: float
    confidence: float
    tier: str                       # Tier.value
    basis: str                      # allergen_matrix | menu_evidence | restaurant_signal | dish_prior | cuisine_prior
    rationale: list[str] = field(default_factory=list)
    riskiest_items: list[dict[str, Any]] = field(default_factory=list)
    community_reported: bool = False
    # Menu shape (for the whole-picture view the LLM judge needs, cheaply): how many
    # dishes we parsed, how many NAME the allergen, how many are SUSPECTED (type often
    # hides nuts -- low confidence), and whether it's navigable.
    menu_total: int = 0
    menu_flagged: int = 0
    menu_suspected: int = 0
    navigable: bool = False


@dataclass
class UserAllergenAssessment:
    overall_risk: float
    overall_confidence: float
    tier: str                       # worst per-allergen tier
    evidence_basis: str             # basis of the allergen that set the tier
    per_allergen: list[AllergenAssessment]
    handling: Handling
    rationale: list[str]
    community_reported: bool = False
    disclaimer: str = DISCLAIMER
    # Citable evidence behind the rationale's [E#] markers: each entry is
    # {id, type, text, url?, quote?}. Populated by the LLM scorer so the UI can link
    # each cite chip to EXACTLY where the claim came from; empty for the rules scorer
    # (its rationale carries no [E#] citations).
    evidence: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Allergen-term -> nut-family recognition (grounded-evidence matching)
# --------------------------------------------------------------------------- #
# Matrices emit canonical English labels ("peanut", "tree nut"); free-text
# extraction emits whatever ``menu_text.ALLERGEN_TERMS`` matched. Multilingual
# nut labels mirror ``allergen_prior`` and can be extended the same way.
_PEANUT_TERMS = {
    "peanut", "peanuts", "groundnut",
    "cacahuete", "cacahuate", "cacahuète", "arachide", "arachidi", "erdnuss",
    "amendoim", "落花生", "ピーナッツ", "花生",
    "땅콩", "ถั่วลิสง", "मूंगफली",
    "арахис",
    "đậu phộng", "فول سوداني", "fıstığı",  # peanut (vi/ar; tr fıstığı = peanut/pistachio)
}
_TREE_NUT_TERMS = {
    "tree nut", "treenut", "tree-nut",
    "almond", "cashew", "walnut", "pecan", "hazelnut", "pistachio", "macadamia",
    "chestnut", "pine nut", "pinenut", "brazil nut",
    "almendra", "amande", "mandel", "mandorla", "anacardo", "cajou", "avellana",
    "noisette", "haselnuss", "nocciola", "pistacho", "pistache", "pistazie",
    "pistacchio", "walnuss",
    # Non-Latin / accented tree-nut INGREDIENT words that menu_text.ALLERGEN_TERMS
    # already extracts but the scorer previously dropped (recognized no nut family),
    # so a foreign-language menu literally naming a nut produced grounded evidence
    # that was then silently discarded -- a safety-asymmetric false negative. Mirrored
    # from the extraction vocabulary; a regression test asserts the two cannot drift.
    "amêndoa", "badem",                                   # almond (pt, tr)
    "アーモンド", "杏仁", "아몬드", "बादाम", "миндаль", "لوز",        # almond (ja/zh/ko/hi/ru/ar)
    "カシューナッツ", "腰果", "काजू", "كاجو",                       # cashew (ja/zh/hi/ar)
    "fındık", "ヘーゼルナッツ", "بندق", "фундук",                   # hazelnut (tr/ja/ar/ru)
    "ピスタチオ", "开心果", "فستق",                              # pistachio (ja/zh/ar)
    "くるみ", "核桃", "호두", "अखरोट", "ceviz",                   # walnut (ja/zh/ko/hi/tr)
    "pinoli", "松子", "잣",                                  # pine nut (it/zh/ko)
    # Reverse-gap ingredient words newly added to the extraction vocabulary too.
    "fıstığı", "hạnh nhân", "anacardi", "cashewkern", "hạt điều", "кешью",
    "avelã", "фисташки", "クルミ", "piñón",
    # Definitional nut-derived ingredients (the named thing IS a nut), so a literal
    # listing counts as grounded tree-nut evidence. Mirrors menu_text.ALLERGEN_TERMS.
    "filbert", "marzipan", "frangipane", "gianduja", "nutella", "pignoli",
}
_GENERIC_NUT_TERMS = {"nut", "nuts"}
# Words that merely CONTAIN "nut" but are not (tree/ground) nuts. Used to keep the
# generic-nut fallback from firing on them. ("chestnut" is intentionally absent --
# it is a real tree nut and is matched above.)
_NUT_SUBSTRING_FALSE_FRIENDS = ("coconut", "butternut", "doughnut", "donut", "nutmeg")


def _is_generic_nut(term: str) -> bool:
    """True for a free-text allergen mention that names nuts generically -- not only
    the exact tokens 'nut'/'nuts' but also 'mixed nuts', 'nut oil', 'tree-nuts', etc.

    LLM extraction feeds raw ``allergen_mentions`` strings straight into the scorer
    (unlike matrices, which canonicalize), so without this a confirmed nut mention
    phrased as 'mixed nuts' would not count -- a safety-asymmetric MISS. Guards the
    handful of words that contain 'nut' but are not nuts."""
    if term in _GENERIC_NUT_TERMS:
        return True
    if "nut" not in term:
        return False
    # Count it unless every "nut" occurrence sits inside a false-friend word: strip
    # those words out and see whether a "nut" survives.
    stripped = term
    for false_friend in _NUT_SUBSTRING_FALSE_FRIENDS:
        stripped = stripped.replace(false_friend, "")
    return "nut" in stripped


# The nut family is constant; share one frozenset instead of rebuilding a set per call
# (called per allergen and per community signal). All callers only read it (& / in).
_NUT_FAMILY_SET = frozenset({PEANUTS, TREE_NUTS})


def _families(allergen: str) -> set[str] | frozenset[str]:
    if allergen == NUTS:
        return _NUT_FAMILY_SET
    return {allergen}


_NUT_LABELS = {
    "almond": "almonds", "cashew": "cashews", "walnut": "walnuts", "pecan": "pecans",
    "pistachio": "pistachios", "hazelnut": "hazelnuts", "macadamia": "macadamias",
    "brazil_nut": "Brazil nuts", "pine_nut": "pine nuts", "chestnut": "chestnuts",
    "peanuts": "peanuts",
}


def _nut_selection_label(allergen: str, wanted_nuts: frozenset[str] | None) -> str:
    """Human label for the user's nut selection, for the rationale ('almonds' vs the
    generic 'nuts'). None -> the family word ('nuts')."""
    if wanted_nuts is None:
        return allergen
    names = [_NUT_LABELS.get(k, k.replace("_", " ")) for k in NUT_TYPES if k in wanted_nuts]
    if not names:
        return allergen
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{', '.join(names[:-1])} & {names[-1]}"


def _nut_terms_present(
    allergen_terms: Sequence[str], families: set[str] | str,
) -> list[str]:
    """Family-level grounded nut hits (back-compat wrapper over ``_split_nut_terms``).
    Accepts a family set or an allergen key string. Equivalent to the pre-per-nut
    behavior; used by the vocab-consistency and robustness tests."""
    fams = _families(families) if isinstance(families, str) else families
    contains, _other = _split_nut_terms(allergen_terms, fams, None)
    return contains


def _split_nut_terms(
    allergen_terms: Sequence[str], families: set[str], wanted_nuts: frozenset[str] | None,
) -> tuple[list[str], list[str]]:
    """Classify grounded allergen terms into (contains, other) for the user's nut
    selection. ``contains`` = terms naming a nut the user reacts to OR family-level
    evidence we can't disaggregate (a 'tree nut' chart column, a generic 'nuts'
    mention); ``other`` = terms naming nuts they did NOT select (a cross-contact
    signal only). With ``wanted_nuts is None`` every family hit is 'contains' and
    'other' is empty -- byte-identical to the family-level default."""
    if not families & {PEANUTS, TREE_NUTS}:
        return [], []  # non-nut allergens not modelled in the nuts build
    contains: list[str] = []
    other: list[str] = []
    for raw in allergen_terms or []:
        term = str(raw).strip().lower()
        if not term:
            continue
        is_peanut = any(p in term for p in _PEANUT_TERMS)
        is_tree = any(t in term for t in _TREE_NUT_TERMS)
        is_generic = _is_generic_nut(term)
        if not (is_peanut or is_tree or is_generic):
            continue
        if wanted_nuts is None:
            # Family-level default (unchanged behavior).
            if (PEANUTS in families and is_peanut) or (TREE_NUTS in families and is_tree) or is_generic:
                contains.append(term)
            continue
        # --- per-nut split ---
        if is_peanut:
            (contains if PEANUTS in wanted_nuts else other).append(term)
        elif is_tree:
            specifics = specific_tree_nuts(term)
            if not specifics:  # unspecified tree nut ('tree nut') -> can't disaggregate
                (contains if TREE_NUTS in families else other).append(term)
            elif specifics & wanted_nuts:
                contains.append(term)
            else:
                other.append(term)  # a tree nut the user didn't select
        else:  # generic 'nuts'/'mixed nuts': can't disaggregate -> count as contains
            contains.append(term)
    return contains, other


def _is_matrix_method(method: str) -> bool:
    return "matrix" in (method or "").lower()


# --------------------------------------------------------------------------- #
# Source provenance / freshness (Phase B)
# --------------------------------------------------------------------------- #
# A clean "allergen NOT listed" signal is only as trustworthy as its source. An
# official, recent allergen chart earns full trust; a stale or off-site copy (an
# allergy blog's 2012 PDF) must NOT strongly pull risk down. Presence ("contains
# nuts") is trusted regardless of age -- this asymmetry keeps us safety-conservative.
_OFFSITE_TRUST = 0.7
_STALE_YEARS = 3


def _domain_of(url: str | None) -> str:
    host = urlparse(url or "").netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _freshness_factor(url: str | None) -> float:
    current_year = datetime.now(timezone.utc).year
    years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", url or "")]
    years = [y for y in years if 2000 <= y <= current_year]
    if not years:
        return 0.9  # no date in the URL -> mild discount
    age = current_year - max(years)
    return 1.0 if age <= _STALE_YEARS else max(0.5, 1.0 - 0.12 * (age - _STALE_YEARS))


def _source_trust(url: str | None, official_domain: str | None) -> float:
    """1.0 = official-domain + recent; lower for off-site and/or stale sources."""
    if not url:
        return 0.85
    on_official = bool(official_domain) and _domain_of(url) == official_domain
    return (1.0 if on_official else _OFFSITE_TRUST) * _freshness_factor(url)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
# Severity -> (caution threshold, clean-evidence floor). Higher severity trips
# caution sooner and refuses to let a clean signal pull risk as far down.
_SEVERITY_TUNING: dict[Severity, tuple[float, float]] = {
    Severity.ANAPHYLAXIS: (0.10, 0.20),
    Severity.ALLERGY: (0.18, 0.12),
    Severity.INTOLERANCE: (0.28, 0.08),
    Severity.AVOID_PREFERENCE: (0.38, 0.05),
}

# Cross-contact concern defaults to a severity-derived level when the user hasn't
# set it explicitly -- preserving the old severity-only behaviour for callers that
# don't pass it. Anaphylaxis -> STRICT (a nut-handling kitchen is disqualifying);
# a mild preference -> NOT_CONCERNED. The user can override this independently of
# severity (e.g. anaphylactic to ingestion but not worried about traces).
_CC_DEFAULT_BY_SEVERITY: dict[Severity, CrossContactSensitivity] = {
    Severity.ANAPHYLAXIS: CrossContactSensitivity.STRICT,
    Severity.ALLERGY: CrossContactSensitivity.MODERATE,
    Severity.INTOLERANCE: CrossContactSensitivity.MODERATE,
    Severity.AVOID_PREFERENCE: CrossContactSensitivity.NOT_CONCERNED,
}

# De-quantization: a place where we PARSED a real menu and found no nut-named
# dishes is weakly safer than one we know nothing about -- but only weakly (a menu
# rarely names the peanut oil in the kitchen). This lets two same-cuisine places
# diverge by how much menu we actually saw, instead of both snapping to the flat
# cuisine constant. Effect is mild, scaled by coverage x regional labeling trust,
# and always floored -- it never reaches "safe". _COVERAGE_FULL = item count at
# which coverage saturates; _COVERAGE_DISCOUNT = max fraction it can shave off.
_COVERAGE_FULL = 20
_COVERAGE_DISCOUNT = 0.22

# Navigability / dineability: when nuts are confined to a clearly-avoidable MINORITY
# of dishes and the user tolerates traces, the restaurant score should reflect EASE
# OF AVOIDANCE -- not the single worst dish. A labeled chain with a few nut desserts
# (88 of 93 safe) is a fine choice; pinning it at the riskiest dish (0.97) punishes
# transparency and makes the tool say "never eat out". These tune that model.
_PERVASIVE_FRACTION = 0.6      # at/above this risky share, nuts are unavoidable -> high
_NAV_BASE = 0.22              # floor of the navigable band
_NAV_SLOPE = 0.55            # how fast risk climbs with the risky-dish fraction
_NAV_MATRIX_FACTOR = 0.7     # a CONFIRMED chart lets you trust the safe dishes -> lowest
_NAV_INFERRED_RETAIN = 0.7   # dish-NAME nav can't vouch for unnamed dishes: a high-nut
                             #   cuisine stays anchored near its baseline (hidden nuts)
_NAV_HANDLING_FACTOR = 0.85  # allergy-handling signals (disclaimer/ask-staff) -> accommodation
# SUSPECTED dishes (a low-confidence assumption that the dish type hides nuts) are
# UNCERTAIN, not safe: they nudge the score up modestly (less than a NAMED nut dish)
# and never let the place read as "clearly safe".
_NAV_SUSPECTED_SLOPE = 0.25   # per suspected-dish fraction (vs _NAV_SLOPE for named)
_NAV_BASE_SUSPECTED = 0.18    # floor of the "only suspected dishes" band
_SUSPECTED_CONF_DISPLAY = 0.4  # shown confidence when the verdict rests on assumptions

# Floor a 'may contain' / cross-contact warning imposes, keyed on the user's
# cross-contact sensitivity (NOT their ingestion severity). A NOT_CONCERNED user is
# unaffected by such a warning; a trace-sensitive user is pushed to caution.
_CC_WARNING_FLOOR: dict[CrossContactSensitivity, float] = {
    CrossContactSensitivity.NOT_CONCERNED: 0.0,
    CrossContactSensitivity.MODERATE: 0.35,
    CrossContactSensitivity.STRICT: 0.45,
}

# Per-nut cross-contact allowance: when the user selected SPECIFIC nuts and only OTHER
# nuts are present, add this small bump scaled by cross-contact sensitivity. Kept minor
# (and capped by _XNUT_CC_CEIL) so it nudges ranking without ever, on its own, escalating
# a clean place into AVOID -- presence of the user's OWN nut is still required for that.
_XNUT_CC_BUMP: dict[CrossContactSensitivity, float] = {
    CrossContactSensitivity.NOT_CONCERNED: 0.0,
    CrossContactSensitivity.MODERATE: 0.03,
    CrossContactSensitivity.STRICT: 0.07,
}
_XNUT_CC_CEIL = 0.50


def _effective_cross_contact(
    severity: Severity, cross_contact: CrossContactSensitivity | None
) -> CrossContactSensitivity:
    if cross_contact is not None:
        return cross_contact
    return _CC_DEFAULT_BY_SEVERITY[severity]


def score_restaurant_for_user(
    profile: UserProfile,
    *,
    cuisines: list[str] | None,
    region: str = "unknown",
    menu_items: Sequence[Any] | None = None,
    signals: RestaurantSignals | None = None,
    community: Sequence[CommunitySignal] | None = None,
    official_domain: str | None = None,
) -> UserAllergenAssessment:
    """Fuse prior + grounded evidence + signals + community into one per-user
    assessment. ``community`` is populated live in the drawer flow (and may be
    empty elsewhere). ``official_domain`` (the restaurant's own domain) lets
    provenance weighting distrust off-site / stale allergen sources' clean signals."""
    signals = signals or RestaurantSignals()
    community = list(community or [])

    handling = _restaurant_handling(signals, community)

    per_allergen = [
        _score_one_allergen(
            pref,
            cuisines=cuisines,
            region=region,
            menu_items=menu_items or [],
            signals=signals,
            community=community,
            official_domain=official_domain,
        )
        for pref in profile.allergens
    ]
    if not per_allergen:
        # No declared allergens: nothing to assess. Be explicit, not silently safe.
        return UserAllergenAssessment(
            overall_risk=0.0,
            overall_confidence=0.0,
            tier=Tier.LIKELY_OK.value,
            evidence_basis="no_profile",
            per_allergen=[],
            handling=handling,
            rationale=["No allergens selected; no assessment performed."],
        )

    # Worst allergen drives the headline tier (you are unsafe if ANY of your
    # allergens is unsafe); keep the full per-allergen breakdown for the UI.
    worst = max(per_allergen, key=lambda a: (Tier(a.tier).rank, a.risk))
    overall_risk = max(a.risk for a in per_allergen)
    rationale = list(worst.rationale)
    if handling.allergy_aware:
        rationale.append("Restaurant shows allergy-handling awareness -- still ask staff directly.")

    return UserAllergenAssessment(
        overall_risk=round(overall_risk, 3),
        overall_confidence=round(worst.confidence, 2),
        tier=worst.tier,
        evidence_basis=worst.basis,
        per_allergen=per_allergen,
        handling=handling,
        rationale=rationale,
        community_reported=any(a.community_reported for a in per_allergen),
    )


def _score_one_allergen(
    pref: AllergenPref,
    *,
    cuisines: list[str] | None,
    region: str,
    menu_items: Sequence[Any],
    signals: RestaurantSignals,
    community: Sequence[CommunitySignal],
    official_domain: str | None = None,
) -> AllergenAssessment:
    # Per-nut selection: None (or 'all selected') keeps the calibrated family-level
    # behavior; a strict subset narrows the families in play and turns on the per-nut
    # term split + cross-contact allowance below.
    wanted_nuts = pref.nut_types
    if wanted_nuts is not None and wanted_nuts >= set(NUT_TYPES):
        wanted_nuts = None
    families = _families(pref.allergen) if wanted_nuts is None else families_for_nut_types(wanted_nuts)
    allergen_label = _nut_selection_label(pref.allergen, wanted_nuts)
    severity = pref.severity
    cross_contact = _effective_cross_contact(severity, pref.cross_contact)
    caution_threshold, severity_floor = _SEVERITY_TUNING[severity]
    labeling_trust = labeling_trust_for_region(region)

    # Materialize each item's fields ONCE (raw name, stripped name, description,
    # method, terms, source url) so the prior call, the grounded-evidence loop, and
    # the parsed-count below don't each re-walk menu_items via repeated _field calls.
    rows = []
    for item in menu_items:
        name_raw = _field(item, "item_name") or _field(item, "name") or ""
        rows.append(
            (
                name_raw,
                name_raw.strip(),
                _field(item, "description") or "",
                _field(item, "extraction_method") or "",
                _field(item, "allergen_terms") or [],
                _field(item, "menu_source_url") or "",
                _field(item, "matrix_allergen_columns") or (),
            )
        )

    # Cuisine-only baseline (no dish priors): used both as the per-item baseline inside
    # restaurant_nut_risk AND to tell a dish that is nut-risky by NAME (e.g. "Satay")
    # apart from one merely inheriting a high-nut cuisine's floor (e.g. "Rice" at a Thai
    # place). Computed once here and threaded down so it isn't evaluated twice.
    cuisine_prior = score_restaurant_prior(
        cuisines=cuisines, region=region, allergen=pref.allergen
    )

    # T4 + T5: cuisine/location floor + dish-name priors (reuses the prior layer).
    base = restaurant_nut_risk(
        cuisines=cuisines,
        region=region,
        menu_items=[
            {"item_name": name_raw, "description": desc}
            for name_raw, _name, desc, _method, _terms, _src, _cols in rows
        ],
        allergen=pref.allergen,
        baseline=cuisine_prior,
        wanted_nuts=wanted_nuts,
    )
    cuisine_floor = cuisine_prior.risk

    risk = base.risk
    confidence = base.confidence
    rationale = list(base.rationale)
    # A known risky dish on the menu makes the basis "dish_prior"; otherwise it's
    # the cuisine/location floor. Grounded evidence below can override either.
    if base.riskiest_items and any(r >= 0.5 for _n, r in base.riskiest_items):
        basis = "dish_prior"
    else:
        basis = "cuisine_prior"

    # T1 / T2: grounded presence evidence from extracted allergen_terms.
    matrix_present = False
    matrix_dish_total = 0
    matrix_hit_items: list[str] = []
    text_hit_items: list[str] = []
    matrix_source_urls: list[str] = []
    matrix_columns: set[str] = set()
    other_nut_terms: set[str] = set()  # nuts the user did NOT select (cross-contact signal)
    for _name_raw, name, _desc, method, terms, src_url, mcols in rows:
        hits, other = _split_nut_terms(terms, families, wanted_nuts)
        other_nut_terms.update(other)
        if _is_matrix_method(method):
            matrix_present = True
            matrix_source_urls.append(src_url)
            matrix_columns.update(mcols)
            if name:
                matrix_dish_total += 1
            if hits and name:
                matrix_hit_items.append(name)
        elif hits and name:
            text_hit_items.append(name)

    # Did the chart actually have a column for the user's nut family? A matrix only
    # needs >=3 allergen columns to be recognized, and those can be milk/egg/gluten with
    # NO peanut/tree-nut column. Without this, a chart that never asked about nuts would
    # trigger the "chart present, nuts not listed" pull-down and score nut-safe -- a
    # false negative. Matrix nut columns canonicalize to "peanut" / "tree nut".
    matrix_covers_nuts = (
        (PEANUTS in families and "peanut" in matrix_columns)
        or (TREE_NUTS in families and "tree nut" in matrix_columns)
    )

    # Provenance trust of the allergen chart -- the most conservative (lowest) of
    # its source(s). A stale/off-site chart should not strongly vouch for absence.
    # A matrix usually shares ONE source url across all its rows; dedupe so the
    # urlparse/regex/datetime in _source_trust runs once per distinct url, not per row.
    matrix_trust = min(
        (_source_trust(url, official_domain) for url in set(matrix_source_urls)),
        default=1.0,
    )

    # How much menu we parsed, and which dishes are nut-RISKY by ANY evidence:
    # grounded chart/text hits OR dish-name inference. Navigability + the coverage
    # discount both read these.
    parsed_count = sum(1 for row in rows if row[1])
    coverage_fraction = min(1.0, parsed_count / _COVERAGE_FULL) if parsed_count else 0.0

    # A dish is nut-risky by NAME only if its prior is ELEVATED above the cuisine
    # floor -- otherwise a high-nut cuisine marks every dish risky and nothing is
    # ever navigable (the bug that pinned a labeled chain at its worst dish).
    name_risk_threshold = max(0.5, cuisine_floor + 0.1)
    # Read the FULL per-dish list (item_details), not riskiest_items, which is capped at
    # the top 5 for display. On a menu with >5 name-risky dishes the cap undercounted
    # risky_count -- inflating safe_count, mislabeling a pervasive menu as navigable, and
    # producing an under-warning "the other N are safe" rationale (the dangerous direction).
    inferred_risky = {
        d["name"] for d in base.item_details if d["risk"] >= name_risk_threshold
    }
    risky_names = set(matrix_hit_items) | set(text_hit_items) | inferred_risky
    # SUSPECTED: dish types that often HIDE nuts (low-confidence assumption). Treated
    # as UNCERTAIN -- they don't count as confirmed nut dishes, but they aren't
    # "clearly safe" either, so they modestly raise risk + lower confidence.
    suspected_names = {
        d["name"] for d in base.item_details if d.get("basis") == "suspected_nuts"
    } - risky_names
    risky_count = len(risky_names)
    suspected_count = len(suspected_names)
    safe_count = max(0, parsed_count - risky_count)
    risky_fraction = (risky_count / parsed_count) if parsed_count else 0.0
    suspected_fraction = (suspected_count / parsed_count) if parsed_count else 0.0
    trace_sensitive = cross_contact.rank >= CrossContactSensitivity.STRICT.rank
    pervasive = parsed_count > 0 and risky_fraction >= _PERVASIVE_FRACTION

    # DINEABILITY: nuts confined to a clearly-avoidable MINORITY, safe options remain,
    # and the user tolerates traces. True regardless of evidence type (chart, menu
    # text, or dish-name) -- so a labeled chain with a few nut desserts is navigable,
    # not pinned at its worst dish. Gated on CROSS-CONTACT concern, not ingestion
    # severity: an anaphylactic user who isn't worried about traces can still navigate.
    navigable = (
        risky_count > 0 and safe_count >= 3 and not pervasive and not trace_sensitive
    )

    grounded_matrix = bool(matrix_hit_items)
    grounded_text = bool(text_hit_items) and not grounded_matrix
    presence = grounded_matrix or grounded_text
    handling_aware = bool(
        signals.allergy_disclaimer or signals.ask_staff or signals.allergen_menu_available
    )

    if risky_count and navigable:
        # Score = EASE OF AVOIDANCE, not the single worst dish. A CONFIRMED chart is
        # trustworthy (you can rely on the unmarked dishes) -> lowest. Dish-NAME
        # inference can't vouch for the unnamed dishes, so for a high-nut cuisine it
        # stays anchored near the baseline (hidden nuts are likely). Allergy-handling
        # signals (accommodation) pull it down further. Never below the caution band:
        # a nut-using kitchen always warrants "confirm with staff".
        nav = _NAV_BASE + _NAV_SLOPE * risky_fraction + _NAV_SUSPECTED_SLOPE * suspected_fraction
        if grounded_matrix:
            nav *= _NAV_MATRIX_FACTOR
            basis = "allergen_matrix"
        elif grounded_text:
            basis = "menu_evidence"
        else:
            nav = max(nav, cuisine_floor * _NAV_INFERRED_RETAIN)
            basis = "dish_prior"
        if handling_aware:
            nav *= _NAV_HANDLING_FACTOR
        risk = max(caution_threshold, min(risk, nav))
        confidence = max(confidence, 0.8 if grounded_matrix else 0.6)
        shown = sorted(risky_names)[:4]
        more = risky_count - len(shown)
        rationale.append(
            f"{risky_count} of {parsed_count} dishes involve {allergen_label} "
            f"({', '.join(shown)}{f' +{more} more' if more > 0 else ''}); the other "
            f"{safe_count} don't -- avoid the flagged dishes and you can likely eat safely."
        )
        if suspected_count:
            rationale.append(
                f"(Plus {suspected_count} dish(es) whose type often hides nuts -- "
                "possible, not confirmed.)"
            )
        if handling_aware:
            rationale.append(
                "Restaurant shows allergy-handling awareness -- still confirm with staff."
            )
    elif risky_count == 0 and suspected_count:
        # No dish NAMES a nut, but some dish TYPES commonly hide them -- a low-confidence
        # assumption. Moderate-low risk, kept in the caution band (never "likely OK"),
        # with LOW displayed confidence so the user knows it's a guess. Trace-sensitive
        # users get a firmer floor (a hidden nut would matter more to them).
        if trace_sensitive:
            risk = max(risk, 0.5)
        else:
            nav = _NAV_BASE_SUSPECTED + _NAV_SUSPECTED_SLOPE * suspected_fraction
            risk = max(caution_threshold, min(risk, nav))
        confidence = min(confidence, _SUSPECTED_CONF_DISPLAY)
        basis = "suspected_nuts"
        shown = sorted(suspected_names)[:4]
        rationale.append(
            f"No dish names {allergen_label}, but {suspected_count} of {parsed_count} dishes "
            f"are types that often hide nuts ({', '.join(shown)}) -- possible, not confirmed "
            "(low confidence; ask staff)."
        )
    elif risky_count:
        # NOT navigable: nuts are pervasive, the user is trace-sensitive, or too few
        # safe options remain. Confirmed presence dominates; inference caps at caution.
        if grounded_matrix:
            risk = max(risk, 0.9)
            confidence = max(confidence, 0.9)
            basis = "allergen_matrix"
        elif grounded_text:
            risk = max(risk, 0.8)
            confidence = max(confidence, 0.7)
            basis = "menu_evidence"
        else:
            risk = max(risk, base.risk)
            basis = "dish_prior"
        if trace_sensitive and safe_count >= 3 and not pervasive:
            rationale.append(
                f"{safe_count} dishes look nut-free, but you've flagged cross-contact as "
                "a serious risk and this kitchen handles nuts -- treated as high risk."
            )
        else:
            shown = sorted(risky_names)[:4]
            rationale.append(
                f"{allergen_label.capitalize()} runs across the menu ({', '.join(shown)}) "
                "-- hard to avoid here."
            )

    # T3: restaurant-level signals. Presence DOMINATES -- a clean signal cannot
    # erase a confirmed hit; it only applies when nothing was found present.
    if not presence:
        if matrix_present and matrix_covers_nuts:
            # A chart that HAS a nut column marks the allergen NOWHERE -> the clean
            # down-signal, gated by labeling trust x severity AND by how much we
            # trust this chart's PROVENANCE (off-site/stale -> weaker pull, lower
            # confidence). Presence is never discounted this way. A chart WITHOUT a nut
            # column proves nothing about nuts, so it falls through to the prior below.
            risk = _pull_down(
                risk, strength=0.75,
                labeling_trust=labeling_trust * matrix_trust, floor=severity_floor,
            )
            confidence = max(confidence, 0.8 * matrix_trust)
            basis = "allergen_matrix"
            rationale.append(
                f"Allergen chart present and does not list {allergen_label} "
                "(cross-contact still possible -- verify)."
            )
        elif signals.nut_free_claim and (families & {PEANUTS, TREE_NUTS}):
            risk = _pull_down(risk, strength=0.5, labeling_trust=labeling_trust, floor=severity_floor)
            confidence = max(confidence, 0.6)
            if basis == "cuisine_prior":
                basis = "restaurant_signal"
            rationale.append("Menu states a nut-free claim (still verify directly).")
        elif basis == "cuisine_prior" and parsed_count:
            # No chart, no claim, no nut-named dishes -- but we DID read a menu.
            # Mild, coverage-scaled reassurance so this ranks below an identical
            # cuisine with no menu at all, without ever implying it's safe. Scaled
            # by REGION MANDATE (not chart trust): a clean US menu reassures less
            # than a clean UK/EU one, since the US mandates no per-dish disclosure.
            discount = _COVERAGE_DISCOUNT * coverage_fraction * absence_inference_factor(region)
            lowered = max(severity_floor, risk * (1.0 - discount))
            if lowered < risk:
                risk = lowered
                confidence = max(confidence, 0.5 + 0.2 * coverage_fraction)
                basis = "menu_coverage"
                rationale.append(
                    f"Reviewed {parsed_count} menu items; none name {allergen_label} "
                    "-- mild reassurance only (no allergen chart; ask staff)."
                )

    if matrix_present and matrix_covers_nuts and matrix_trust < 0.85:
        rationale.append(
            "Allergen data here is an off-site or older copy -- treat as indicative "
            "and verify directly."
        )

    # A 'may contain' / cross-contact warning raises / holds the floor, weighted by
    # the user's cross-contact sensitivity (NOT their ingestion severity): a
    # NOT_CONCERNED user is unaffected, a trace-sensitive one is pushed to caution.
    if signals.cross_contact_warning:
        cc_floor = _CC_WARNING_FLOOR[cross_contact]
        if cc_floor and risk < cc_floor:
            risk = cc_floor
            rationale.append("Cross-contact / 'may contain' warning present.")
        if cc_floor and basis == "cuisine_prior":
            basis = "restaurant_signal"

    # PER-NUT cross-contact allowance: the user picked specific nuts, NONE of them are
    # present, but the kitchen grounds OTHER nuts -- shared-equipment reality. Add a
    # small bump scaled by the user's cross-contact sensitivity. Deliberately minor
    # and capped (per the product spec: "a little bump, nothing significant"); it can
    # never on its own push a clean place into AVOID (presence is required for that).
    if wanted_nuts is not None and other_nut_terms and not presence:
        bump = _XNUT_CC_BUMP.get(cross_contact, 0.0)
        bumped = min(_clamp(risk + bump), _XNUT_CC_CEIL)
        if bumped > risk:
            risk = bumped
            confidence = max(confidence, 0.4)
            rationale.append(
                "Kitchen handles other nuts you didn't select "
                f"({', '.join(sorted(other_nut_terms)[:3])}) -- small cross-contact "
                "allowance added (verify if you react to traces)."
            )

    # Tier C: community / anecdotal adjustment (asymmetric, bounded, provenance-tagged).
    risk, confidence, community_reported, community_notes = _apply_community(
        risk=risk,
        confidence=confidence,
        community=community,
        families=families,
        presence=presence,
    )
    rationale.extend(community_notes)

    tier = _tier_for(
        risk=risk,
        severity=severity,
        caution_threshold=caution_threshold,
        presence=presence,
        matrix_hit=bool(matrix_hit_items),
        navigable=navigable,
        community_reported=community_reported,
    )

    # Per-dish guidance: when a chart confirms specific dishes, surface THOSE (the
    # real "avoid these" list) instead of the prior's name-based guesses. Otherwise
    # surface named + suspected dishes, tagging suspected ones (low-confidence guess).
    if matrix_hit_items:
        riskiest = [{"itemName": n, "risk": 0.95, "confidence": 0.9, "suspected": False}
                    for n in matrix_hit_items[:8]]
    else:
        riskiest = []
        for d in base.item_details[:8]:
            is_suspected = d.get("basis") == "suspected_nuts"
            if d["risk"] >= 0.35 or d["name"] in risky_names:
                riskiest.append({
                    "itemName": d["name"], "risk": round(d["risk"], 3),
                    "confidence": round(d.get("confidence", 0.5), 2),
                    "suspected": is_suspected,
                })

    return AllergenAssessment(
        allergen=pref.allergen,
        severity=severity.name.lower(),
        risk=round(_clamp(risk), 3),
        confidence=round(confidence, 2),
        tier=tier.value,
        basis=basis,
        rationale=rationale,
        riskiest_items=riskiest,
        community_reported=community_reported,
        menu_total=parsed_count,
        menu_flagged=risky_count,
        menu_suspected=suspected_count,
        navigable=navigable,
    )


def _tier_for(
    *,
    risk: float,
    severity: Severity,
    caution_threshold: float,
    presence: bool,
    matrix_hit: bool,
    navigable: bool,
    community_reported: bool,
) -> Tier:
    """Evidence outranks inference. AVOID needs grounded presence (or strong,
    consistent community reports). A prior alone caps at CAUTION. A matrix hit is
    AVOID UNLESS it's navigable (safe dishes exist, not pervasive, non-anaphylactic)
    -- then it's CAUTION with per-dish guidance."""
    if matrix_hit and not navigable:
        return Tier.AVOID
    # Text-only presence has no per-dish chart to navigate by -> stays AVOID.
    if presence and not matrix_hit and severity.rank >= Severity.INTOLERANCE.rank:
        return Tier.AVOID
    if community_reported and risk >= 0.5 and severity.rank >= Severity.ALLERGY.rank:
        return Tier.AVOID
    if risk >= caution_threshold:
        return Tier.CAUTION
    return Tier.LIKELY_OK


def _apply_community(
    *,
    risk: float,
    confidence: float,
    community: Sequence[CommunitySignal],
    families: set[str],
    presence: bool,
) -> tuple[float, float, bool, list[str]]:
    """Pure, asymmetric, bounded community adjustment. Adverse reports RAISE risk;
    positive reports do NOT lower it (they only feed Handling, handled elsewhere).
    Returns (risk, confidence, community_reported, rationale_notes)."""
    if not community:
        return risk, confidence, False, []

    notes: list[str] = []
    adverse = [
        c for c in community
        if c.type in ("adverse_event", "allergen_presence") and _community_matches(c, families)
    ]
    poor = [c for c in community if c.type == "poor_handling"]
    mismatch = [
        c for c in community
        if c.type == "adverse_event" and c.allergen and not _community_matches(c, families)
    ]

    delta = 0.0
    community_reported = False
    if adverse:
        weight = _community_weight(adverse)
        delta += min(0.25, 0.25 * weight)
        confidence = max(confidence, min(0.6, 0.30 + 0.10 * len(adverse)))
        community_reported = True
        quote = next((c.quote for c in adverse if c.quote), "")
        notes.append(
            "Community-reported reaction"
            + (f': "{quote[:120]}"' if quote else " (diner review, unverified).")
        )
    if poor:
        delta += min(0.05, 0.05 * _community_weight(poor))

    new_risk = _clamp(risk + delta)
    # Only flag provenance when community actually moved the number.
    community_reported = community_reported and new_risk > risk + 1e-9
    if mismatch:
        other = next((c.allergen for c in mismatch if c.allergen), "another allergen")
        notes.append(f"Other diners reported {other} reactions (not your allergen).")
    return new_risk, confidence, community_reported, notes


def _restaurant_handling(
    signals: RestaurantSignals, community: Sequence[CommunitySignal]
) -> Handling:
    handling = Handling(
        cross_contact_warning=signals.cross_contact_warning,
        ask_staff=signals.ask_staff,
        allergen_menu=signals.allergen_menu_available,
        nut_free_claim=signals.nut_free_claim,
    )
    handling.allergy_aware = (
        signals.allergen_menu_available or signals.ask_staff or signals.allergy_disclaimer
    )
    for c in community:
        if c.type == "good_handling":
            handling.community_praise += 1
            handling.allergy_aware = True
        elif c.type == "poor_handling":
            handling.community_concern += 1
    return handling


def _community_matches(signal: CommunitySignal, families: set[str]) -> bool:
    """An adverse report counts toward this allergen if it names a matching
    allergen, or names none at all (an unspecified 'allergic reaction' report)."""
    if not signal.allergen:
        return True
    return bool(_families(signal.allergen) & families)


def _community_weight(signals: Sequence[CommunitySignal]) -> float:
    """0..1 strength of a set of consistent community reports: stronger when the
    allergen is named, the report is recent, and multiple reports agree."""
    best = 0.0
    for sig in signals:
        base = 0.5
        if sig.allergen:
            base += 0.2
        if sig.age_days is not None:
            if sig.age_days <= 365:
                base *= 1.0
            elif sig.age_days <= 730:
                base *= 0.6
            else:
                base *= 0.3
        best = max(best, base)
    best = min(1.0, best + 0.15 * (len(signals) - 1))
    return best


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def rank_restaurants_for_user(
    restaurants: Sequence[Any],
    *,
    get_assessment: Callable[[Any], UserAllergenAssessment],
    get_quality: Callable[[Any], float] | None = None,
) -> list[Any]:
    """Order restaurants safest-first for this user. Safety leads; restaurant
    quality is only an in-tier tiebreak and never overrides the tier."""

    def sort_key(restaurant: Any) -> tuple[int, float, float, float]:
        assessment = get_assessment(restaurant)
        quality = float(get_quality(restaurant)) if get_quality else 0.0
        return (
            Tier(assessment.tier).rank,        # likely_ok < caution < avoid
            assessment.overall_risk,           # lower risk first
            -assessment.overall_confidence,    # break ties toward more-certain
            -quality,                          # then better restaurants
        )

    return sorted(restaurants, key=sort_key)


# --------------------------------------------------------------------------- #
# Convenience wrapper (ergonomic for local_app; keeps the core pure)
# --------------------------------------------------------------------------- #
def assess_restaurant_record(
    record: Any,
    profile: UserProfile,
    *,
    menu_items: Sequence[Any] | None = None,
    signals: RestaurantSignals | None = None,
    community: Sequence[CommunitySignal] | None = None,
    cuisines: list[str] | None = None,
    region: str | None = None,
) -> UserAllergenAssessment:
    """Derive cuisines + region from a ``RestaurantRecord``-shaped object, then
    score. The core ``score_restaurant_for_user`` stays pure (cuisines + region
    explicit) for testability. A caller that already derived ``cuisines`` /
    ``region`` (e.g. to render the prior) can pass them in to avoid recomputing."""
    if cuisines is None:
        cuisines = normalize_cuisine(getattr(record, "categories", None) or [])
    if region is None:
        region = region_from_address(
            getattr(record, "address", None),
            latitude=getattr(record, "latitude", None),
            longitude=getattr(record, "longitude", None),
        )
    return score_restaurant_for_user(
        profile,
        cuisines=cuisines,
        region=region,
        menu_items=menu_items,
        signals=signals,
        community=community,
        official_domain=_domain_of(getattr(record, "website_url", None)),
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _pull_down(risk: float, *, strength: float, labeling_trust: float, floor: float) -> float:
    """Apply a clean down-signal: lower ``risk`` proportional to the signal's
    strength x how much a missing label can be trusted here, never below ``floor``."""
    effective = strength * labeling_trust
    return max(floor, risk * (1.0 - effective))
