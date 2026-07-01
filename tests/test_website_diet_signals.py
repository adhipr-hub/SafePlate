from safeplate.extraction2 import allergy_signals as A
from safeplate.extraction2.schema import Payload, PayloadKind


def _payload(text):
    return Payload(url="https://x.test/menu", source_type="website_link",
                    kind=PayloadKind.TEXT, text=text)


def test_grounded_vegan_signal_kept(monkeypatch):
    text = "Most of our dishes can be made vegan on request. Ask your server."
    monkeypatch.setattr(A, "_cached_or_call", lambda *a, **k: {
        "allergy_friendly_claim": False, "cross_contact_warning": False,
        "ask_staff": False, "allergen_menu_available": False, "nut_free_claim": False,
        "statements": [],
        "vegan_can_be_made": True, "veg_can_be_made": False,
        "diet_statements": ["Most of our dishes can be made vegan on request"],
    })
    sigs = A.extract_diet_signals(_payload(text), api_key="k", model="m")
    assert any(s.diet == "vegan" and "vegan" in s.quote.lower() for s in sigs)


def test_ungrounded_diet_quote_dropped(monkeypatch):
    text = "We serve lunch and dinner."
    monkeypatch.setattr(A, "_cached_or_call", lambda *a, **k: {
        "allergy_friendly_claim": False, "cross_contact_warning": False,
        "ask_staff": False, "allergen_menu_available": False, "nut_free_claim": False,
        "statements": [], "vegan_can_be_made": True, "veg_can_be_made": False,
        "diet_statements": ["everything can be made vegan"],   # NOT in source
    })
    assert A.extract_diet_signals(_payload(text), api_key="k", model="m") == []
