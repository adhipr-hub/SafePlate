"""Menu service: structured extraction + Layer-#5 scoring orchestration for one
restaurant (drawer) and the menu-backed list cards, the menu-response/summary shapes
the UI reads, and the demo menu path. Depends on ``common`` + the scoring/extraction
modules only -- never on the search or API layers (keeps the import graph acyclic)."""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

from safeplate.common import (
    _empty_validation_summary,
    _is_ai_engine,
    _menu_item_payloads,
    _safe_payload,
    _scoring_engine_from_payload,
    _string_list,
    _user_profile_from_payload,
)
from safeplate.config import (
    get_brave_search_api_key,
    get_gemini_api_key,
    get_gemini_model,
    get_user_agent,
)
from safeplate.coerce import optional_float as _optional_float
from safeplate.demo_fixtures import DemoFixtureError, load_demo_menu, load_demo_search


def run_menu_extraction(payload: dict[str, Any], *, demo_mode: bool = False) -> dict[str, Any]:
    if demo_mode:
        return _run_demo_menu_extraction(payload)
    return _run_structured_menu_extraction(payload)


def _build_restaurant_signals(allergy_signals, *, name, address, website_url):
    """Build the Layer-#5 RestaurantSignals from extracted allergy signals + the
    curated registry. Shared so the deterministic score and the batched-LLM bundle
    speak from the IDENTICAL signals (no drift between list extraction and re-score)."""
    from safeplate.allergen_score import RestaurantSignals
    from safeplate.allergy_registry import apply_registry

    signals = RestaurantSignals.from_allergy_signals(allergy_signals)
    # A verified dedicated nut-free / allergy kitchen gets a trusted nut-free /
    # allergy-aware signal even when its own site yields nothing extractable.
    apply_registry(signals, name, address, website_url)
    return signals


def _extract_and_assess_structured(
    *,
    name: str,
    website_url: str,
    address: str,
    categories: list[str],
    latitude: float | None,
    longitude: float | None,
    profile: Any,
    user_agent: str,
    api_key: str | None,
    cuisines: list[str] | None = None,
    region: str | None = None,
    scoring_engine: str = "rules",
    no_cache: bool = False,
    experience_history: list | None = None,
):
    """Run the structured extraction (result-cached) + Layer #5 assessment for one
    restaurant. Shared by the menu drawer and the menu-backed search list so both
    speak from the SAME extraction + scorer; the result cache means the second
    caller (whichever fires later) pays nothing. ``cuisines`` / ``region`` are
    derived by the scorer when not supplied; callers that already have them (the
    search card renders the prior first) pass them in to skip the re-derivation.
    Returns (assessment, menu_items, allergy_signals, coverage, errors, diet_signals)."""
    from safeplate.allergen_score import assess_restaurant_record
    from safeplate.extraction2.discover import discover_and_extract

    errors: list[dict[str, str]] = []
    menu_items: list[Any] = []
    allergy_signals: list[Any] = []
    coverage: list[Any] = []
    diet_signals: list[Any] = []  # grounded website "can be made vegan/veg" statements

    if website_url:
        try:
            _candidates, result = discover_and_extract(
                website_url,
                user_agent=user_agent,
                restaurant_name=name,
                address=address,
                api_key=api_key,
                model=get_gemini_model(),
                brave_api_key=get_brave_search_api_key(),
                # repeat opens skip all API calls; the UI "raw / no cache" toggle turns
                # BOTH the result cache and the per-source caches off for a live fetch.
                use_result_cache=not no_cache,
                use_cache=not no_cache,
            )
            menu_items = result.items
            allergy_signals = result.allergy_signals
            coverage = result.coverage
            diet_signals = list(getattr(result, "diet_signals", []) or [])
        except Exception as exc:  # never let extraction break the response
            errors.append({"source": "extraction2", "error": str(exc)})
    else:
        errors.append({"source": "website_lookup", "error": "No website URL provided."})

    signals = _build_restaurant_signals(
        allergy_signals, name=name, address=address, website_url=website_url
    )
    record = SimpleNamespace(
        categories=categories,
        address=address,
        latitude=latitude,
        longitude=longitude,
        website_url=website_url,  # lets the scorer judge source provenance
    )
    if _is_ai_engine(scoring_engine):
        # AI scorer: label-routes (trust a chart / judge the raw menu / context only),
        # with the deterministic floor + guardrails. Falls back to the deterministic
        # assessment if no key / the LLM call fails.
        from safeplate.allergen_score_llm import assess_restaurant_record_with_llm
        assessment = assess_restaurant_record_with_llm(
            record, profile, menu_items=menu_items, signals=signals,
            experience_history=experience_history,
            api_key=api_key, model=get_gemini_model(),
        )
    else:
        assessment = assess_restaurant_record(
            record, profile, menu_items=menu_items, signals=signals,
            cuisines=cuisines, region=region,
        )
    return assessment, menu_items, allergy_signals, coverage, errors, diet_signals


def _region_notice_for(
    coverage: list[Any], menu_items: list[Any], *, address: str, website_url: str
) -> dict[str, Any] | None:
    """Content-locale notice: does the SHOWN allergen/menu data come from the
    diner's region? Compares each contributing source's detected region (stamped
    on coverage) against the home region. Prefers the allergen sources (the
    safety-critical data) and surfaces a foreign region if any is present, so the
    drawer can say 'this data is from <region>, not verified for your area'."""
    from safeplate.extraction2 import region as region_mod

    home = region_mod.home_country(address, website_url)
    cov_region = {
        c.url: getattr(c, "region", "")
        for c in coverage
        if getattr(c, "url", "") and getattr(c, "region", "")
    }
    if not cov_region:
        return None

    def _regions_of(items: list[Any]) -> list[str]:
        out = []
        for it in items:
            reg = cov_region.get(getattr(it, "menu_source_url", "") or "")
            if reg:
                out.append(reg)
        return out

    # Judge provenance on the ALLERGEN data (the safety-critical part); only when
    # there are no allergen items at all do we fall back to the menu sources. (Using
    # `or` here would wrongly borrow a non-allergen page's region when the allergen
    # sources resolved to no detectable region -- the code review's L1 finding.)
    allergen_items = [it for it in menu_items if getattr(it, "allergen_terms", None)]
    regions = _regions_of(allergen_items) if allergen_items else _regions_of(menu_items)
    if not regions:
        return None
    # Surface a foreign region when present (the case worth warning about); with an
    # unknown home, any detected region is reported so we never silently trust it.
    foreign = [r for r in regions if r != home] if home else regions
    source_region = foreign[0] if foreign else regions[0]
    return region_mod.region_notice(home=home, source_region=source_region)


def _location_notice_for(
    coverage: list[Any], menu_items: list[Any], *, address: str, restaurant_name: str
) -> dict[str, Any] | None:
    """Location-provenance notice: is the SHOWN menu from the diner's location, or
    did discovery fall back to another branch's menu? Structural only (Places
    address city vs. menu-source URL slug), never menu prose. We keep the menu but
    flag the mismatch -- 'flag, don't hide'. None when there's nothing to say."""
    from safeplate.extraction2 import locality

    home = locality.city_from_address(address)
    if not home:
        return None
    used = [getattr(it, "menu_source_url", "") for it in menu_items]
    used = [u for u in used if u]
    if not used:
        return None
    home_label = home.replace("-", " ").title()

    # (a) A used source explicitly names a DIFFERENT city -> labeled mismatch.
    for url in used:
        if locality.menu_city_mismatch(url, address, restaurant_name):
            shown = locality.source_city_slug(url, restaurant_name) or ""
            return {
                "verified": False,
                "shownCity": shown.replace("-", " ").title(),
                "homeCity": home_label,
                "confidence": "labeled",
            }

    # (b) Coverage-diff: a diner-city menu was DISCOVERED but not the source we
    #     used -> inferred mismatch (no clean label to show).
    home_in_used = any(locality.url_has_city(u, home) for u in used)
    home_in_coverage = any(
        locality.url_has_city(getattr(c, "url", ""), home) for c in coverage
    )
    if home_in_coverage and not home_in_used:
        return {
            "verified": False,
            "shownCity": "",
            "homeCity": home_label,
            "confidence": "inferred",
        }
    return None


def _diet_summary_payload(
    diets: Any, menu_items: list[Any], *, cuisines: list[str] | None = None,
    llm_judgments: dict[str, Any] | None = None,
    diet_signals: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Map each requested diet to its DietAssessment as a UI-shaped dict. A distinct
    concept from allergen risk (ingredient membership, no severity/cross-contact) --
    see ``safeplate.diet_score`` for the compatibility rules. ``llm_judgments`` is the
    ``{diet: {name: DietJudgment}}`` map from the diet LLM judge; ``diet_signals`` are
    grounded accommodation statements ("can be made vegan on request")."""
    from safeplate.allergens import DIETS
    from safeplate.diet_score import assess_diets

    return [
        {
            "diet": a.diet,
            "display": DIETS[a.diet].display if a.diet in DIETS else a.diet,
            "verdict": a.verdict,
            "support": a.support,
            "basis": a.basis,
            "rationale": a.rationale,
            "offendingItems": a.offending_items,
            "compatibleItems": a.compatible_items,
            "notes": a.notes,
        }
        for a in assess_diets(
            diets, menu_items=menu_items, cuisines=cuisines,
            llm_judgments=llm_judgments, accommodation_signals=diet_signals,
        )
    ]


def _structured_menu_response(
    *,
    restaurant_name: str,
    website_url: str,
    address: str = "",
    assessment: Any,
    menu_items: list[Any],
    allergy_signals: list[Any],
    coverage: list[Any],
    errors: list[dict[str, str]],
    scoring_engine: str = "rules",
    personalized: bool = False,
    diets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the structured drawer payload (menuItems + allergySignals + assessment + the
    legacy-shaped summary the UI drawer reads). Shared so the SEARCH can embed this exact
    payload per menu-backed card -- letting the drawer open instantly with no
    /api/menu round-trip -- and so /api/menu can return it on demand for cards that
    weren't pre-extracted."""
    from safeplate.allergens import spec_for

    item_payloads = _menu_item_payloads(menu_items)
    riskiest_items: list[dict[str, Any]] = []
    per_allergen_payload: list[dict[str, Any]] = []
    for per_allergen in assessment.per_allergen:
        riskiest_items.extend(per_allergen.riskiest_items)
        spec = spec_for(per_allergen.allergen)
        display = spec.display if spec else per_allergen.allergen.replace("_", " ").title()
        per_allergen_payload.append(
            {
                "allergen": per_allergen.allergen,
                "display": display,
                "tier": per_allergen.tier,
                "risk": round(per_allergen.risk, 2),
                "rationale": per_allergen.rationale,
            }
        )
    coverage_status = "menu_backed" if menu_items else "cuisine_estimate"
    region_notice = _region_notice_for(
        coverage, menu_items, address=address, website_url=website_url
    )
    location_notice = _location_notice_for(
        coverage, menu_items, address=address, restaurant_name=restaurant_name
    )
    summary: dict[str, Any] = {
        "engine": "structured",
        "scoringEngine": scoring_engine,
        "personalized": personalized,
        "regionNotice": region_notice,
        "locationNotice": location_notice,
        "itemCount": len(item_payloads),
        "allergenItemCount": sum(
            1 for item in menu_items if getattr(item, "allergen_terms", None)
        ),
        "allergySignalCount": len(allergy_signals),
        "tier": assessment.tier,
        "overallRisk": round(assessment.overall_risk, 3),
        "overallConfidence": round(assessment.overall_confidence, 2),
        "evidenceBasis": assessment.evidence_basis,
        "menuSourceErrors": errors,
        "coverageStatus": coverage_status,
        # per-allergen breakdown (Task 9): reflects whatever allergens were scored,
        # so it's always present -- unlike `diets` below it doesn't depend on an
        # optional profile selection.
        "perAllergen": per_allergen_payload,
        # legacy-compatible shapes the UI drawer reads:
        "menuBackedRisk": {
            "risk": round(assessment.overall_risk, 3),
            "confidence": round(assessment.overall_confidence, 2),
            "rationale": assessment.rationale,
            "isMenuBacked": bool(menu_items),
            "tier": assessment.tier,
            "riskiestItems": riskiest_items,
            "scoringEngine": scoring_engine,
            # Citable evidence (id -> source url/quote) so the UI can deep-link each
            # [E#] chip in the rationale to exactly where the claim came from.
            "evidence": getattr(assessment, "evidence", []) or [],
        },
        "restaurantSignals": {
            "has_allergy_disclaimer": assessment.handling.allergy_aware,
            "has_cross_contact_warning": assessment.handling.cross_contact_warning,
            "mentions_staff_allergy_instruction": assessment.handling.ask_staff,
            "has_nut_free_claim": assessment.handling.nut_free_claim,
        },
    }
    if diets:
        # Only attached when the profile actually selected diets, so existing
        # responses (no diets picked) stay byte-unchanged.
        summary["diets"] = diets
    return {
        "engine": "structured",
        "restaurantName": restaurant_name,
        "websiteUrl": website_url,
        "menuItems": item_payloads,
        "allergySignals": [asdict(sig) for sig in allergy_signals],
        "assessment": asdict(assessment),
        "coverage": [asdict(report) for report in coverage],
        "coverageStatus": coverage_status,
        "regionNotice": region_notice,
        "locationNotice": location_notice,
        "summary": summary,
        "files": {},
    }


def _run_structured_menu_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    """Engine 'structured': clean-architecture extraction (extraction2) fused with the
    Layer #5 per-user scorer. Returns the same menuItems shape as legacy (same
    MenuItemRecord), plus an `assessment` (tiered per-user risk) and
    `allergySignals` (restaurant-level allergy-handling evidence)."""
    restaurant_name = str(payload.get("name") or "").strip()
    website_url = str(payload.get("websiteUrl") or "").strip()
    address = str(payload.get("address") or "").strip()
    categories = _string_list(payload.get("categories"))
    if not restaurant_name:
        raise ValueError("Restaurant name is required.")

    profile = _user_profile_from_payload(payload)
    scoring_engine = _scoring_engine_from_payload(payload)
    no_cache = bool(payload.get("noCache"))  # UI "raw" toggle: bypass every cache, fetch live
    experience_history = payload.get("experienceHistory") if _is_ai_engine(scoring_engine) else None
    latitude = _optional_float(payload.get("latitude"))
    longitude = _optional_float(payload.get("longitude"))
    # Derive cuisines/region once and reuse for both the extraction-stage score and
    # the community re-score below, instead of each call re-deriving them.
    from safeplate.allergen_prior import cuisines_for, region_from_address

    cuisines = cuisines_for(categories, restaurant_name)
    region = region_from_address(address, latitude=latitude, longitude=longitude)
    api_key = get_gemini_api_key()
    assessment, menu_items, allergy_signals, coverage, errors, diet_signals = _extract_and_assess_structured(
        name=restaurant_name,
        website_url=website_url,
        address=address,
        categories=categories,
        latitude=latitude,
        longitude=longitude,
        profile=profile,
        user_agent=get_user_agent(),
        api_key=api_key,
        cuisines=cuisines,
        region=region,
        scoring_engine=scoring_engine,
        no_cache=no_cache,
        experience_history=experience_history,
    )

    # Community layer (DRAWER ONLY -- one restaurant, cacheable; the list stays cheap):
    # web-sourced allergy-handling signals fold into the score (safety-asymmetric), and
    # when NO menu was found, diner-mentioned dishes seed the dish-name prior so even a
    # menu-less place beats a bare cuisine guess. Never grounded allergen evidence.
    community_quotes: list[str] = []
    cres = None
    try:
        from safeplate.allergen_score import assess_restaurant_record
        from safeplate.community_signals import fetch_community_signals

        cres = fetch_community_signals(
            restaurant_name=restaurant_name, address=address,
            user_agent=get_user_agent(), brave_api_key=get_brave_search_api_key(),
            gemini_api_key=get_gemini_api_key(), gemini_model=get_gemini_model(),
            want_dishes=not menu_items,
        )
        community_quotes = cres.quotes
        if cres.signals or (not menu_items and cres.dishes):
            if not menu_items and cres.dishes:
                menu_items = cres.dishes  # no-menu dish-context -> feeds the dish prior
            record = SimpleNamespace(
                categories=categories, address=address,
                latitude=latitude, longitude=longitude,
                website_url=website_url,
            )
            sig = _build_restaurant_signals(
                allergy_signals, name=restaurant_name, address=address,
                website_url=website_url,
            )
            if _is_ai_engine(scoring_engine):
                from safeplate.allergen_score_llm import assess_restaurant_record_with_llm
                assessment = assess_restaurant_record_with_llm(
                    record, profile, menu_items=menu_items, signals=sig,
                    community=cres.signals or None,
                    experience_history=experience_history,
                    api_key=get_gemini_api_key(), model=get_gemini_model(),
                )
            else:
                assessment = assess_restaurant_record(
                    record, profile, menu_items=menu_items, signals=sig,
                    community=cres.signals or None, cuisines=cuisines, region=region,
                )
    except Exception as exc:  # community is best-effort; never break the drawer
        errors.append({"source": "community_signals", "error": str(exc)})

    # Diet compatibility (vegan/vegetarian/etc.) is a distinct concept from allergen
    # risk -- attach only when the diner actually selected diets, on the FINAL
    # menu_items (the community layer above may have seeded dish-context items when
    # no menu was found), so unchanged (no-diet) responses stay byte-identical. The
    # diet LLM judge is a SEPARATE call from the allergen judge, fired only when the
    # AI engine + a key + selected diets + menu_items are ALL present (never on the
    # no-diet default path, keeping it byte-identical to today).
    diet_judgments: dict[str, Any] = {}
    if _is_ai_engine(scoring_engine) and api_key and profile.diets and menu_items:
        from safeplate.diet_llm import judge_diet_compatibility

        try:
            diet_judgments = judge_diet_compatibility(
                menu_items, list(profile.diets), api_key=api_key, model=get_gemini_model(),
            )
        except Exception:
            diet_judgments = {}
    diet_signals = list(diet_signals)
    if cres is not None:
        diet_signals.extend(cres.diet_signals)
    diets_payload = (
        _diet_summary_payload(
            profile.diets, menu_items, cuisines=cuisines,
            llm_judgments=diet_judgments, diet_signals=diet_signals,
        )
        if profile.diets
        else None
    )

    response = _structured_menu_response(
        restaurant_name=restaurant_name,
        website_url=website_url,
        address=address,
        assessment=assessment,
        menu_items=menu_items,
        allergy_signals=allergy_signals,
        coverage=coverage,
        errors=errors,
        scoring_engine=scoring_engine,
        personalized=bool(experience_history) and _is_ai_engine(scoring_engine),
        diets=diets_payload,
    )
    response["communityQuotes"] = community_quotes
    return response


def _run_demo_menu_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    restaurant_name = str(payload.get("name") or "").strip()
    restaurant_source_id = str(payload.get("sourceId") or "").strip()
    if not restaurant_source_id:
        restaurant_source_id = _demo_source_id_for_name(restaurant_name)
    if not restaurant_source_id:
        raise ValueError("Demo restaurant sourceId is required.")

    try:
        fixture = load_demo_menu(restaurant_source_id)
    except DemoFixtureError as exc:
        raise ValueError(str(exc)) from exc

    if not restaurant_name:
        for source in fixture.menu_sources:
            if source.restaurant_name:
                restaurant_name = source.restaurant_name
                break
    website_url = str(payload.get("websiteUrl") or "")
    if not website_url and fixture.menu_sources:
        website_url = fixture.menu_sources[0].website_url

    displayed_menu_items = _menu_item_payloads(fixture.menu_items)
    for item in displayed_menu_items:
        item.update(
            {
                "llm_validation_status": "demo_fixture",
                "llm_validated": False,
                "llm_is_menu_item": None,
                "llm_confidence": None,
                "llm_rejection_reason": "",
                "llm_evidence_quote": "",
            }
        )

    summary = _menu_summary(
        fixture.menu_sources,
        fixture.menu_text,
        displayed_menu_items,
        parsed_item_count=len(fixture.menu_items),
        rejected_items=[],
        validation_summary=_empty_validation_summary(),
        menu_source_errors=[],
        website_url=website_url,
        website_recovery=None,
        brave_fallback_used=False,
        restaurant_payload=payload,
        demo_scenario=fixture.scenario,
    )
    return {
        "restaurantName": restaurant_name,
        "websiteUrl": website_url,
        "websiteRecovery": None,
        "menuSources": [_safe_payload(row) for row in fixture.menu_sources],
        "menuText": [_safe_payload(row) for row in fixture.menu_text],
        "menuItems": displayed_menu_items,
        "rejectedMenuItems": [],
        "summary": summary,
        "files": {},
        "demoMode": True,
    }


def _demo_source_id_for_name(name: str) -> str:
    if not name:
        return ""
    try:
        fixture = load_demo_search()
    except DemoFixtureError:
        return ""
    normalized_name = name.strip().lower()
    for row in fixture.restaurants:
        if str(row.name or "").strip().lower() == normalized_name:
            return row.source_id
    return ""


def _write_assessment_into_card(
    payload: dict[str, Any], assessment: Any, *,
    prior: Any, cuisines: list[str], region: str, name: str, website_url: str,
    menu_items: list[Any], allergy_signals: list[Any], coverage: list[Any],
    errors: list[dict[str, str]], scoring_engine: str = "rules", address: str = "",
    diets: list[dict[str, Any]] | None = None,
) -> None:
    """Write an assessment (and its menu-backed detail) into a result card. Used for
    both the deterministic build and the ai_assisted batched re-score, so the two stay in
    lockstep -- the only thing that changes between them is ``assessment`` (and which
    scoring engine produced it, surfaced so the drawer can label the explanation).
    ``diets`` (precomputed by the caller, since it only depends on the profile +
    menu_items which don't change between the deterministic and re-scored write) is
    stamped onto the row AND threaded into the embedded ``menuDetail`` so the list
    card and the drawer agree, same as the risk score."""
    payload["allergenPrior"] = {
        "allergen": assessment.per_allergen[0].allergen if assessment.per_allergen else "nuts",
        "risk": round(assessment.overall_risk, 3),
        "confidence": round(assessment.overall_confidence, 2),
        "basis": assessment.evidence_basis,
        "rationale": assessment.rationale,
        "tier": assessment.tier,
        "labelingTrust": round(prior.labeling_trust, 2),
        "cuisines": cuisines,
        "region": region,
        "scoringEngine": scoring_engine,
    }
    payload["coverageStatus"] = "menu_backed" if menu_items else "cuisine_estimate"
    if diets:
        payload["diets"] = diets
    # We just extracted the full menu to score the card -- carry it along so opening
    # the drawer is INSTANT (no /api/menu round-trip). Only for menu-backed cards;
    # cuisine-estimate ones have nothing to embed and fetch fresh on open.
    if menu_items:
        payload["menuDetail"] = _structured_menu_response(
            restaurant_name=name,
            website_url=website_url,
            address=address,
            assessment=assessment,
            menu_items=menu_items,
            allergy_signals=allergy_signals,
            coverage=coverage,
            errors=errors,
            scoring_engine=scoring_engine,
            diets=diets,
        )
    else:
        payload.pop("menuDetail", None)


def _fetch_community_signals(name: str, address: str, *, want_dishes: bool):
    """Web-sourced community allergy signals (Brave + Gemini), cached per restaurant.
    Shared by the drawer and the list so both feed the SAME community evidence into the
    scorer. Best-effort: returns an empty result on any failure."""
    from safeplate.community_signals import CommunityResult, fetch_community_signals

    try:
        return fetch_community_signals(
            restaurant_name=name, address=address, user_agent=get_user_agent(),
            brave_api_key=get_brave_search_api_key(), gemini_api_key=get_gemini_api_key(),
            gemini_model=get_gemini_model(), want_dishes=want_dishes,
        )
    except Exception:
        return CommunityResult()


def _menu_backed_card(row: Any, *, profile: Any, user_agent: str, api_key: str | None,
                      scoring_engine: str = "rules") -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Build a result-card payload whose ``allergenPrior`` IS the menu-backed Layer
    #5 assessment (same extraction + scorer + result cache as the drawer), so the
    list card and the drawer show the IDENTICAL score -- the drawer just adds the
    item-level detail. Falls back to the cuisine prior only if extraction yields
    nothing (no website / nothing found).

    Scoring is always DETERMINISTIC here. For ``scoring_engine == "ai_assisted"`` the card is
    re-scored by ONE batched LLM call over the whole list (see ``_build_search_cards``),
    not per restaurant -- so this also returns a re-scoring context (inputs + the bits
    needed to rewrite the card with the batched assessment), or ``None`` for rules."""
    from safeplate.allergen_prior import (
        cuisines_for,
        region_from_address,
        score_restaurant_prior,
    )
    from safeplate.allergen_score import _domain_of

    payload = asdict(row)
    payload["categories"] = row.categories
    cuisines = cuisines_for(row.categories, str(row.name or "").strip())
    region = region_from_address(
        row.address, latitude=row.latitude, longitude=row.longitude
    )
    prior = score_restaurant_prior(cuisines=cuisines, region=region, allergen="nuts")

    from safeplate.timing import span

    name = str(row.name or "").strip()
    website_url = str(row.website_url or "").strip()
    address = str(row.address or "")
    with span("card_extract_assess"):
        assessment, menu_items, allergy_signals, coverage, errors, diet_signals = _extract_and_assess_structured(
            name=name,
            website_url=website_url,
            address=address,
            categories=row.categories,
            latitude=row.latitude,
            longitude=row.longitude,
            profile=profile,
            user_agent=user_agent,
            api_key=api_key,
            cuisines=cuisines,  # already derived above for the prior; don't recompute
            region=region,
            scoring_engine="rules",  # ai_assisted re-scores the whole list in ONE batched call
        )
    # Diet compatibility: only when the diner selected diets, on the menu_items we
    # just extracted for this row (empty menu_items -> assess_diets reports "unknown"
    # per diet, which is the honest answer, never "good_options"). The diet LLM judge
    # (a SEPARATE call from the allergen judge) only fires when the AI engine + a key
    # + selected diets + menu_items are ALL present -- never on the default (no-diet
    # or rules-engine) path, keeping those responses byte-identical to today.
    diet_judgments: dict[str, Any] = {}
    if _is_ai_engine(scoring_engine) and api_key and profile.diets and menu_items:
        from safeplate.diet_llm import judge_diet_compatibility

        try:
            diet_judgments = judge_diet_compatibility(
                menu_items, list(profile.diets), api_key=api_key, model=get_gemini_model(),
            )
        except Exception:
            diet_judgments = {}
    diets_payload = (
        _diet_summary_payload(
            profile.diets, menu_items, cuisines=cuisines,
            llm_judgments=diet_judgments, diet_signals=diet_signals,
        )
        if profile.diets
        else None
    )
    rebuild = dict(
        prior=prior, cuisines=cuisines, region=region, name=name,
        website_url=website_url, address=address, menu_items=menu_items,
        allergy_signals=allergy_signals, coverage=coverage, errors=errors,
        diets=diets_payload,
    )
    _write_assessment_into_card(payload, assessment, scoring_engine="rules", **rebuild)
    if isinstance(row.raw_payload, dict) and row.raw_payload.get("demo_scenario"):
        payload["demoScenario"] = row.raw_payload["demo_scenario"]

    ctx: dict[str, Any] | None = None
    if _is_ai_engine(scoring_engine):
        # Community allergy signals (same source the drawer uses) so the batched list
        # score reflects them too -- and matches the drawer. want_dishes=False: the
        # list only folds in handling/adverse signals, not no-menu dish inference
        # (that stays drawer-only to avoid faking menu coverage on the card).
        with span("card_community"):
            cres = _fetch_community_signals(name, address, want_dishes=False)
        ctx = {
            "profile": profile,
            "cuisines": cuisines,
            "region": region,
            "menu_items": menu_items,
            "signals": _build_restaurant_signals(
                allergy_signals, name=name, address=address, website_url=website_url
            ),
            "community": cres.signals or None,
            "official_domain": _domain_of(website_url),
            "rebuild": rebuild,
        }
    return payload, ctx


def _menu_summary(
    menu_sources: list[Any],
    menu_text: list[Any],
    menu_items: list[Any],
    *,
    parsed_item_count: int | None = None,
    rejected_items: list[Any] | None = None,
    validation_summary: dict[str, Any] | None = None,
    menu_source_errors: list[dict[str, str]] | None = None,
    website_url: str = "",
    website_recovery: dict[str, Any] | None = None,
    brave_fallback_used: bool = False,
    restaurant_payload: dict[str, Any] | None = None,
    demo_scenario: str = "",
) -> dict[str, Any]:
    method_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    dietary_rows = 0
    allergen_rows = 0
    priced_rows = 0
    for item in menu_items:
        extraction_method = str(_item_value(item, "extraction_method") or "")
        source_type = str(_item_value(item, "source_type") or "")
        method_counts[extraction_method] = method_counts.get(extraction_method, 0) + 1
        source_counts[source_type] = source_counts.get(source_type, 0) + 1
        category = str(_item_value(item, "category") or "Uncategorized")
        category_counts[category] = category_counts.get(category, 0) + 1
        if _item_terms(item, "dietary_terms"):
            dietary_rows += 1
        if _item_terms(item, "allergen_terms"):
            allergen_rows += 1
        if _item_value(item, "price"):
            priced_rows += 1

    validation_summary = validation_summary or _empty_validation_summary()
    rejected_items = rejected_items or []
    coverage_status = _coverage_status(menu_sources, menu_text, menu_items)
    menu_backed_risk = _menu_backed_nut_risk(restaurant_payload or {}, menu_items)
    restaurant_signals = restaurant_signals_from_evidence(menu_text, menu_items)
    return {
        "sourceCount": len(menu_sources),
        "textRecordCount": len(menu_text),
        "itemCount": len(menu_items),
        "parsedItemCount": parsed_item_count
        if parsed_item_count is not None
        else len(menu_items),
        "shownItemCount": len(menu_items),
        "rejectedItemCount": len(rejected_items),
        "pricedRows": priced_rows,
        "dietaryRows": dietary_rows,
        "allergenRows": allergen_rows,
        "geminiValidationEnabled": bool(validation_summary.get("enabled")),
        "geminiModel": validation_summary.get("model", ""),
        "geminiModelUsed": validation_summary.get("modelUsed", ""),
        "geminiFallbackModels": validation_summary.get("fallbackModels", []),
        "geminiValidatedRows": validation_summary.get("validatedRows", 0),
        "geminiAcceptedRows": validation_summary.get("acceptedRows", 0),
        "geminiRejectedRows": validation_summary.get("rejectedRows", 0),
        "geminiMissingRows": validation_summary.get("missingRows", 0),
        "geminiValidationError": validation_summary.get("error", ""),
        "geminiValidationWarnings": validation_summary.get("warnings", []),
        "geminiAttemptErrors": validation_summary.get("attemptErrors", []),
        "menuSourceErrors": menu_source_errors or [],
        "websiteUrl": website_url,
        "websiteRecoveryStatus": (website_recovery or {}).get("status", ""),
        "braveFallbackUsed": brave_fallback_used,
        "coverageStatus": coverage_status,
        "menuBackedRisk": menu_backed_risk,
        "restaurantSignals": restaurant_signals,
        "demoScenario": demo_scenario,
        "methodCounts": dict(
            sorted(method_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "sourceTypeCounts": dict(
            sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "categoryCounts": dict(
            sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "textCharacters": sum(row.char_count for row in menu_text),
        "priceHits": sum(row.price_count for row in menu_text),
    }


def _coverage_status(
    menu_sources: list[Any],
    menu_text: list[Any],
    menu_items: list[Any],
) -> str:
    if menu_items:
        return "menu_backed"
    if menu_sources or menu_text:
        return "cuisine_estimate"
    return "no_menu_found"


def _menu_backed_nut_risk(
    restaurant_payload: dict[str, Any],
    menu_items: list[Any],
) -> dict[str, Any]:
    from safeplate.allergen_prior import cuisines_for
    from safeplate.allergen_prior import region_from_address
    from safeplate.allergen_prior import restaurant_nut_risk

    categories = _string_list(restaurant_payload.get("categories"))
    cuisines = cuisines_for(categories, str(restaurant_payload.get("name") or ""))
    latitude = _optional_float(restaurant_payload.get("latitude"))
    longitude = _optional_float(restaurant_payload.get("longitude"))
    region = region_from_address(
        str(restaurant_payload.get("address") or ""),
        latitude=latitude,
        longitude=longitude,
    )
    item_rows = [
        {
            "item_name": str(_item_value(item, "item_name") or ""),
            "description": str(_item_value(item, "description") or ""),
        }
        for item in menu_items
    ]
    risk = restaurant_nut_risk(
        cuisines=cuisines,
        region=region,
        menu_items=item_rows,
        allergen="nuts",
    )
    return {
        "allergen": "nuts",
        "risk": round(risk.risk, 3),
        "confidence": round(risk.confidence, 2),
        "basis": "menu_items" if item_rows else "cuisine_location_prior",
        "rationale": risk.rationale,
        "labelingTrust": round(risk.labeling_trust, 2),
        "riskiestItems": [
            {"itemName": name, "risk": round(item_risk, 3)}
            for name, item_risk in risk.riskiest_items
        ],
        "isMenuBacked": bool(item_rows),
        "cuisines": cuisines,
        "region": region,
    }


def restaurant_signals_from_evidence(
    menu_text: list[Any],
    menu_items: list[Any],
) -> dict[str, bool]:
    text = _normalized_evidence_text(menu_text, menu_items)
    return {
        "has_allergy_disclaimer": _has_any(
            text,
            [
                "food allergy",
                "food allergies",
                "allergy notice",
                "allergen notice",
                "allergen information",
                "allergen guide",
            ],
        ),
        "has_cross_contact_warning": _has_any(
            text,
            [
                "cross contact",
                "cross contamination",
                "shared fryer",
                "shared fryers",
                "may contain",
                "cannot guarantee",
            ],
        ),
        "mentions_staff_allergy_instruction": _has_any(
            text,
            [
                "tell your server",
                "inform your server",
                "please inform",
                "please alert",
                "notify your server",
                "let us know",
                "speak to a manager",
            ],
        ),
        "has_nut_free_claim": _has_any(
            text,
            [
                "nut free",
                "peanut free",
                "tree nut free",
                "no peanuts",
                "no tree nuts",
            ],
        ),
    }


def _normalized_evidence_text(menu_text: list[Any], menu_items: list[Any]) -> str:
    pieces = []
    for row in menu_text:
        pieces.append(str(_item_value(row, "extracted_text") or ""))
        pieces.extend(_item_terms(row, "dietary_terms"))
        pieces.extend(_item_terms(row, "allergen_terms"))
    for item in menu_items:
        for field in ["item_name", "description", "raw_text", "price"]:
            pieces.append(str(_item_value(item, field) or ""))
        pieces.extend(_item_terms(item, "dietary_terms"))
        pieces.extend(_item_terms(item, "allergen_terms"))
    return " ".join(pieces).lower().replace("-", " ")


def _has_any(text: str, needles: list[str]) -> bool:
    return any(needle.replace("-", " ") in text for needle in needles)


def _item_value(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _item_terms(item: Any, name: str) -> list[str]:
    value = _item_value(item, name)
    if isinstance(value, list):
        return [str(term) for term in value if str(term).strip()]
    if isinstance(value, str):
        return [term.strip() for term in value.split(";") if term.strip()]
    return []
