from safeplate.common import _user_profile_from_payload
from safeplate.allergen_score import Severity


def test_legacy_payload_still_nuts():
    p = _user_profile_from_payload({"severity": "anaphylaxis", "nutTypes": []})
    assert len(p.allergens) == 1
    assert p.allergens[0].allergen == "nuts"
    assert p.allergens[0].severity == Severity.ANAPHYLAXIS
    assert p.diets == frozenset()


def test_multi_allergen_payload():
    p = _user_profile_from_payload({"allergens": [
        {"allergen": "milk", "severity": "allergy"},
        {"allergen": "gluten", "severity": "intolerance"},
    ]})
    keys = {a.allergen for a in p.allergens}
    assert keys == {"milk", "gluten"}


def test_gluten_free_diet_expands_to_gluten_allergen():
    p = _user_profile_from_payload({"diets": ["gluten_free", "vegan"]})
    assert any(a.allergen == "gluten" for a in p.allergens)
    assert p.diets == frozenset({"vegan"})  # gluten_free consumed into an allergen


def test_diet_flags_parsed():
    p = _user_profile_from_payload({"allergens": [{"allergen": "milk", "severity": "allergy"}],
                                    "diets": ["vegetarian"]})
    assert p.diets == frozenset({"vegetarian"})
