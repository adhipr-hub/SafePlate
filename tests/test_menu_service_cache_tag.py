"""The drawer response carries cache provenance only when there is something to say."""
from types import SimpleNamespace

from safeplate.allergen_score import Severity, UserProfile, assess_restaurant_record


def _minimal_response(cache_info):
    from safeplate.menu_service import _structured_menu_response

    record = SimpleNamespace(
        categories=["primary_type:thai_restaurant"],
        address="123 Main St, Bangkok, Thailand",
        latitude=13.7,
        longitude=100.5,
    )
    profile = UserProfile.for_nuts(Severity.ALLERGY)
    assessment = assess_restaurant_record(record, profile)
    return _structured_menu_response(
        restaurant_name="Tag Test", website_url="https://t.example", address="",
        assessment=assessment, menu_items=[], allergy_signals=[], coverage=[],
        errors=[], scoring_engine="rules", personalized=False, diets=None,
        cache_info=cache_info,
    )


def test_cache_key_present_when_origin_set():
    resp = _minimal_response({"origin": "postgres", "savedTo": None})
    assert resp["cache"] == {"origin": "postgres", "savedTo": None}


def test_cache_key_absent_when_none():
    assert "cache" not in _minimal_response(None)
