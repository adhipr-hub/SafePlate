from safeplate import allergen_prior as ap


def test_cuisine_baseline_reads_generic_table():
    prior = ap.allergen_cuisine_baseline("gluten", ["italian"], "US")
    assert prior.risk >= 0.7  # italian gluten baseline is high
    assert prior.allergen == "gluten"


def test_unknown_allergen_falls_back_to_default_baseline():
    prior = ap.allergen_cuisine_baseline("celery", ["thai"], "US")
    assert 0.0 < prior.risk < 0.3  # no table yet -> low default, never zero/"safe"


def test_restaurant_allergen_risk_flags_known_dish():
    risk = ap.restaurant_allergen_risk(
        allergen="milk",
        cuisines=["italian"],
        region="US",
        menu_items=[{"name": "Fettuccine Alfredo"}, {"name": "Garden Salad"}],
    )
    assert risk.risk >= 0.8
    names = [d["name"] for d in risk.item_details if d["risk"] >= 0.8]
    assert any("alfredo" in n.lower() for n in names)


def test_restaurant_allergen_risk_floor_never_zero():
    risk = ap.restaurant_allergen_risk(
        allergen="mustard", cuisines=["thai"], region="US", menu_items=[{"name": "Plain Rice"}]
    )
    assert risk.risk > 0.0  # absence != safe
