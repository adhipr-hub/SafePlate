"""Broader i18n coverage for the link-classifier heuristic, from a live sweep of
European restaurant sites: accented menu words (menú/menü), German "Karte",
Spanish "Nutrición", and Greek/Cyrillic menu/nutrition/allergen words.
"""

from __future__ import annotations

import pytest

from safeplate.extraction2.discover import _heuristic_select


def _kinds(url, text):
    return {kind for (_l, kind) in _heuristic_select([(url, text)])}


@pytest.mark.parametrize("url,text", [
    ("https://x.es/carta/Nochevieja2024.pdf", "Ver menú"),   # Spanish accent
    ("https://x.de/", "Menü"),                                # German/Turkish/Hungarian
    ("https://x.it/", "Menù"),                                # Italian
    ("https://x.de/karte/", "Karte"),                         # German "die Karte"
    ("https://x.gr/", "Μενού"),                               # Greek
    ("https://x.ru/", "Меню"),                                # Cyrillic
])
def test_i18n_menu_words(url, text):
    assert "menu" in _kinds(url, text), (url, text)


@pytest.mark.parametrize("url,text", [
    ("https://x.es/nutricion/", "Nutrición"),                 # Spanish
    ("https://x.pt/", "Nutrição"),                            # Portuguese
    ("https://x.gr/", "ΔΙΑΤΡΟΦΙΚΗ ΑΞΙΑ"),                     # Greek "nutritional value"
])
def test_i18n_nutrition_words(url, text):
    assert "nutrition" in _kinds(url, text), (url, text)


@pytest.mark.parametrize("url,text", [
    ("https://x.gr/", "Αλλεργιογόνα"),                        # Greek allergens
    ("https://x.ru/", "Аллергены"),                           # Cyrillic allergens
])
def test_i18n_allergen_words(url, text):
    assert "allergen" in _kinds(url, text), (url, text)


@pytest.mark.parametrize("url,text", [
    ("https://x.no/meny-en/", "Meny"),        # earlier fix still works
    ("https://x.jp/", "メニュー"),              # CJK still works (no regression)
    ("https://x.fr/", "Carte"),
])
def test_existing_words_still_match(url, text):
    assert "menu" in _kinds(url, text), (url, text)


@pytest.mark.parametrize("url,text", [
    ("https://x.com/ceremony", "Ceremony hall"),   # 'mony' != 'meny'
    ("https://x.com/harmony", "Our harmony"),
    ("https://x.de/produkte", "Produkte"),         # generic 'products' NOT added
    ("https://x.com/about", "About us"),
])
def test_lookalikes_and_generics_not_menu(url, text):
    assert "menu" not in _kinds(url, text), (url, text)
