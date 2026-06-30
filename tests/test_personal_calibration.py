from safeplate.allergen_score_llm import _clean_history, _build_bundle
from safeplate.allergen_score import UserProfile, Severity, score_restaurant_for_user, Tier

NUT = UserProfile.for_nuts(Severity.ALLERGY)

def _det(**kw):
    return score_restaurant_for_user(NUT, cuisines=kw.get("cuisines", ["american"]),
                                     region="US", menu_items=kw.get("menu_items"))

def test_clean_history_sanitizes_and_caps():
    raw = ([{"name": "Burger King", "rating": 9, "note": "fine"}]
           + [{"name": f"P{i}", "rating": 99} for i in range(40)]
           + [{"rating": 5}])  # no name -> dropped
    out = _clean_history(raw)
    assert len(out) == 30
    assert out[0] == {"name": "Burger King", "rating": 9, "note": "fine"}
    assert all(1 <= e["rating"] <= 10 for e in out)
    assert all(e["name"] for e in out)

def test_bundle_includes_history_when_present():
    b = _build_bundle(profile=NUT, cuisines=["american"], region="US", det=_det(),
                      signals=None, community=None, menu_items=None, name="BK",
                      experience_history=[{"name": "BK", "rating": 9, "note": ""}])
    assert b["your_history"][0]["name"] == "BK"

def test_bundle_omits_history_when_empty():
    b = _build_bundle(profile=NUT, cuisines=["american"], region="US", det=_det(),
                      signals=None, community=None, menu_items=None, name="BK",
                      experience_history=None)
    assert "your_history" not in b

def test_score_with_llm_passes_history_to_bundle(monkeypatch):
    import safeplate.allergen_score_llm as m
    seen = {}
    def fake_call(bundle, *, api_key, model, system):
        seen["bundle"] = bundle
        return {"tier": "likely_ok", "risk": 0.2, "confidence": 0.6, "rationale": []}
    monkeypatch.setattr(m, "_call_llm_scorer", fake_call)
    m.score_restaurant_with_llm(
        NUT, cuisines=["american"], region="US", api_key="k",
        experience_history=[{"name": "BK", "rating": 9, "note": "fine"}],
    )
    assert seen["bundle"]["your_history"][0]["name"] == "BK"

def test_history_cannot_override_confirmed_presence():
    import safeplate.allergen_score_llm as m
    from safeplate.allergen_score import Tier
    items = [{"item_name": "House Salad", "description": "", "allergen_terms": ["peanut"],
              "extraction_method": "allergen_matrix",
              "matrix_allergen_columns": ("peanut", "tree nut", "milk", "egg", "soy", "gluten")}]
    det = score_restaurant_for_user(NUT, cuisines=["american"], region="US", menu_items=items)
    assert det.tier == Tier.AVOID.value  # confirmed presence -> avoid (grounded)
    bundle = m._build_bundle(profile=NUT, cuisines=["american"], region="US", det=det,
                             signals=None, community=None, menu_items=items, name="X",
                             experience_history=[{"name": "X", "rating": 10, "note": "always fine"}])
    # The LLM tries to drop it to likely_ok; the grounded guardrail must hold the floor.
    llm = {"tier": "likely_ok", "risk": 0.05, "confidence": 0.9, "rationale": []}
    out = m._apply_guardrails(llm, det=det, severity=NUT.allergens[0].severity, bundle=bundle)
    assert out.tier == Tier.AVOID.value
    assert out.overall_risk >= det.overall_risk
