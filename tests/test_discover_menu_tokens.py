"""The link-classifier heuristic should recognize menu words beyond English "menu".

Real case: Der Pepper'n Gror (Oslo) publishes its menu at /meny-en/ and /meny-norsk/
with anchor text "Meny" -- Norwegian for menu. The token heuristic missed it, so with
no LLM (or exhausted quota) discovery found zero candidates on the location site.
"""

from __future__ import annotations

import pytest

from safeplate.extraction2.discover import _heuristic_select


@pytest.mark.parametrize("url,text", [
    ("https://radhusplassen.derpepperngror.no/meny-en/", "Meny"),   # Norwegian/Danish/Swedish
    ("https://x.se/meny/", "Meny"),
    ("https://x.no/spisekart/", "Spisekart"),                        # Norwegian "menu card"
    ("https://x.fi/ruokalista/", "Ruokalista"),                      # Finnish
    ("https://x.pt/cardapio/", "Cardápio"),                          # Portuguese
])
def test_non_english_menu_words_classified_as_menu(url, text):
    selected = _heuristic_select([(url, text)])
    kinds = {kind for (_link, kind) in selected}
    assert "menu" in kinds, (url, text, selected)


@pytest.mark.parametrize("url,text", [
    ("https://x.com/about/harmony-values", "Our harmony"),  # 'mony' != 'meny'
    ("https://x.com/ceremony", "Ceremony hall"),
    ("https://x.com/company/careers", "Careers"),
])
def test_menu_lookalikes_not_misclassified(url, text):
    selected = _heuristic_select([(url, text)])
    kinds = {kind for (_link, kind) in selected}
    assert "menu" not in kinds, (url, text, selected)
