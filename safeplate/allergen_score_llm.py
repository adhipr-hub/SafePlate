"""ALTERNATE scoring engine: an LLM-as-scorer that runs PARALLEL to the deterministic
``allergen_score.score_restaurant_for_user`` (which stays the default).

Idea (user-requested): instead of the rule-based fusion ("British -> 0.3"), serialize
ALL inputs into a structured evidence bundle, hand it to the LLM under a strict safety
policy, and use its holistic score for ranking -- with each rationale claim CITING the
evidence it used (so the drawer can make those citations clickable).

SAFETY = HYBRID, never pure-LLM. We FIRST run the deterministic scorer to get the
ground-truth facts + floor, then let the LLM weigh things, then GUARDRAIL the output:
- GROUNDED presence (allergen chart / menu text confirms the allergen in a dish) ->
  the LLM may NOT undercut it (tier >= deterministic, risk >= deterministic).
- INFERENCE only (cuisine/dish prior) -> the LLM may refine the crude prior freely
  (this is the whole point), but stays within [severity floor, 0.97] and cannot invent
  a grounded AVOID from inference alone.
- Every citation must reference a real evidence ID from the bundle, else it's dropped.

Output shape == ``UserAllergenAssessment`` so it's a drop-in for ranking + the UI. The
LLM call (``_call_llm_scorer``) is monkeypatchable for tests; live use needs Gemini.
"""

from __future__ import annotations

from typing import Any, Sequence

from safeplate.allergen_prior import (
    NUTS,
    cuisines_for,
    labeling_trust_for_region,
    normalize_cuisine,
    region_from_address,
)
from safeplate.allergen_score import (
    DISCLAIMER,
    AllergenAssessment,
    CommunitySignal,
    RestaurantSignals,
    Severity,
    Tier,
    UserAllergenAssessment,
    UserProfile,
    _SEVERITY_TUNING,
    _domain_of,
    score_restaurant_for_user,
)

DEFAULT_MODEL = "gemini-3.1-flash-lite"

_GROUNDED_BASES = ("allergen_matrix", "menu_evidence")

# v3 may refine the (now navigability-aware) deterministic score DOWN freely, but
# only UP within this band -- so a holistic LLM guess can't balloon a navigable,
# clearly-labeled restaurant back to "avoid everything". The deterministic scorer is
# the calibrated anchor; v3 explains + nudges, it doesn't override navigability.
_V3_UPWARD_BAND = 0.20

_MATRIX_LABEL_COVERAGE = 0.6  # share of dishes from a chart to count a menu "labeled"
_FULLMENU_MAX = 120           # cap dishes sent in the raw-menu scenario (token bound)

_SCORER_SYSTEM = (
    "You are SafePlate's allergen RISK SCORER. For ONE restaurant and ONE user's "
    "allergy, return a risk in [0,1] (higher = more dangerous), a tier, a confidence, "
    "and a rationale where each claim lists the evidence ids it used.\n"
    "The question is 'how hard is it for THIS user to eat here safely?' -- NOT 'are "
    "there any nuts?'. A few clearly-avoidable nut dishes among many safe ones is a "
    "GOOD option, not a dangerous one.\n"
    "Each restaurant arrives in ONE of three shapes (see its `scenario` field):\n"
    " - 'labeled': a `chart_summary` taken from the restaurant's OWN per-item allergen "
    "chart (AUTHORITATIVE). It tells you exactly how many dishes contain the allergen "
    "and which. Trust it; score from that ratio + the safety factors below.\n"
    " - 'raw_menu': a `menu` list of dish names with NO allergen labels. YOU decide "
    "which dishes likely involve the allergen using real culinary knowledge (e.g. "
    "korma/satay/pesto/baklava/pad thai likely; grilled salmon, fries, soda unlikely), "
    "then judge navigability across the whole menu. Do not assume a dish is safe just "
    "because its NAME omits the nut.\n"
    " - 'no_menu': no menu was found. Judge from cuisine, region, and any restaurant / "
    "community allergy signals only.\n"
    "SAFETY POLICY:\n"
    "1. NAVIGABILITY IS SAFETY: allergen confined to a few avoidable dishes + many safe "
    "options -> low/moderate (caution), not high. Don't pin a place at its worst dish.\n"
    "2. REWARD TRANSPARENCY & ACCOMMODATION: a chart, allergy disclaimer, 'ask staff' "
    "lower the score. Never punish a restaurant for labeling its allergens.\n"
    "3. HIGH RISK for: allergen PERVASIVE/unavoidable; trace-sensitive user + a kitchen "
    "that uses it; or unknown AND a high-nut cuisine with no handling signals.\n"
    "4. ABSENCE IS NOT SAFETY: a missing mention never means safe; never go below "
    "'likely_ok'. An UNCONFIRMED judgement (raw_menu or cuisine) caps at 'caution'; "
    "only a confirmed chart hit can be 'avoid'.\n"
    "5. Community/anecdotal reports raise risk, never lower it. Weigh severity and "
    "cross-contact tolerance.\n"
    "6. Cite ONLY evidence ids present in THAT restaurant's `evidence`; name specific "
    "dishes for chart/menu claims. Do not invent dishes or evidence. Be concise."
)

_SCORER_SCHEMA = {
    "type": "object",
    "properties": {
        "risk": {"type": "number"},
        "tier": {"type": "string", "enum": ["likely_ok", "caution", "avoid"]},
        "confidence": {"type": "number"},
        "rationale": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["claim"],
            },
        },
    },
    "required": ["risk", "tier", "rationale"],
}

# Batch scoring: ONE Gemini call ranks every restaurant in a search (N calls -> 1).
# Each is scored INDEPENDENTLY (and may be in a different scenario); guardrails are
# applied per restaurant.
_SCORER_SYSTEM_BATCH = (
    _SCORER_SYSTEM
    + "\n\nYou are given MANY restaurants (each with its own id, scenario, and evidence "
    "ids). Score EACH one INDEPENDENTLY -- one restaurant's data must not influence "
    "another's. Cite only that restaurant's evidence ids. Return one entry per "
    "restaurant, echoing its id."
)

_SCORER_SCHEMA_BATCH = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, **_SCORER_SCHEMA["properties"]},
                "required": ["id", "risk", "tier", "rationale"],
            },
        }
    },
    "required": ["scores"],
}

def _item_name(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("item_name") or item.get("name") or "").strip()
    return str(getattr(item, "item_name", None) or getattr(item, "name", None) or "").strip()


def _item_terms(item: Any) -> list[str]:
    v = item.get("allergen_terms") if isinstance(item, dict) else getattr(item, "allergen_terms", None)
    return [str(t) for t in (v or [])]


def _item_method(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("extraction_method") or "")
    return str(getattr(item, "extraction_method", "") or "")


_MAX_HISTORY = 30


def _clean_history(raw: Any) -> list[dict[str, Any]]:
    """Sanitize untrusted client history: keep at most _MAX_HISTORY entries with a
    non-empty name, an int rating clamped to 1-10, and a short note."""
    out: list[dict[str, Any]] = []
    for e in (raw or []):
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()[:120]
        if not name:
            continue
        try:
            rating = int(e.get("rating"))
        except (TypeError, ValueError):
            continue
        rating = max(1, min(10, rating))
        note = str(e.get("note") or "").strip()[:300]
        out.append({"name": name, "rating": rating, "note": note})
        if len(out) >= _MAX_HISTORY:
            break
    return out


def _scenario(menu_items: Sequence[Any] | None) -> str:
    """Route a restaurant to one of three LLM-scoring shapes:
      'labeled'  -- a comprehensive per-item allergen chart exists -> trust the labels;
      'raw_menu' -- a menu exists but is NOT labeled -> the LLM judges the dishes;
      'no_menu'  -- nothing parsed -> judge from cuisine/region/signals only.
    'labeled' requires a CHART covering most dishes; a few stray tags do NOT count
    (the unlabeled rest would be wrongly assumed safe)."""
    named = [it for it in (menu_items or []) if _item_name(it)]
    if not named:
        return "no_menu"
    charted = sum(1 for it in named if "matrix" in _item_method(it).lower())
    if charted / len(named) >= _MATRIX_LABEL_COVERAGE:
        return "labeled"
    return "raw_menu"


def _compact_menu(menu_items: Sequence[Any] | None) -> list[str]:
    """Token-cheap menu: deduped dish names, allergen tags inlined only when present,
    capped at ``_FULLMENU_MAX`` so a huge menu can't blow up the prompt."""
    seen: set[str] = set()
    out: list[str] = []
    for item in menu_items or []:
        name = _item_name(item)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        terms = sorted(set(_item_terms(item)))
        out.append(f"{name} [{', '.join(terms)}]" if terms else name)
        if len(out) >= _FULLMENU_MAX:
            break
    return out


def score_restaurant_with_llm(
    profile: UserProfile,
    *,
    cuisines: list[str] | None,
    region: str = "unknown",
    menu_items: Sequence[Any] | None = None,
    signals: RestaurantSignals | None = None,
    community: Sequence[CommunitySignal] | None = None,
    official_domain: str | None = None,
    name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> UserAllergenAssessment:
    """Label-routing LLM score: per restaurant it picks 'labeled' (trust the chart),
    'raw_menu' (LLM judges the dishes), or 'no_menu' (context only). Falls back to the
    deterministic assessment unchanged if there is no API key or the LLM call fails."""
    det = score_restaurant_for_user(
        profile,
        cuisines=cuisines,
        region=region,
        menu_items=menu_items,
        signals=signals,
        community=community,
        official_domain=official_domain,
    )
    if not profile.allergens or not api_key:
        return det

    severity = profile.allergens[0].severity
    bundle = _build_bundle(
        profile=profile, cuisines=cuisines or [], region=region, det=det,
        signals=signals, community=community, menu_items=menu_items, name=name,
    )
    try:
        llm = _call_llm_scorer(
            bundle, api_key=api_key, model=model or DEFAULT_MODEL, system=_SCORER_SYSTEM
        )
    except Exception:
        return det  # fail closed to the deterministic assessment

    return _apply_guardrails(llm, det=det, severity=severity, bundle=bundle)


def assess_restaurant_record_with_llm(
    record: Any,
    profile: UserProfile,
    *,
    menu_items: Sequence[Any] | None = None,
    signals: RestaurantSignals | None = None,
    community: Sequence[CommunitySignal] | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> UserAllergenAssessment:
    name = getattr(record, "name", None)
    cuisines = cuisines_for(getattr(record, "categories", None) or [], name)
    region = region_from_address(
        getattr(record, "address", None),
        latitude=getattr(record, "latitude", None),
        longitude=getattr(record, "longitude", None),
    )
    return score_restaurant_with_llm(
        profile, cuisines=cuisines, region=region, menu_items=menu_items,
        signals=signals, community=community,
        official_domain=_domain_of(getattr(record, "website_url", None)),
        name=name, api_key=api_key, model=model,
    )


def score_restaurants_with_llm_batch(
    requests: Sequence[dict[str, Any]],
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, UserAllergenAssessment]:
    """Score MANY restaurants in ONE LLM call (the scalability win: N calls -> 1).

    Each ``request`` is a dict with: ``id`` (str), ``profile``, and the same scoring
    inputs as ``score_restaurant_with_llm`` (``cuisines``, ``region``, ``menu_items``,
    ``signals``, ``community``, ``official_domain``). Every restaurant is scored
    DETERMINISTICALLY first (facts + floor) and LABEL-ROUTED into its own scenario
    (labeled / raw_menu / no_menu); all bundles go in a single Gemini call, and
    per-restaurant guardrails are applied. Returns ``id -> assessment``. Any restaurant
    the LLM omits -- or the whole call failing / no key -- falls back to that
    restaurant's own deterministic assessment, so it is always safe.
    """
    dets: dict[str, UserAllergenAssessment] = {}
    bundles: dict[str, dict[str, Any]] = {}
    severities: dict[str, Severity] = {}

    for req in requests:
        rid = str(req["id"])
        profile = req["profile"]
        det = score_restaurant_for_user(
            profile,
            cuisines=req.get("cuisines"),
            region=req.get("region", "unknown"),
            menu_items=req.get("menu_items"),
            signals=req.get("signals"),
            community=req.get("community"),
            official_domain=req.get("official_domain"),
        )
        dets[rid] = det
        if not profile.allergens:
            continue
        severities[rid] = profile.allergens[0].severity
        bundles[rid] = _build_bundle(
            profile=profile, cuisines=req.get("cuisines") or [],
            region=req.get("region", "unknown"), det=det,
            signals=req.get("signals"), community=req.get("community"),
            menu_items=req.get("menu_items"), name=req.get("name"),
        )

    out: dict[str, UserAllergenAssessment] = dict(dets)  # default to deterministic
    if not api_key or not bundles:
        return out
    try:
        scored = _call_llm_scorer_batch(
            bundles, api_key=api_key, model=model or DEFAULT_MODEL, system=_SCORER_SYSTEM_BATCH
        )
    except Exception:
        return out  # fail closed -- every restaurant keeps its deterministic score

    for rid, llm in scored.items():
        if rid in bundles:
            out[rid] = _apply_guardrails(
                llm, det=dets[rid], severity=severities[rid], bundle=bundles[rid]
            )
    return out


def _call_llm_scorer_batch(
    bundles: dict[str, dict[str, Any]], *, api_key: str, model: str,
    system: str = _SCORER_SYSTEM_BATCH,
) -> dict[str, dict[str, Any]]:
    """One Gemini call scoring every restaurant. Returns id -> raw scorer JSON.
    Monkeypatched in tests; live use is bounded by the global Gemini semaphore."""
    import json

    from safeplate.extraction2.interpret_llm import _call_with_retry

    batch = {"restaurants": [{"id": rid, **bundle} for rid, bundle in bundles.items()]}
    request = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": "Restaurants to score:\n\n" + json.dumps(batch)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": _SCORER_SCHEMA_BATCH,
        },
    }
    resp = _call_with_retry(request, api_key=api_key, model=model)
    result: dict[str, dict[str, Any]] = {}
    for entry in (resp or {}).get("scores", []):
        if isinstance(entry, dict) and entry.get("id") is not None:
            result[str(entry["id"])] = entry
    return result


# --------------------------------------------------------------------------- #
def _build_bundle(
    *,
    profile: UserProfile,
    cuisines: list[str],
    region: str,
    det: UserAllergenAssessment,
    signals: RestaurantSignals | None,
    community: Sequence[CommunitySignal] | None,
    menu_items: Sequence[Any] | None,
    name: str | None = None,
    experience_history: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build ONE restaurant's bundle, routed by label coverage:
      - 'labeled'  -> a `chart_summary` (authoritative per-item counts); no raw menu.
      - 'raw_menu' -> the compact `menu` (LLM decides which dishes involve nuts); we do
                      NOT feed our keyword guesses, so the LLM judges fresh.
      - 'no_menu'  -> neither; judge from cuisine/region/signals.
    All carry the user, a rough deterministic_baseline (the guardrail anchor), and
    cuisine/handling/community evidence (E#-cited)."""
    pref = profile.allergens[0]
    a = det.per_allergen[0] if det.per_allergen else None
    grounded = bool(a) and a.basis in _GROUNDED_BASES
    scenario = _scenario(menu_items)

    evidence: list[dict[str, Any]] = []

    def add(typ: str, text: str, **extra: Any) -> None:
        evidence.append({"id": f"E{len(evidence) + 1}", "type": typ, "text": text, **extra})

    add("cuisine",
        (f'Restaurant name: "{name}". ' if name else "")
        + f"Cuisine(s): {', '.join(cuisines) or 'unknown — infer the likely cuisine from the name'}; "
        + f"region {region}; allergen-labeling trust {labeling_trust_for_region(region):.2f}.")
    if signals:
        for label, val in (
            ("allergy disclaimer / allergy-aware", signals.allergy_disclaimer),
            ("cross-contact / 'may contain' warning", signals.cross_contact_warning),
            ("tells customers to ask staff about allergies", signals.ask_staff),
            ("publishes an allergen menu/chart", signals.allergen_menu_available),
            ("explicit nut-free claim", signals.nut_free_claim),
        ):
            if val:
                add("handling", f"Restaurant signal: {label}.")
    for c in community or []:
        add("community", f"Community {c.type}: \"{(c.quote or '')[:140]}\".",
            ctype=c.type, url=getattr(c, "url", "") or "", quote=(c.quote or ""))

    bundle: dict[str, Any] = {
        "scenario": scenario,
        "user": {
            "allergen": pref.allergen,
            # The SPECIFIC nuts the user reacts to (when they narrowed it): judge only
            # these as 'contains'; other nuts matter only as cross-contact. "all" means
            # every nut (the default).
            "nuts": (sorted(pref.nut_types) if pref.nut_types else "all"),
            "severity": pref.severity.name.lower(),
            "cross_contact": (pref.cross_contact.name.lower() if pref.cross_contact else "default"),
        },
        "deterministic_baseline": {
            "risk": det.overall_risk, "tier": det.tier, "basis": det.evidence_basis,
            "grounded_presence": grounded,
            "note": "a rough rule-based prior; trust the chart/menu over this",
        },
        "evidence": evidence,
    }

    if scenario == "labeled":
        # Authoritative per-item chart: feed the counts + the confirmed nut dishes.
        nut_dishes = [it["itemName"] for it in (a.riskiest_items if a else [])
                      if not it.get("suspected")]
        bundle["chart_summary"] = {
            "source": "the restaurant's own per-item allergen chart (authoritative)",
            "total_dishes": getattr(a, "menu_total", 0) if a else 0,
            "dishes_with_nuts": getattr(a, "menu_flagged", 0) if a else 0,
            "nut_dishes": nut_dishes[:20],
        }
    elif scenario == "raw_menu":
        # Hand over the raw dish names; the LLM identifies nut dishes itself.
        bundle["menu"] = _compact_menu(menu_items)
    hist = _clean_history(experience_history)
    if hist:
        # The diner's own rated experiences -> the scorer infers their demonstrated
        # tolerance and calibrates THIS restaurant toward how they'd actually fare.
        bundle["your_history"] = hist
    return bundle


def _call_llm_scorer(
    bundle: dict[str, Any], *, api_key: str, model: str, system: str = _SCORER_SYSTEM
) -> dict[str, Any]:
    """Single Gemini call returning the scorer JSON. Monkeypatched in tests; live use
    is bounded by the global Gemini semaphore in gemini_menu and the retry/backoff."""
    import json

    from safeplate.extraction2.interpret_llm import _call_with_retry

    request = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": "Evidence bundle:\n\n" + json.dumps(bundle)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": _SCORER_SCHEMA,
        },
    }
    return _call_with_retry(request, api_key=api_key, model=model)


def _apply_guardrails(
    llm: dict[str, Any], *, det: UserAllergenAssessment, severity: Severity,
    bundle: dict[str, Any],
) -> UserAllergenAssessment:
    valid_ids = {e["id"] for e in bundle.get("evidence", [])}
    det_tier = Tier(det.tier)
    grounded = det.evidence_basis in _GROUNDED_BASES
    floor = _SEVERITY_TUNING[severity][1]

    llm_tier = _parse_tier(llm.get("tier"), default=det_tier)
    risk = _to_float(llm.get("risk"), default=det.overall_risk)
    confidence = _to_float(llm.get("confidence"), default=det.overall_confidence)

    # The deterministic scorer is now navigability-aware and well-calibrated, so it
    # anchors v3: the LLM explains + nudges within a band, it does not override the
    # navigability verdict (which is what made v3 over-warn labeled chains).
    ceiling = min(0.97, det.overall_risk + _V3_UPWARD_BAND)
    if grounded:
        # Rules are authoritative on confirmed presence + navigability: keep the det
        # tier (no escalation), don't undercut the grounded floor, cap the over-warn.
        tier = det_tier
        risk = _clamp(risk, lo=det.overall_risk, hi=ceiling)
    else:
        # Inference: v3 may refine the crude prior DOWN freely (its purpose), capped
        # at CAUTION upward (no grounded AVOID from a guess) unless the deterministic
        # layer already escalated via community; the ceiling stops it ballooning.
        cap = det_tier if det.community_reported else Tier.CAUTION
        tier = _by_rank(min(llm_tier.rank, cap.rank))
        risk = _clamp(risk, lo=floor, hi=ceiling)

    # Ground the citations: drop any evidence id the bundle doesn't contain.
    rationale: list[str] = []
    for entry in llm.get("rationale", []):
        if not isinstance(entry, dict):
            continue
        claim = str(entry.get("claim", "")).strip()
        if not claim:
            continue
        cites = [cid for cid in (entry.get("evidence_ids") or []) if cid in valid_ids]
        rationale.append(claim + (f" [{', '.join(cites)}]" if cites else ""))
    if not rationale:
        rationale = list(det.rationale)

    base = det.per_allergen[0] if det.per_allergen else None
    per = [AllergenAssessment(
        allergen=(base.allergen if base else NUTS),
        severity=severity.name.lower(),
        risk=round(risk, 3),
        confidence=round(confidence, 2),
        tier=tier.value,
        basis=det.evidence_basis,
        rationale=rationale,
        riskiest_items=(base.riskiest_items if base else []),
        community_reported=det.community_reported,
    )]
    return UserAllergenAssessment(
        overall_risk=round(risk, 3),
        overall_confidence=round(_clamp(confidence, lo=0.0, hi=1.0), 2),
        tier=tier.value,
        evidence_basis=det.evidence_basis,
        per_allergen=per,
        handling=det.handling,
        rationale=rationale,
        community_reported=det.community_reported,
        disclaimer=DISCLAIMER,
        # Carry the citable evidence (with source URLs where known) so the UI can link
        # each [E#] chip in the rationale straight to where the claim came from.
        evidence=list(bundle.get("evidence", [])),
    )


# --------------------------------------------------------------------------- #
def _parse_tier(value: Any, *, default: Tier) -> Tier:
    try:
        return Tier(str(value).strip().lower())
    except (ValueError, AttributeError):
        return default


def _worse(a: Tier, b: Tier) -> Tier:
    return a if a.rank >= b.rank else b


def _by_rank(rank: int) -> Tier:
    for t in Tier:
        if t.rank == rank:
            return t
    return Tier.CAUTION


def _to_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, *, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
