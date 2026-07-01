from types import SimpleNamespace

import safeplate.diet_llm as diet_llm


def _item(name):
    return SimpleNamespace(item_name=name, description="", dietary_terms=[], allergen_terms=[])


def test_no_api_key_returns_empty():
    assert diet_llm.judge_diet_compatibility([_item("Salad")], ["vegan"],
                                             api_key=None, model="m") == {}


def test_grounded_judgments_kept_ungrounded_dropped(monkeypatch):
    items = [_item("Mushroom Risotto"), _item("Garden Salad")]

    def fake_call(request, *, api_key, model):
        return {"judgments": [
            {"diet": "vegan", "item_name": "Mushroom Risotto", "verdict": "no",
             "reason": "risotto is finished with butter and parmesan", "confidence": 0.8},
            {"diet": "vegan", "item_name": "Garden Salad", "verdict": "yes",
             "reason": "plain vegetables", "confidence": 0.7},
            {"diet": "vegan", "item_name": "Phantom Dish", "verdict": "yes",  # ungrounded
             "reason": "n/a", "confidence": 0.9},
        ]}

    monkeypatch.setattr(diet_llm, "_call_with_retry", fake_call)
    monkeypatch.setattr(diet_llm, "_load_cache", lambda *a, **k: None)
    monkeypatch.setattr(diet_llm, "_save_cache", lambda *a, **k: None)

    out = diet_llm.judge_diet_compatibility(items, ["vegan"], api_key="k", model="m")
    assert out["vegan"]["mushroom risotto"].verdict == "no"
    assert out["vegan"]["garden salad"].verdict == "yes"
    assert "phantom dish" not in out["vegan"]      # ungrounded dropped


def test_llm_failure_returns_empty(monkeypatch):
    from safeplate.gemini_menu import GeminiMenuError

    def boom(*a, **k):
        raise GeminiMenuError("down")

    monkeypatch.setattr(diet_llm, "_call_with_retry", boom)
    monkeypatch.setattr(diet_llm, "_load_cache", lambda *a, **k: None)
    out = diet_llm.judge_diet_compatibility([_item("Salad")], ["vegan"], api_key="k", model="m")
    assert out == {}
