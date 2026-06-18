from safeplate.extraction2.discover import _city_token, _pdf_mentions


def test_pdf_mentions_compact_match():
    text = "Welcome to Pizza Express. Our menu includes..."
    assert _pdf_mentions(text, "Pizza Express") is True


def test_pdf_mentions_token_majority():
    text = "the wagamama allergen guide lists every dish"
    assert _pdf_mentions(text, "Wagamama") is True


def test_pdf_mentions_rejects_wrong_restaurant():
    text = "Nando's peri-peri chicken menu and nutritional info"
    assert _pdf_mentions(text, "Pizza Express") is False


def test_pdf_mentions_empty():
    assert _pdf_mentions("", "Anything") is False


def test_city_token():
    assert _city_token("5152 Moorpark Avenue, San Jose, CA, 95129") == "San Jose"
    assert _city_token("Cupertino") is None
    assert _city_token(None) is None
