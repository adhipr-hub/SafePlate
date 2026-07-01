# tests/test_diet_score_llm_fusion.py
from types import SimpleNamespace

from safeplate.diet_llm import DietJudgment
from safeplate.diet_score import assess_diet


def _item(name, dietary=(), allergens=()):
    return SimpleNamespace(item_name=name, dietary_terms=list(dietary),
                           allergen_terms=list(allergens))


def test_llm_no_overrides_floor_assume():
    # Floor would ASSUME "Mushroom Risotto" vegan; the LLM says no (hidden butter).
    items = [_item("Mushroom Risotto"), _item("Green Salad")]
    judg = {"mushroom risotto": DietJudgment("no", "butter + parmesan", 0.8)}
    a = assess_diet("vegan", menu_items=items, llm_judgments=judg)
    assert "Mushroom Risotto" in a.offending_items


def test_ai_assessed_vegan_can_reach_good_options():
    items = [_item("Buddha Bowl"), _item("Green Curry"), _item("Chili Oil Noodles")]
    judg = {n.lower(): DietJudgment("yes", "no animal products", 0.8)
            for n in ("Buddha Bowl", "Green Curry", "Chili Oil Noodles")}
    a = assess_diet("vegan", menu_items=items, llm_judgments=judg)
    assert a.verdict == "good_options"      # NOT capped -- LLM checked hidden dairy
    assert a.basis == "ai_assessed"


def test_llm_unknown_falls_back_to_floor():
    items = [_item("Garden Salad")]
    judg = {"garden salad": DietJudgment("unknown", "cannot tell", 0.2)}
    a = assess_diet("vegan", menu_items=items, llm_judgments=judg)
    # Falls back to floor -> assumed -> estimated -> vegan cap -> limited
    assert a.basis == "estimated"
    assert a.verdict == "limited"


def test_mixed_vegan_capped_when_estimates_carry_it():
    # 1 AI-yes + 4 name-assumed: 'mixed' basis, but confident evidence (1/5) can't
    # clear the bar on its own -> vegan cap holds at 'limited', not good_options.
    items = [_item("Tofu Bowl"), _item("Garden Salad"), _item("Steamed Rice"),
             _item("Fruit Bowl"), _item("Veg Noodles")]
    judg = {"tofu bowl": DietJudgment("yes", "no animal products", 0.8)}
    a = assess_diet("vegan", menu_items=items, llm_judgments=judg)
    assert a.verdict == "limited"


def test_mixed_vegan_good_options_when_ai_carries_it():
    # 3 AI-yes + 2 name-assumed (total 5): confident 3/5 >= 0.4 -> good_options stays.
    items = [_item("A"), _item("B"), _item("C"), _item("D"), _item("E")]
    judg = {n.lower(): DietJudgment("yes", "ok", 0.8) for n in ("A", "B", "C")}
    a = assess_diet("vegan", menu_items=items, llm_judgments=judg)
    assert a.verdict == "good_options"
