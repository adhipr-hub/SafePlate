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
asymmetric, bounded delta math lives here (``_apply_community``) and is
unit-tested with synthetic signals -- but NOTHING populates ``community`` yet.
The Places-reviews fetch + LLM classification (``community_signals.py``) is
deferred until the Gemini path is back; callers pass ``community=None`` today and
the seam is a clean drop-in. Per the locked design, community is SAFETY-
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
    PEANUTS,
    TREE_NUTS,
    labeling_trust_for_region,
    normalize_cuisine,
    region_from_address,
    restaurant_nut_risk,
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


@dataclass(frozen=True)
class UserProfile:
    allergens: tuple[AllergenPref, ...] = ()

    @classmethod
    def for_nuts(
        cls,
        severity: Severity = Severity.ALLERGY,
        cross_contact: CrossContactSensitivity | None = None,
    ) -> "UserProfile":
        """Convenience for the nuts-only first build."""
        return cls(allergens=(
            AllergenPref(allergen=NUTS, severity=severity, cross_contact=cross_contact),
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
}
_TREE_NUT_TERMS = {
    "tree nut", "treenut", "tree-nut",
    "almond", "cashew", "walnut", "pecan", "hazelnut", "pistachio", "macadamia",
    "chestnut", "pine nut", "pinenut", "brazil nut",
    "almendra", "amande", "mandel", "mandorla", "anacardo", "cajou", "avellana",
    "noisette", "haselnuss", "nocciola", "pistacho", "pistache", "pistazie",
    "pistacchio", "walnuss",
}
_GENERIC_NUT_TERMS = {"nut", "nuts"}


def _families(allergen: str) -> set[str]:
    if allergen == NUTS:
        return {PEANUTS, TREE_NUTS}
    return {allergen}


def _nut_terms_present(allergen_terms: Sequence[str], families: set[str]) -> list[str]:
    """Return the allergen_terms that match one of the wanted nut families."""
    if not families & {PEANUTS, TREE_NUTS}:
        return []  # non-nut allergens not modelled in the nuts-only build
    hits: list[str] = []
    for raw in allergen_terms or []:
        term = str(raw).strip().lower()
        if not term:
            continue
        is_peanut = any(p in term for p in _PEANUT_TERMS)
        is_tree = any(t in term for t in _TREE_NUT_TERMS)
        is_generic = term in _GENERIC_NUT_TERMS or term == "nut"
        if (PEANUTS in families and is_peanut) or (TREE_NUTS in families and is_tree):
            hits.append(term)
        elif is_generic:  # bare "nuts" counts for any nut user
            hits.append(term)
    return hits


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

# Floor a 'may contain' / cross-contact warning imposes, keyed on the user's
# cross-contact sensitivity (NOT their ingestion severity). A NOT_CONCERNED user is
# unaffected by such a warning; a trace-sensitive user is pushed to caution.
_CC_WARNING_FLOOR: dict[CrossContactSensitivity, float] = {
    CrossContactSensitivity.NOT_CONCERNED: 0.0,
    CrossContactSensitivity.MODERATE: 0.35,
    CrossContactSensitivity.STRICT: 0.45,
}


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
    """Fuse prior + grounded evidence + signals (+ community seam) into one
    per-user assessment. ``community`` is accepted but unpopulated today.
    ``official_domain`` (the restaurant's own domain) lets provenance weighting
    distrust off-site / stale allergen sources' clean signals."""
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
    families = _families(pref.allergen)
    severity = pref.severity
    cross_contact = _effective_cross_contact(severity, pref.cross_contact)
    caution_threshold, severity_floor = _SEVERITY_TUNING[severity]
    labeling_trust = labeling_trust_for_region(region)

    # T4 + T5: cuisine/location floor + dish-name priors (reuses the prior layer).
    base = restaurant_nut_risk(
        cuisines=cuisines,
        region=region,
        menu_items=[
            {
                "item_name": _field(item, "item_name") or _field(item, "name") or "",
                "description": _field(item, "description") or "",
            }
            for item in menu_items
        ],
        allergen=pref.allergen,
    )
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
    for item in menu_items:
        method = _field(item, "extraction_method") or ""
        terms = _field(item, "allergen_terms") or []
        name = (_field(item, "item_name") or _field(item, "name") or "").strip()
        hits = _nut_terms_present(terms, families)
        if _is_matrix_method(method):
            matrix_present = True
            matrix_source_urls.append(_field(item, "menu_source_url") or "")
            if name:
                matrix_dish_total += 1
            if hits and name:
                matrix_hit_items.append(name)
        elif hits and name:
            text_hit_items.append(name)

    # Provenance trust of the allergen chart -- the most conservative (lowest) of
    # its source(s). A stale/off-site chart should not strongly vouch for absence.
    matrix_trust = min(
        (_source_trust(url, official_domain) for url in matrix_source_urls), default=1.0
    )

    # Per-dish navigability: a complete chart that marks the allergen in only a few
    # of many dishes is NAVIGABLE (avoid those, eat the rest) -> CAUTION + per-dish
    # guidance, not a blanket AVOID. Stays AVOID when nuts are pervasive or the user
    # is trace-sensitive (a kitchen using nuts is then a cross-contact risk on every
    # dish). Note this is gated on CROSS-CONTACT concern, not ingestion severity: an
    # anaphylactic user who isn't worried about traces can still navigate the menu.
    safe_dish_count = max(0, matrix_dish_total - len(matrix_hit_items))
    pervasive = matrix_dish_total > 0 and len(matrix_hit_items) / matrix_dish_total >= 0.6
    navigable = (
        bool(matrix_hit_items)
        and safe_dish_count >= 3
        and not pervasive
        and cross_contact.rank < CrossContactSensitivity.STRICT.rank
    )

    presence = False
    if matrix_hit_items:
        # T1: a dish x allergen chart marks the user's allergen present. Confirmed.
        presence = True
        basis = "allergen_matrix"
        more = len(matrix_hit_items) - 4
        rationale.append(
            f"Allergen chart marks {pref.allergen} present in: "
            + ", ".join(matrix_hit_items[:4])
            + (f" (+{more} more)" if more > 0 else "")
        )
        if navigable:
            # Some dishes contain it, but there are clearly safe options to order.
            risk = max(risk, 0.55)
            confidence = max(confidence, 0.85)
            note = (
                f"{safe_dish_count} other listed dishes are not marked {pref.allergen} -- "
                "you can likely order safely by avoiding those above"
            )
            note += (
                " (you've set trace cross-contact as not a concern)."
                if cross_contact == CrossContactSensitivity.NOT_CONCERNED
                else ", and confirm cross-contact handling with staff."
            )
            rationale.append(note)
        else:
            risk = max(risk, 0.9)
            confidence = max(confidence, 0.9)
            # Safe dishes exist and nuts aren't pervasive, yet we still say AVOID:
            # make the reason explicit so the verdict isn't a black box.
            if (
                safe_dish_count >= 3
                and not pervasive
                and cross_contact.rank >= CrossContactSensitivity.STRICT.rank
            ):
                rationale.append(
                    f"{safe_dish_count} dishes are nut-free, but you've flagged cross-contact "
                    "as a serious risk and this kitchen handles nuts -- treated as avoid."
                )
    elif text_hit_items:
        # T2: an explicit allergen mention in item/description text.
        presence = True
        risk = max(risk, 0.8)
        confidence = max(confidence, 0.7)
        basis = "menu_evidence"
        rationale.append(
            f"Menu text names {pref.allergen} in: " + ", ".join(text_hit_items[:4])
        )

    # T3: restaurant-level signals. Presence DOMINATES -- a clean signal cannot
    # erase a confirmed hit; it only applies when nothing was found present.
    if not presence:
        if matrix_present:
            # A complete chart exists and marks the allergen NOWHERE -> the clean
            # down-signal, gated by labeling trust x severity AND by how much we
            # trust this chart's PROVENANCE (off-site/stale -> weaker pull, lower
            # confidence). Presence is never discounted this way.
            risk = _pull_down(
                risk, strength=0.75,
                labeling_trust=labeling_trust * matrix_trust, floor=severity_floor,
            )
            confidence = max(confidence, 0.8 * matrix_trust)
            basis = "allergen_matrix"
            rationale.append(
                f"Allergen chart present and does not list {pref.allergen} "
                "(cross-contact still possible -- verify)."
            )
        elif signals.nut_free_claim and (families & {PEANUTS, TREE_NUTS}):
            risk = _pull_down(risk, strength=0.5, labeling_trust=labeling_trust, floor=severity_floor)
            confidence = max(confidence, 0.6)
            if basis == "cuisine_prior":
                basis = "restaurant_signal"
            rationale.append("Menu states a nut-free claim (still verify directly).")

    if matrix_present and matrix_trust < 0.85:
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
    # real "avoid these" list) instead of the prior's name-based guesses.
    if matrix_hit_items:
        riskiest = [{"itemName": n, "risk": 0.95} for n in matrix_hit_items[:8]]
    else:
        riskiest = [{"itemName": n, "risk": round(r, 3)} for n, r in base.riskiest_items]

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
) -> UserAllergenAssessment:
    """Derive cuisines + region from a ``RestaurantRecord``-shaped object, then
    score. The core ``score_restaurant_for_user`` stays pure (cuisines + region
    explicit) for testability."""
    cuisines = normalize_cuisine(getattr(record, "categories", None) or [])
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


def _clamp(value: float) -> float:
    return max(0.0, min(0.97, value))
