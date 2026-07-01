from safeplate import allergens


def test_registry_has_all_eu14_tokens():
    keys = set(allergens.all_allergen_keys())
    assert keys == {
        "peanut", "tree_nut", "milk", "egg", "soy", "gluten", "wheat",
        "fish", "shellfish", "mollusc", "sesame", "mustard", "celery",
        "sulphites", "lupin",
    }


def test_canonical_reconciles_three_vocabularies():
    # matrix space-form, prior underscore-plural, and bare
    assert allergens.canonical("tree nut") == "tree_nut"
    assert allergens.canonical("tree_nuts") == "tree_nut"
    assert allergens.canonical("peanut") == "peanut"
    assert allergens.canonical("peanuts") == "peanut"
    assert allergens.canonical("milk") == "milk"
    assert allergens.canonical("dairy") == "milk"
    assert allergens.canonical("not-an-allergen") is None


def test_spec_carries_matrix_tokens():
    spec = allergens.spec_for("milk")
    assert spec.display == "Milk"
    assert "milk" in spec.matrix_tokens


def test_diet_exclusion_sets():
    vegan = allergens.DIETS["vegan"]
    assert {"milk", "egg", "fish", "shellfish", "mollusc"} <= vegan.excluded_allergens
    assert "meat" in vegan.excluded_categories
    veg = allergens.DIETS["vegetarian"]
    assert {"fish", "shellfish", "mollusc"} <= veg.excluded_allergens
    assert "milk" not in veg.excluded_allergens  # lacto-veg keeps dairy/egg
