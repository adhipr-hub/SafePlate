from types import SimpleNamespace

from safeplate.diet_score import DietSignal, assess_diet


def _item(name, dietary=(), allergens=()):
    return SimpleNamespace(item_name=name, dietary_terms=list(dietary),
                           allergen_terms=list(allergens))


def test_signal_releases_vegan_estimated_cap():
    items = [_item("Garden Salad"), _item("Steamed Rice"), _item("Fruit Bowl")]
    sig = [DietSignal("vegan", "many dishes can be made vegan", "https://r/1", "community")]
    a = assess_diet("vegan", menu_items=items, accommodation_signals=sig)
    assert a.verdict == "good_options"      # cap released by the signal
    assert a.notes and a.notes[0]["source"] == "community"


def test_signal_never_overrides_not_compatible():
    items = [_item("Chicken Wings"), _item("Beef Tacos")]
    sig = [DietSignal("vegan", "can be made vegan", "https://r/1", "website")]
    a = assess_diet("vegan", menu_items=items, accommodation_signals=sig)
    assert a.verdict == "not_compatible"    # signal cannot override real conflicts


def test_signal_for_other_diet_ignored():
    items = [_item("Garden Salad")]
    sig = [DietSignal("vegetarian", "veg options", "https://r/1", "website")]
    a = assess_diet("vegan", menu_items=items, accommodation_signals=sig)
    assert not a.notes                       # vegetarian signal doesn't attach to vegan
