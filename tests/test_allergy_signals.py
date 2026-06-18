from safeplate.extraction2 import allergy_signals as A
from safeplate.extraction2.schema import Payload, PayloadKind


def _payload(text):
    return Payload(url="http://t", source_type="website_link", kind=PayloadKind.TEXT, text=text)


def test_flags_and_quote_grounding(monkeypatch):
    page = (
        "<p>We are proud to be an allergy-friendly kitchen. Please speak to our team "
        "about any allergies. Cross-contamination may occur.</p>"
    )
    fake = {
        "allergy_friendly_claim": True,
        "cross_contact_warning": True,
        "ask_staff": True,
        "allergen_menu_available": False,
        "statements": [
            "We are proud to be an allergy-friendly kitchen.",  # grounded
            "We guarantee 100% nut-free meals.",                # NOT in page -> dropped
        ],
    }
    monkeypatch.setattr(A, "_cached_or_call", lambda text, *, api_key, model: fake)
    sig = A.extract_allergy_signals(_payload(page), api_key="x", model="m")
    assert sig is not None
    assert sig.allergy_friendly_claim and sig.cross_contact_warning and sig.ask_staff
    assert sig.allergen_menu_available is False
    assert any("allergy-friendly kitchen" in s for s in sig.statements)
    assert all("nut-free" not in s for s in sig.statements)  # ungrounded dropped


def test_none_when_no_signal(monkeypatch):
    monkeypatch.setattr(A, "_cached_or_call", lambda text, *, api_key, model: {
        "allergy_friendly_claim": False, "cross_contact_warning": False,
        "ask_staff": False, "allergen_menu_available": False, "statements": [],
    })
    assert A.extract_allergy_signals(_payload("<p>hi</p>"), api_key="x", model="m") is None


def test_none_without_key():
    assert A.extract_allergy_signals(_payload("<p>x</p>"), api_key=None, model="m") is None
