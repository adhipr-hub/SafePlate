from safeplate.allergen_score_llm import _clean_history, _build_bundle
from safeplate.allergen_score import UserProfile, Severity, score_restaurant_for_user

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
