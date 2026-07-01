"""Global-sweep follow-up: menu/nutrition words the token heuristic missed when run
against real restaurant sites across 47 countries. The motivating empirical gap was
Romanian "meniu" (two Bucharest sites where the heuristic found ZERO -- the same
whole-menu-missed failure mode as Norwegian "meny"). Rounded out with distinctive,
collision-free "menu" words for the major scripts still uncovered so the no-API-key
fallback is genuinely international.
"""

from __future__ import annotations

import pytest

from safeplate.extraction2.discover import _heuristic_select


def _kinds(url, text):
    return {kind for (_l, kind) in _heuristic_select([(url, text)])}


@pytest.mark.parametrize("url,text", [
    ("https://example.ro/meniu-mancare", "Meniu"),                 # Romanian (empirical)
    ("https://example.ro/media/Meniu-L-Oroscopo.pdf", "Meniu"),    # Romanian PDF (empirical)
    ("https://example.nl/menukaart", "Menukaart"),                 # Dutch
    ("https://example.hu/etlap", "Étlap"),                         # Hungarian (accent-folded)
    ("https://example.cz/jidelni-listek", "Jídelní lístek"),       # Czech
    ("https://example.co.il/", "תפריט"),                            # Hebrew (tafrit)
    ("https://example.ae/", "منيو"),                                # Arabic (menu loanword)
    ("https://example.co.th/", "เมนู"),                             # Thai
    ("https://example.in/", "मेन्यू"),                               # Hindi
])
def test_global_menu_words_classified_as_menu(url, text):
    assert "menu" in _kinds(url, text), (url, text)


@pytest.mark.parametrize("url,text", [
    ("https://example.dk/naeringsberegner", "Næringsberegner"),    # Danish nutrition calc
])
def test_nordic_nutrition_word_classified_as_nutrition(url, text):
    assert "nutrition" in _kinds(url, text), (url, text)


@pytest.mark.parametrize("url,text", [
    # New tokens must not create English false-friends via substring matching.
    ("https://example.com/complementary-services", "Complementary services"),
    ("https://example.com/engineering", "Engineering team"),      # not 'naering'
    ("https://example.com/aluminium", "Aluminium supplier"),      # not 'meniu'
    ("https://example.com/harmony", "Our harmony"),
    ("https://example.de/produkte", "Produkte"),
])
def test_new_tokens_do_not_create_false_menu_matches(url, text):
    assert _kinds(url, text) == set(), (url, text)
