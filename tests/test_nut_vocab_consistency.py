"""Guard the three nut vocabularies against drift.

There are three nut word-lists in the pipeline: ``menu_text.ALLERGEN_TERMS``
(free-text extraction), ``allergen_prior._MULTILINGUAL_NUT_TERMS`` (priors), and
``allergen_score._PEANUT_TERMS`` / ``_TREE_NUT_TERMS`` (grounded-evidence
recognition). If a nut term is EXTRACTED but not RECOGNIZED, a literal nut mention
becomes grounded evidence that is then silently dropped -- a safety-asymmetric
false negative. These tests make that failure impossible to merge unnoticed.
"""
from __future__ import annotations

from safeplate.menu_text import ALLERGEN_TERMS
from safeplate.allergen_score import _nut_terms_present
from safeplate.allergen_prior import PEANUTS, TREE_NUTS, _MULTILINGUAL_NUT_TERMS

NUTS = {PEANUTS, TREE_NUTS}
_PRIOR_NUT_TERMS = {pat for pat, fams, _risk, _note in _MULTILINGUAL_NUT_TERMS if fams & NUTS}


def test_every_extracted_nut_term_is_recognized():
    """Any multilingual nut INGREDIENT word the extractor can emit must be recognized
    as a nut by the scorer; otherwise extraction surfaces it and scoring throws it away."""
    dropped = [
        term for term in ALLERGEN_TERMS
        if term in _PRIOR_NUT_TERMS and not _nut_terms_present([term], NUTS)
    ]
    assert not dropped, f"extracted nut terms silently dropped by the scorer: {dropped}"


def test_representative_non_english_nut_terms_recognized():
    """Spot-check across scripts so a regression in any one language family is caught."""
    samples = [
        "アーモンド", "杏仁", "아몬드", "बादाम", "миндаль", "لوز",  # almond
        "カシューナッツ", "腰果", "काजू", "كاجو", "кешью",          # cashew
        "fındık", "بندق", "фундук",                              # hazelnut
        "ピスタチオ", "开心果", "فستق", "фисташки",                 # pistachio
        "核桃", "호두", "अखरोट", "ceviz",                         # walnut
        "đậu phộng", "فول سوداني",                              # peanut
    ]
    for term in samples:
        assert _nut_terms_present([term], NUTS), f"{term!r} not recognized as a nut"
