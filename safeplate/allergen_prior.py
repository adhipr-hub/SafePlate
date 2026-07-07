"""Cuisine x dish x location allergen *prior* (nut-focused, extensible).

This layer answers the question the menu-evidence pipeline cannot: "how likely
is this dish / cuisine / place to involve nuts, *even when the menu never says
so*?" It is deterministic and free — a curated knowledge base plus simple,
transparent combination rules — so it works for every nearby restaurant,
including the many that have no usable menu online.

Design contract:
- Absence of an allergen mention is NOT evidence of absence. The prior exists
  precisely to catch hidden nuts (pad thai -> peanuts, pesto -> pine nuts,
  korma -> cashew) that surface-text matching misses.
- Every score carries a ``basis`` and ``rationale`` so the eventual ranking can
  explain itself and never present a bare "safe" verdict.
- Risk values are documented heuristic seeds, meant to be calibrated against
  real outcomes, not treated as ground truth.

The menu-evidence stage (Gemini / text extraction) should *update* these priors,
not replace them: an explicit "contains walnuts" pushes risk up; a verified
"nut-free kitchen" pushes it down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

from safeplate.textutil import norm_ws

# Allergen keys. "nuts" is the convenience union of the two nut families, which
# is what most users mean and what the project's examples target.
PEANUTS = "peanuts"
TREE_NUTS = "tree_nuts"
NUTS = "nuts"
_NUT_FAMILY = {PEANUTS, TREE_NUTS}

# --------------------------------------------------------------------------- #
# Per-NUT taxonomy (user-selectable specific nuts). The scorer can be told the
# EXACT nuts a user reacts to; it then flags only dishes/evidence that name one of
# THOSE nuts (strict per-nut), while family-level evidence we cannot disaggregate
# (a chart's single "tree nut" column, a generic "nuts" mention) still counts for
# any tree-nut selection -- we can't prove the user's nut is absent (safety-first).
#
# Default (no selection / all nuts selected) is byte-identical to the family-level
# behavior: the per-nut filters are bypassed entirely. So the calibrated default
# scores -- and the whole offline+live quality gate -- are unchanged.
# --------------------------------------------------------------------------- #
ALMOND = "almond"
CASHEW = "cashew"
WALNUT = "walnut"
PECAN = "pecan"
PISTACHIO = "pistachio"
HAZELNUT = "hazelnut"
MACADAMIA = "macadamia"
BRAZIL_NUT = "brazil_nut"
PINE_NUT = "pine_nut"
CHESTNUT = "chestnut"
TREE_NUT_TYPES = (
    ALMOND, CASHEW, WALNUT, PECAN, PISTACHIO,
    HAZELNUT, MACADAMIA, BRAZIL_NUT, PINE_NUT, CHESTNUT,
)
# The full selectable set: ten tree nuts + peanut (a legume, kept separate).
NUT_TYPES = TREE_NUT_TYPES + (PEANUTS,)

# Specific-nut term variants (lowercased, substring-matched). Mirrors the words the
# extraction vocabulary and dish-knowledge already recognize, partitioned by which
# nut they name. Used ONLY to refine an already-detected tree-nut hit into a
# specific nut; the family-level recognition stays in allergen_score/menu_text.
NUT_TYPE_TERMS: dict[str, frozenset[str]] = {
    ALMOND: frozenset({
        "almond", "amandine", "amande", "almendra", "mandel", "mandorla", "amêndoa",
        "badem", "hạnh nhân", "アーモンド", "杏仁", "아몬드", "बादाम", "миндаль", "لوز",
        "marzipan", "frangipane", "financier", "amaretto", "amaretti", "bakewell",
        "marcona", "turron", "turrón", "torrone", "macaron",
    }),
    CASHEW: frozenset({
        "cashew", "anacardo", "anacardi", "cajou", "cashewkern", "カシューナッツ",
        "腰果", "hạt điều", "काजू", "كاجو", "кешью",
    }),
    WALNUT: frozenset({
        "walnut", "walnuss", "muhammara", "waldorf", "くるみ", "クルミ", "核桃",
        "호두", "अखरोट", "ceviz",
    }),
    PECAN: frozenset({"pecan"}),
    PISTACHIO: frozenset({
        "pistachio", "pistacho", "pistache", "pistazie", "pistacchio", "ピスタチオ",
        "开心果", "فستق", "фисташки",
    }),
    HAZELNUT: frozenset({
        "hazelnut", "filbert", "nutella", "gianduja", "frangelico", "avellana",
        "noisette", "haselnuss", "nocciola", "avelã", "fındık", "ヘーゼルナッツ",
        "بندق", "فندق", "фундук",
    }),
    MACADAMIA: frozenset({"macadamia", "macademia"}),
    BRAZIL_NUT: frozenset({"brazil nut", "brazilnut"}),
    PINE_NUT: frozenset({
        "pine nut", "pinenut", "pignoli", "pinoli", "piñón", "松子", "잣",
    }),
    CHESTNUT: frozenset({"chestnut"}),
    PEANUTS: frozenset({
        "peanut", "groundnut", "cacahuete", "cacahuate", "cacahuète", "arachide",
        "arachidi", "erdnuss", "amendoim", "đậu phộng", "落花生", "ピーナッツ", "花生",
        "땅콩", "ถั่วลิสง", "मूंगफली", "арахис", "فول سوداني",
    }),
}


def normalize_nut_types(values: object) -> frozenset[str] | None:
    """Map a user's selected-nut list to canonical keys, or None for 'all nuts'.

    None (nothing selected, or every nut selected) means 'use the family-level
    default' -- the calibrated, gate-covered behavior. A strict subset activates
    per-nut scoring. Unknown tokens are ignored; ``tree_nuts``/``nuts`` expand."""
    if not values or not isinstance(values, (list, tuple, set, frozenset)):
        return None
    aliases = {
        "tree nut": TREE_NUTS, "treenut": TREE_NUTS, "tree-nut": TREE_NUTS,
        "brazil": BRAZIL_NUT, "brazil nut": BRAZIL_NUT, "brazilnut": BRAZIL_NUT,
        "pine": PINE_NUT, "pine nut": PINE_NUT, "pinenut": PINE_NUT,
        "peanut": PEANUTS, "peanuts": PEANUTS,
    }
    selected: set[str] = set()
    for raw in values:
        key = str(raw or "").strip().lower().replace("_", " ")
        key = {"tree nuts": TREE_NUTS}.get(key, key)
        key = aliases.get(key, key.replace(" ", "_"))
        if key == TREE_NUTS:
            selected.update(TREE_NUT_TYPES)
        elif key == NUTS:
            selected.update(NUT_TYPES)
        elif key in NUT_TYPES:
            selected.add(key)
    if not selected or selected >= set(NUT_TYPES):
        return None  # nothing recognized, or everything -> family-level default
    return frozenset(selected)


def families_for_nut_types(selected: frozenset[str] | None) -> set[str]:
    """Which nut FAMILIES a specific-nut selection touches (for matrix columns /
    generic mentions). None -> both (the default)."""
    if selected is None:
        return set(_NUT_FAMILY)
    fams: set[str] = set()
    if selected & set(TREE_NUT_TYPES):
        fams.add(TREE_NUTS)
    if PEANUTS in selected:
        fams.add(PEANUTS)
    return fams or set(_NUT_FAMILY)


def specific_tree_nuts(term: str) -> frozenset[str]:
    """The specific tree-nut keys a (lowercased) term names, e.g. 'marzipan' ->
    {almond}. Empty when it's an unspecified tree-nut word ('tree nut') or names no
    tree nut -- the caller then treats it as family-level (can't disaggregate)."""
    return frozenset(
        k for k in TREE_NUT_TYPES
        if any(variant in term for variant in NUT_TYPE_TERMS[k])
    )


def _expand_allergen(allergen: str) -> set[str]:
    if allergen == NUTS:
        return set(_NUT_FAMILY)
    return {allergen}


@dataclass(frozen=True)
class AllergenPrior:
    allergen: str
    risk: float  # 0..1 prior probability the allergen is present
    confidence: float  # 0..1 how much to trust this prior itself
    basis: str  # dish_knowledge | cuisine_baseline | nut_free_claim | default
    rationale: list[str] = field(default_factory=list)
    # How much an *absence* of allergen labels should reassure at this location,
    # i.e. regulatory/labeling culture. Used by the downstream scorer.
    labeling_trust: float = 0.35


# --------------------------------------------------------------------------- #
# Dish knowledge base: dishes that frequently contain nuts regardless of where
# they are served. Matched as substrings on normalized "name + description".
# Each entry: (pattern, allergens, risk, note). Highest-risk match wins.
# --------------------------------------------------------------------------- #
DISH_NUT_KNOWLEDGE: list[tuple[str, set[str], float, str]] = [
    # explicit nut ingredients
    ("peanut", {PEANUTS}, 0.97, "named peanut ingredient"),
    ("groundnut", {PEANUTS}, 0.95, "groundnut = peanut"),
    ("almond", {TREE_NUTS}, 0.95, "named almond"),
    ("amandine", {TREE_NUTS}, 0.93, "amandine = with almonds"),
    ("cashew", {TREE_NUTS}, 0.95, "named cashew"),
    ("walnut", {TREE_NUTS}, 0.95, "named walnut"),
    ("pecan", {TREE_NUTS}, 0.95, "named pecan"),
    ("pistachio", {TREE_NUTS}, 0.96, "named pistachio"),
    ("hazelnut", {TREE_NUTS}, 0.95, "named hazelnut"),
    ("macadamia", {TREE_NUTS}, 0.95, "named macadamia"),
    ("pine nut", {TREE_NUTS}, 0.95, "named pine nut"),
    ("pignoli", {TREE_NUTS}, 0.95, "pignoli = pine nut"),
    ("brazil nut", {TREE_NUTS}, 0.95, "named brazil nut"),
    ("mixed nut", {PEANUTS, TREE_NUTS}, 0.97, "mixed nuts"),
    ("tree nut", {TREE_NUTS}, 0.95, "named tree nut"),
    # nut-derived preparations
    ("pesto", {TREE_NUTS}, 0.85, "pesto usually contains pine nuts"),
    ("nutella", {TREE_NUTS}, 0.95, "Nutella = hazelnut"),
    ("gianduja", {TREE_NUTS}, 0.95, "gianduja = hazelnut chocolate"),
    ("frangelico", {TREE_NUTS}, 0.9, "hazelnut liqueur"),
    ("praline", {TREE_NUTS}, 0.9, "praline = caramelized nuts"),
    ("marzipan", {TREE_NUTS}, 0.95, "marzipan = almond paste"),
    ("frangipane", {TREE_NUTS}, 0.9, "frangipane = almond cream"),
    ("financier", {TREE_NUTS}, 0.85, "financier = almond cake"),
    ("amaretto", {TREE_NUTS}, 0.85, "amaretto = almond"),
    ("amaretti", {TREE_NUTS}, 0.85, "amaretti = almond cookie"),
    ("nougat", {TREE_NUTS}, 0.85, "nougat usually contains nuts"),
    ("bakewell", {TREE_NUTS}, 0.9, "bakewell = almond frangipane"),
    ("dukkah", {TREE_NUTS}, 0.85, "dukkah = nut/seed blend"),
    ("dukka", {TREE_NUTS}, 0.85, "dukkah = nut/seed blend"),
    ("muhammara", {TREE_NUTS}, 0.9, "muhammara = walnut dip"),
    ("romesco", {TREE_NUTS}, 0.85, "romesco = almond/hazelnut sauce"),
    ("baklava", {TREE_NUTS}, 0.95, "baklava = nut pastry"),
    ("baklawa", {TREE_NUTS}, 0.95, "baklava = nut pastry"),
    ("kataifi", {TREE_NUTS}, 0.85, "kataifi = shredded-pastry nut dessert"),
    ("waldorf", {TREE_NUTS}, 0.85, "waldorf salad = walnuts"),
    ("filbert", {TREE_NUTS}, 0.95, "filbert = hazelnut"),
    ("marcona", {TREE_NUTS}, 0.95, "marcona = almond variety"),
    ("turron", {TREE_NUTS}, 0.9, "turrón = almond nougat"),
    ("turrón", {TREE_NUTS}, 0.9, "turrón = almond nougat"),
    ("torrone", {TREE_NUTS}, 0.9, "torrone = almond nougat"),
    ("linzer", {TREE_NUTS}, 0.9, "linzer = almond/hazelnut torte"),
    # 'macaron' (almond-flour meringue) -- guarded against 'macaroon' (coconut) below.
    ("macaron", {TREE_NUTS}, 0.9, "macaron = almond-flour meringue"),
    # nut-sauce / nut-forward dishes
    ("pad thai", {PEANUTS}, 0.9, "pad thai = peanuts"),
    ("satay", {PEANUTS}, 0.9, "satay = peanut sauce"),
    ("sate", {PEANUTS}, 0.88, "sate = peanut sauce"),
    ("massaman", {PEANUTS, TREE_NUTS}, 0.85, "massaman curry = peanuts/cashew"),
    ("gado gado", {PEANUTS}, 0.85, "gado-gado = peanut sauce"),
    ("gado-gado", {PEANUTS}, 0.85, "gado-gado = peanut sauce"),
    ("kung pao", {PEANUTS}, 0.85, "kung pao = peanuts"),
    ("gong bao", {PEANUTS}, 0.85, "gong bao = peanuts"),
    ("korma", {TREE_NUTS}, 0.85, "korma = cashew/almond gravy"),
    ("qorma", {TREE_NUTS}, 0.85, "korma = cashew/almond gravy"),
    ("mole", {PEANUTS, TREE_NUTS}, 0.7, "mole often contains nuts"),
    ("rendang", {TREE_NUTS}, 0.4, "rendang sometimes uses candlenut"),
]

# Multilingual nut INGREDIENT terms so non-English menus aren't a blind spot.
# Deliberately excludes words that collide with coconut/nutmeg in other
# languages (es nuez, fr noix, it noce, de nuss, ja ナッツ) to protect precision.
# (pattern, allergens, risk, note)
_MULTILINGUAL_NUT_TERMS: list[tuple[str, set[str], float, str]] = [
    # peanut
    ("cacahuete", {PEANUTS}, 0.95, "peanut (es)"),
    ("cacahuate", {PEANUTS}, 0.95, "peanut (es)"),
    ("cacahuète", {PEANUTS}, 0.95, "peanut (fr)"),
    ("arachide", {PEANUTS}, 0.95, "peanut (fr/it)"),
    ("arachidi", {PEANUTS}, 0.95, "peanut (it)"),
    ("erdnuss", {PEANUTS}, 0.95, "peanut (de)"),
    ("amendoim", {PEANUTS}, 0.95, "peanut (pt)"),
    ("đậu phộng", {PEANUTS}, 0.95, "peanut (vi)"),
    ("fıstığı", {PEANUTS, TREE_NUTS}, 0.9, "nut (tr; peanut/pistachio)"),
    ("落花生", {PEANUTS}, 0.95, "peanut (ja)"),
    ("ピーナッツ", {PEANUTS}, 0.95, "peanut (ja)"),
    ("花生", {PEANUTS}, 0.95, "peanut (zh)"),
    ("땅콩", {PEANUTS}, 0.95, "peanut (ko)"),
    ("ถั่วลิสง", {PEANUTS}, 0.95, "peanut (th)"),
    ("मूंगफली", {PEANUTS}, 0.95, "peanut (hi)"),
    ("арахис", {PEANUTS}, 0.95, "peanut (ru)"),
    ("فول سوداني", {PEANUTS}, 0.95, "peanut (ar)"),
    # almond
    ("almendra", {TREE_NUTS}, 0.95, "almond (es)"),
    ("amande", {TREE_NUTS}, 0.95, "almond (fr)"),
    ("mandel", {TREE_NUTS}, 0.93, "almond (de/sv)"),
    ("mandorla", {TREE_NUTS}, 0.95, "almond (it)"),
    ("amêndoa", {TREE_NUTS}, 0.95, "almond (pt)"),
    ("badem", {TREE_NUTS}, 0.95, "almond (tr)"),
    ("hạnh nhân", {TREE_NUTS}, 0.95, "almond (vi)"),
    ("アーモンド", {TREE_NUTS}, 0.95, "almond (ja)"),
    ("杏仁", {TREE_NUTS}, 0.95, "almond (zh)"),
    ("아몬드", {TREE_NUTS}, 0.95, "almond (ko)"),
    ("बादाम", {TREE_NUTS}, 0.95, "almond (hi)"),
    ("миндаль", {TREE_NUTS}, 0.95, "almond (ru)"),
    ("لوز", {TREE_NUTS}, 0.95, "almond (ar)"),
    # cashew (avoid bare 'caju' -> 'cajun')
    ("anacardo", {TREE_NUTS}, 0.95, "cashew (es)"),
    ("anacardi", {TREE_NUTS}, 0.95, "cashew (it)"),
    ("cajou", {TREE_NUTS}, 0.95, "cashew (fr)"),
    ("cashewkern", {TREE_NUTS}, 0.95, "cashew (de)"),
    ("カシューナッツ", {TREE_NUTS}, 0.95, "cashew (ja)"),
    ("腰果", {TREE_NUTS}, 0.95, "cashew (zh)"),
    ("hạt điều", {TREE_NUTS}, 0.95, "cashew (vi)"),
    ("काजू", {TREE_NUTS}, 0.95, "cashew (hi)"),
    ("كاجو", {TREE_NUTS}, 0.95, "cashew (ar)"),
    # hazelnut
    ("avellana", {TREE_NUTS}, 0.92, "hazelnut (es)"),
    ("noisette", {TREE_NUTS}, 0.88, "hazelnut (fr)"),
    ("haselnuss", {TREE_NUTS}, 0.92, "hazelnut (de)"),
    ("nocciola", {TREE_NUTS}, 0.92, "hazelnut (it)"),
    ("avelã", {TREE_NUTS}, 0.92, "hazelnut (pt)"),
    ("fındık", {TREE_NUTS}, 0.92, "hazelnut (tr)"),
    ("ヘーゼルナッツ", {TREE_NUTS}, 0.92, "hazelnut (ja)"),
    ("بندق", {TREE_NUTS}, 0.92, "hazelnut (ar)"),
    ("фундук", {TREE_NUTS}, 0.92, "hazelnut (ru)"),
    ("кешью", {TREE_NUTS}, 0.95, "cashew (ru)"),
    ("фисташки", {TREE_NUTS}, 0.95, "pistachio (ru)"),
    # pistachio
    ("pistacho", {TREE_NUTS}, 0.95, "pistachio (es)"),
    ("pistache", {TREE_NUTS}, 0.95, "pistachio (fr)"),
    ("pistazie", {TREE_NUTS}, 0.95, "pistachio (de)"),
    ("pistacchio", {TREE_NUTS}, 0.95, "pistachio (it)"),
    ("ピスタチオ", {TREE_NUTS}, 0.95, "pistachio (ja)"),
    ("开心果", {TREE_NUTS}, 0.95, "pistachio (zh)"),
    ("فستق", {TREE_NUTS}, 0.93, "pistachio (ar)"),
    # walnut (only unambiguous forms)
    ("walnuss", {TREE_NUTS}, 0.93, "walnut (de)"),
    ("くるみ", {TREE_NUTS}, 0.93, "walnut (ja)"),
    ("クルミ", {TREE_NUTS}, 0.93, "walnut (ja)"),
    ("核桃", {TREE_NUTS}, 0.93, "walnut (zh)"),
    ("호두", {TREE_NUTS}, 0.93, "walnut (ko)"),
    ("अखरोट", {TREE_NUTS}, 0.93, "walnut (hi)"),
    ("ceviz", {TREE_NUTS}, 0.93, "walnut (tr)"),
    # pine nut
    ("piñón", {TREE_NUTS}, 0.9, "pine nut (es)"),
    ("pinoli", {TREE_NUTS}, 0.9, "pine nut (it)"),
    ("松子", {TREE_NUTS}, 0.9, "pine nut (zh)"),
    ("잣", {TREE_NUTS}, 0.9, "pine nut (ko)"),
    # native-script names of common nut dishes
    ("팟타이", {PEANUTS}, 0.9, "pad thai (ko)"),
    ("パッタイ", {PEANUTS}, 0.9, "pad thai (ja)"),
    ("馬薩曼", {PEANUTS, TREE_NUTS}, 0.85, "massaman (zh)"),
    ("サテ", {PEANUTS}, 0.9, "satay (ja)"),
    ("사테", {PEANUTS}, 0.9, "satay (ko)"),
    ("バクラヴァ", {TREE_NUTS}, 0.95, "baklava (ja)"),
]

DISH_NUT_KNOWLEDGE.extend(_MULTILINGUAL_NUT_TERMS)

# Per-dish specific-nut tags, for strict per-nut filtering. Dishes whose NAME isn't
# itself a nut word (so specific_tree_nuts can't infer it) are listed explicitly; an
# empty set means "unspecified within its family" (counts for any selection in that
# family -- e.g. baklava/praline could be any tree nut, so we can't rule yours out).
_DISH_NUT_TYPES_OVERRIDE: dict[str, frozenset[str]] = {
    # peanut-sauce / peanut-forward dishes
    "pad thai": frozenset({PEANUTS}), "팟타이": frozenset({PEANUTS}),
    "パッタイ": frozenset({PEANUTS}), "satay": frozenset({PEANUTS}),
    "sate": frozenset({PEANUTS}), "サテ": frozenset({PEANUTS}), "사테": frozenset({PEANUTS}),
    "gado gado": frozenset({PEANUTS}), "gado-gado": frozenset({PEANUTS}),
    "kung pao": frozenset({PEANUTS}), "gong bao": frozenset({PEANUTS}),
    "massaman": frozenset({PEANUTS, CASHEW}), "馬薩曼": frozenset({PEANUTS, CASHEW}),
    "korma": frozenset({CASHEW, ALMOND}), "qorma": frozenset({CASHEW, ALMOND}),
    "mole": frozenset({PEANUTS, ALMOND}), "rendang": frozenset({MACADAMIA}),
    # nut-derived preparations whose name doesn't contain the nut word
    "pesto": frozenset({PINE_NUT}), "muhammara": frozenset({WALNUT}),
    "waldorf": frozenset({WALNUT}), "romesco": frozenset({ALMOND, HAZELNUT}),
    "marzipan": frozenset({ALMOND}), "frangipane": frozenset({ALMOND}),
    "financier": frozenset({ALMOND}), "amaretto": frozenset({ALMOND}),
    "amaretti": frozenset({ALMOND}), "bakewell": frozenset({ALMOND}),
    "amandine": frozenset({ALMOND}), "marcona": frozenset({ALMOND}),
    "turron": frozenset({ALMOND}), "turrón": frozenset({ALMOND}),
    "torrone": frozenset({ALMOND}), "linzer": frozenset({ALMOND, HAZELNUT}),
    "macaron": frozenset({ALMOND}), "nutella": frozenset({HAZELNUT}),
    "gianduja": frozenset({HAZELNUT}), "frangelico": frozenset({HAZELNUT}),
    "filbert": frozenset({HAZELNUT}), "pignoli": frozenset({PINE_NUT}),
    "pine nut": frozenset({PINE_NUT}), "brazil nut": frozenset({BRAZIL_NUT}),
    # unspecified (could be any tree nut) -> empty set
    "praline": frozenset(), "nougat": frozenset(), "dukkah": frozenset(),
    "dukka": frozenset(), "baklava": frozenset(), "baklawa": frozenset(),
    "バクラヴァ": frozenset(), "kataifi": frozenset(), "mixed nut": frozenset(),
    "tree nut": frozenset(),
}


def _entry_nut_types(pattern: str, allergens: set[str]) -> frozenset[str]:
    """The specific nut keys a dish-knowledge entry names (incl. peanut). Empty means
    'unspecified within its family'."""
    if pattern in _DISH_NUT_TYPES_OVERRIDE:
        return _DISH_NUT_TYPES_OVERRIDE[pattern]
    keys = {k for k in NUT_TYPES if any(v in pattern for v in NUT_TYPE_TERMS[k])}
    return frozenset(keys)


# pattern -> specific nut keys, precomputed once (the table is static).
_DISH_NUT_TYPES: dict[str, frozenset[str]] = {
    pattern: _entry_nut_types(pattern, allergens)
    for pattern, allergens, _risk, _note in DISH_NUT_KNOWLEDGE
}


def _entry_in_selection(pattern: str, selected: frozenset[str]) -> bool:
    """Strict per-nut filter for a dish entry that already passed the FAMILY filter:
    keep it only if it names one of the user's selected nuts, OR it's unspecified
    within its family (we can't rule the user's nut out)."""
    nut_types = _DISH_NUT_TYPES.get(pattern, frozenset())
    if not nut_types:
        return True  # unspecified -> the family filter already decided this
    return bool(nut_types & selected)

# --------------------------------------------------------------------------- #
# SUSPECTED-nuts layer (RECALL, not precision): dish TYPES that frequently HIDE
# nuts even when the name doesn't say so -- desserts/baked goods (walnut brownies,
# almond-flour cakes), curries/stir-fries (nut-thickened sauces, peanut oil), etc.
# These are ASSUMPTIONS, so they carry a MODERATE risk at LOW confidence -- enough
# to stop the scorer from treating the dish as clearly safe, not enough to claim a
# confirmed nut dish. Matched only when no explicit nut term is found.
# --------------------------------------------------------------------------- #
_SUSPECTED_RISK = 0.40
_SUSPECTED_CONF = 0.30
SUSPECTED_NUT_PATTERNS: list[tuple[str, set[str], str]] = [
    # baked goods & desserts (nuts very common, often unstated)
    ("brownie", {PEANUTS, TREE_NUTS}, "brownies often contain nuts"),
    ("blondie", {TREE_NUTS}, "blondies often contain nuts"),
    ("cookie", {PEANUTS, TREE_NUTS}, "cookies often contain nuts"),
    ("biscotti", {TREE_NUTS}, "biscotti often contain nuts"),
    ("cake", {TREE_NUTS}, "cakes often contain nuts"),
    ("torte", {TREE_NUTS}, "tortes often contain nuts"),
    ("tart", {TREE_NUTS}, "tarts often contain nuts"),
    ("muffin", {TREE_NUTS}, "muffins often contain nuts"),
    ("scone", {TREE_NUTS}, "scones often contain nuts"),
    ("granola", {TREE_NUTS}, "granola usually contains nuts"),
    ("muesli", {TREE_NUTS}, "muesli usually contains nuts"),
    ("parfait", {TREE_NUTS}, "parfaits often contain nut granola"),
    ("sundae", {PEANUTS, TREE_NUTS}, "sundaes often contain nut toppings"),
    ("gelato", {TREE_NUTS}, "gelato often contains nuts"),
    ("ice cream", {PEANUTS, TREE_NUTS}, "ice cream often contains nut flavors/toppings"),
    ("pastry", {TREE_NUTS}, "pastries often contain nuts"),
    ("strudel", {TREE_NUTS}, "strudel often contains nuts"),
    ("crumble", {TREE_NUTS}, "crumbles often contain nuts"),
    ("cobbler", {TREE_NUTS}, "cobblers sometimes contain nuts"),
    ("fudge", {PEANUTS, TREE_NUTS}, "fudge often contains nuts"),
    ("brittle", {PEANUTS, TREE_NUTS}, "brittle is usually nut-based"),
    ("toffee", {TREE_NUTS}, "toffee often contains nuts"),
    ("energy bar", {PEANUTS, TREE_NUTS}, "energy/protein bars often contain nuts"),
    ("protein bar", {PEANUTS, TREE_NUTS}, "protein bars often contain nuts"),
    ("trail mix", {PEANUTS, TREE_NUTS}, "trail mix usually contains nuts"),
    # nut-prone savory preparations
    ("curry", {PEANUTS, TREE_NUTS}, "curries often use nut-thickened sauces"),
    ("stir fry", {PEANUTS}, "stir-fries often use peanuts/peanut oil"),
    ("stir-fry", {PEANUTS}, "stir-fries often use peanuts/peanut oil"),
    ("crusted", {TREE_NUTS}, "'crusted' dishes are sometimes nut-crusted"),
    ("encrusted", {TREE_NUTS}, "'encrusted' dishes are sometimes nut-crusted"),
]
# At plant-based kitchens, dairy analogues are typically CASHEW-based -- a common
# hidden-nut trap. Only applied when the cuisine is vegan/vegetarian.
_VEGAN_SUSPECTED_PATTERNS = ("cheese", "cream", "parmesan", "ricotta",
                             "mozzarella", "queso", "alfredo", "milk", "butter")

# Explicit nut-free claims lower the prior. Conservative: this only sets a prior,
# the downstream scorer must still treat allergy decisions cautiously.
NUT_FREE_PATTERNS = [
    "nut free",
    "nut-free",
    "no nuts",
    "without nuts",
    "free of nuts",
    "peanut free",
    "peanut-free",
]

# --------------------------------------------------------------------------- #
# Cuisine baseline nut risk (0..1). Heuristic seeds; calibrate later.
# --------------------------------------------------------------------------- #
CUISINE_NUT_BASELINE: dict[str, float] = {
    "thai": 0.60,
    "indonesian": 0.60,
    "malaysian": 0.58,
    "vietnamese": 0.50,
    "indian": 0.55,
    "pakistani": 0.55,
    "afghan": 0.55,
    "middle_eastern": 0.55,
    "west_african": 0.50,
    "chinese": 0.40,
    "mediterranean": 0.40,
    "italian": 0.35,
    "mexican": 0.35,
    "spanish": 0.30,
    "french": 0.30,
    "korean": 0.25,
    "japanese": 0.20,
    "ethiopian": 0.20,
    "american": 0.20,
    "bbq": 0.15,
    "seafood": 0.15,
    "breakfast": 0.18,
    # Wider world coverage (heuristic seeds, calibratable). Elevated where a
    # cuisine traditionally leans on nuts; low for meat/seafood-forward ones.
    "north_african": 0.60,   # tagines, pastilla, almond pastries
    "taiwanese": 0.45,       # heavy peanut use
    "filipino": 0.40,        # kare-kare (peanut), some
    "burmese": 0.55,         # peanut oil, tea-leaf salad nuts
    "cambodian": 0.45,
    "laotian": 0.45,
    "georgian": 0.60,        # walnut-forward (satsivi, pkhali, churchkhela)
    "uzbek": 0.45,           # plov, halva
    "hawaiian": 0.40,        # macadamia
    "soul_food": 0.35,       # pecan, peanut
    "east_african": 0.35,
    "south_african": 0.30,
    "peruvian": 0.30,
    "brazilian": 0.30,
    "argentinian": 0.20,
    "colombian": 0.25,
    "cuban": 0.20,
    "caribbean": 0.30,
    "jamaican": 0.30,
    "portuguese": 0.35,
    "german": 0.30,
    "polish": 0.30,
    "russian": 0.30,
    "ukrainian": 0.30,
    "british": 0.30,
    "mongolian": 0.20,
    "asian": 0.40,          # generic pan-Asian tag (cautious moderate)
    # Baked goods / desserts disproportionately involve nuts (toppings, fillings,
    # praline, marzipan), so they carry an elevated baseline.
    "bakery": 0.45,
    "dessert": 0.45,
    "ice_cream": 0.35,
    # Beverage-forward spots are low base risk.
    "cafe": 0.18,
    # Plant-based kitchens lean HEAVILY on nuts (cashew cheese, nut milks, nut-based
    # sauces/'parmesan', almond bases) -- "vegan = healthy = safe" is a dangerous
    # misread, so these carry an elevated baseline, not a low one.
    "vegan": 0.45,
    "vegetarian": 0.40,
}
DEFAULT_CUISINE_BASELINE = 0.30

# Countries where a cuisine is "at home", so recipes tend to be more authentic
# (and less likely to omit traditional nut ingredients). Boosts the baseline.
CUISINE_HOME_REGIONS: dict[str, set[str]] = {
    "thai": {"TH"},
    "indonesian": {"ID"},
    "malaysian": {"MY"},
    "vietnamese": {"VN"},
    "indian": {"IN", "PK", "BD", "LK", "NP"},
    "pakistani": {"PK", "IN"},
    "afghan": {"AF"},
    "middle_eastern": {"LB", "SY", "JO", "IL", "TR", "EG", "IR", "SA", "AE", "IQ"},
    "west_african": {"NG", "GH", "SN", "CI", "ML"},
    "chinese": {"CN", "TW", "HK"},
    "mexican": {"MX"},
    "ethiopian": {"ET"},
    "north_african": {"MA", "DZ", "TN", "EG", "LY"},
    "east_african": {"KE", "TZ", "SO", "ER"},
    "south_african": {"ZA"},
    "taiwanese": {"TW"},
    "filipino": {"PH"},
    "burmese": {"MM"},
    "cambodian": {"KH"},
    "laotian": {"LA"},
    "georgian": {"GE"},
    "uzbek": {"UZ"},
    "peruvian": {"PE"},
    "brazilian": {"BR"},
    "argentinian": {"AR"},
    "colombian": {"CO"},
    "cuban": {"CU"},
    "jamaican": {"JM"},
    "portuguese": {"PT"},
    "german": {"DE", "AT", "CH"},
    "polish": {"PL"},
    "russian": {"RU"},
    "ukrainian": {"UA"},
    "british": {"GB", "IE"},
    "mongolian": {"MN"},
    "hawaiian": {"US"},     # macadamia-forward; "at home" in the US (HI)
    "soul_food": {"US"},    # pecan/peanut-forward; at home in the US
}

# Allergen-labeling culture: how much a *missing* nut label can be trusted.
# High in places with allergen-labeling regulation, low elsewhere.
HIGH_LABELING_COUNTRIES = {
    "US", "CA", "GB", "IE", "AU", "NZ",
    "FR", "DE", "IT", "ES", "NL", "BE", "SE", "DK", "FI", "NO",
    "PT", "AT", "PL", "CZ", "GR", "CH", "JP", "SG", "KR", "HK",
}
HIGH_LABELING_TRUST = 0.70
LOW_LABELING_TRUST = 0.35


# --------------------------------------------------------------------------- #
# Cuisine + region normalization (handles OSM, Google, Geoapify category styles)
# --------------------------------------------------------------------------- #
CUISINE_ALIASES: dict[str, str] = {
    "thai": "thai",
    "indonesian": "indonesian",
    "malaysian": "malaysian",
    "vietnamese": "vietnamese",
    "indian": "indian",
    "pakistani": "pakistani",
    "afghan": "afghan",
    "afghani": "afghan",
    "bangladeshi": "indian",
    "nepalese": "indian",
    "sri_lankan": "indian",
    "middle_eastern": "middle_eastern",
    "middle eastern": "middle_eastern",
    "lebanese": "middle_eastern",
    "turkish": "middle_eastern",
    "persian": "middle_eastern",
    "iranian": "middle_eastern",
    "arab": "middle_eastern",
    "syrian": "middle_eastern",
    "israeli": "middle_eastern",
    "kebab": "middle_eastern",
    "shawarma": "middle_eastern",
    "falafel": "middle_eastern",
    "mediterranean_middle_eastern": "middle_eastern",
    "mediterranean": "mediterranean",
    "greek": "mediterranean",
    "chinese": "chinese",
    "szechuan": "chinese",
    "sichuan": "chinese",
    "cantonese": "chinese",
    "dim_sum": "chinese",
    "korean": "korean",
    "japanese": "japanese",
    "sushi": "japanese",
    "ramen": "japanese",
    "udon": "japanese",
    "soba": "japanese",
    "mexican": "mexican",
    "tex-mex": "mexican",
    "italian": "italian",
    "pizza": "italian",
    "french": "french",
    "spanish": "spanish",
    "tapas": "spanish",
    "ethiopian": "ethiopian",
    "west_african": "west_african",
    "nigerian": "west_african",
    "ghanaian": "west_african",
    "senegalese": "west_african",
    "american": "american",
    "burger": "american",
    "diner": "american",
    "steak_house": "american",
    "barbecue": "bbq",
    "bbq": "bbq",
    "seafood": "seafood",
    "breakfast": "breakfast",
    "brunch": "breakfast",
    "sandwich": "american",
    "deli": "american",
    "bakery": "bakery",
    "pastry": "bakery",
    "donut": "bakery",
    "doughnut": "bakery",
    "dessert": "dessert",
    "ice_cream": "ice_cream",
    "gelato": "ice_cream",
    "cafe": "cafe",
    "coffee_shop": "cafe",
    "coffee": "cafe",
    "bubble_tea": "cafe",
    "boba": "cafe",
    "tea": "cafe",
    "juice": "cafe",
    # Wider world coverage
    "egyptian": "north_african",
    "moroccan": "north_african",
    "algerian": "north_african",
    "tunisian": "north_african",
    "libyan": "north_african",
    "north_african": "north_african",
    "kenyan": "east_african",
    "tanzanian": "east_african",
    "somali": "east_african",
    "eritrean": "east_african",
    "south_african": "south_african",
    "taiwanese": "taiwanese",
    "filipino": "filipino",
    "singaporean": "malaysian",
    "burmese": "burmese",
    "myanmar": "burmese",
    "cambodian": "cambodian",
    "khmer": "cambodian",
    "laotian": "laotian",
    "lao": "laotian",
    "peruvian": "peruvian",
    "brazilian": "brazilian",
    "argentinian": "argentinian",
    "argentine": "argentinian",
    "colombian": "colombian",
    "venezuelan": "colombian",
    "cuban": "cuban",
    "caribbean": "caribbean",
    "jamaican": "jamaican",
    "haitian": "caribbean",
    "portuguese": "portuguese",
    "german": "german",
    "austrian": "german",
    "swiss": "german",
    "polish": "polish",
    "russian": "russian",
    "georgian": "georgian",
    "ukrainian": "ukrainian",
    "armenian": "middle_eastern",
    "hawaiian": "hawaiian",
    "british": "british",
    "english": "british",
    "scottish": "british",
    "irish": "british",
    "uzbek": "uzbek",
    "mongolian": "mongolian",
    "soul_food": "soul_food",
    "soul": "soul_food",
    "cajun": "soul_food",
    "creole": "soul_food",
    # Generic provider tags Google/OSM actually emit
    "taco": "mexican",
    "asian": "asian",
    "pan_asian": "asian",
    "pan-asian": "asian",
    "asian_fusion": "asian",
    "fusion": "asian",
    "oriental": "asian",
    "noodle": "asian",
    "noodles": "asian",
    # Common American-tag variants providers emit (were falling to the flat default).
    "southern": "soul_food",
    "new_american": "american",
    "modern_american": "american",
    "fast_food": "american",
    "fried_chicken": "american",
    "chicken_wings": "american",
    "wings": "american",
    "hamburger": "american",
    "bistro": "french",
    "gastropub": "british",
    "pub": "british",
    # Plant-based dietary styles -> kept as cuisine cues (nuts common in vegan cooking).
    "vegan": "vegan",
    "vegan_restaurant": "vegan",
    "plant_based": "vegan",
    "plant-based": "vegan",
    "vegetarian": "vegetarian",
    "vegetarian_restaurant": "vegetarian",
    "veggie": "vegetarian",
}

COUNTRY_ALIASES: dict[str, str] = {
    "usa": "US", "u.s.a": "US", "u.s.a.": "US", "us": "US",
    "united states": "US", "united states of america": "US",
    "canada": "CA",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "ireland": "IE",
    "australia": "AU", "new zealand": "NZ",
    "india": "IN", "pakistan": "PK", "bangladesh": "BD", "sri lanka": "LK", "nepal": "NP",
    "thailand": "TH", "vietnam": "VN", "indonesia": "ID", "malaysia": "MY",
    "china": "CN", "taiwan": "TW", "hong kong": "HK", "japan": "JP", "south korea": "KR",
    "lebanon": "LB", "syria": "SY", "jordan": "JO", "israel": "IL",
    "turkey": "TR", "türkiye": "TR", "turkiye": "TR",
    "egypt": "EG", "iran": "IR", "saudi arabia": "SA", "iceland": "IS",
    "united arab emirates": "AE", "uae": "AE", "iraq": "IQ", "afghanistan": "AF",
    "mexico": "MX", "france": "FR", "germany": "DE", "italy": "IT", "spain": "ES",
    "netherlands": "NL", "ethiopia": "ET", "nigeria": "NG", "ghana": "GH",
    "brazil": "BR", "singapore": "SG", "peru": "PE", "chile": "CL",
    "argentina": "AR", "colombia": "CO", "venezuela": "VE", "cuba": "CU",
    "jamaica": "JM", "morocco": "MA", "algeria": "DZ", "tunisia": "TN",
    "libya": "LY", "kenya": "KE", "tanzania": "TZ", "south africa": "ZA",
    "russia": "RU", "georgia": "GE", "ukraine": "UA", "denmark": "DK",
    "sweden": "SE", "norway": "NO", "finland": "FI", "poland": "PL",
    "czech republic": "CZ", "czechia": "CZ", "austria": "AT", "switzerland": "CH",
    "belgium": "BE", "portugal": "PT", "greece": "GR", "philippines": "PH",
    "myanmar": "MM", "burma": "MM", "cambodia": "KH", "laos": "LA",
    "uzbekistan": "UZ", "mongolia": "MN", "qatar": "QA", "kuwait": "KW",
    "bahrain": "BH", "oman": "OM", "armenia": "AM",
}

# US state abbreviations imply a US address even without "USA" at the end.
_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}

# Full US state names, so an address written "..., Oregon" (no 2-letter code, no "USA")
# still resolves to US instead of 'unknown'. "georgia" is the one name that is ALSO a
# country (COUNTRY_ALIASES), so it is excluded here and disambiguated separately.
_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
}
# Names that are BOTH a US state and a country alias key; need a US signal to call US.
_US_STATE_COUNTRY_COLLISIONS = _US_STATE_NAMES & set(COUNTRY_ALIASES)  # {"georgia"}
_US_STATE_NAMES_UNAMBIGUOUS = _US_STATE_NAMES - _US_STATE_COUNTRY_COLLISIONS


def normalize_cuisine(categories: list[str] | None) -> list[str]:
    """Map provider category strings to canonical cuisine keys (order-preserving)."""
    found: list[str] = []
    for raw in categories or []:
        for token in _cuisine_tokens(raw):
            canonical = CUISINE_ALIASES.get(token)
            if canonical and canonical not in found:
                found.append(canonical)
    return found


def _cuisine_tokens(raw: str) -> list[str]:
    value = raw.strip().lower()
    # Drop provider key prefixes: "cuisine:indian", "primary_type:indian_restaurant".
    if ":" in value:
        value = value.split(":", 1)[1]
    # Geoapify dotted categories: "catering.restaurant.indian".
    value = value.replace("catering.", "").replace("restaurant.", "")
    # OSM multi-value: "indian;thai".
    pieces = re.split(r"[;.,/]", value)
    tokens = []
    for piece in pieces:
        piece = piece.strip().replace(" ", "_")
        if piece.endswith("_restaurant"):
            piece = piece[: -len("_restaurant")]
        if piece:
            tokens.append(piece)
            tokens.append(piece.replace("_", " "))
    return tokens


# --------------------------------------------------------------------------- #
# Name-based cuisine inference: when a provider only gives a generic place type
# ("restaurant", "meal_takeaway"), a distinctive word in the restaurant's NAME is
# often the only cuisine signal we have. Deterministic and free, like the rest of
# this layer. Whole-word matched on the lowercased name; values are canonical
# CUISINE_NUT_BASELINE keys. A name cue is a weak signal, so the prior it yields is
# still a low-confidence cuisine_baseline -- it cannot fabricate menu evidence.
# --------------------------------------------------------------------------- #
NAME_CUISINE_HINTS: dict[str, str] = {
    # Japanese
    "sushi": "japanese", "ramen": "japanese", "izakaya": "japanese",
    "yakitori": "japanese", "teriyaki": "japanese", "donburi": "japanese",
    "udon": "japanese", "soba": "japanese", "omakase": "japanese",
    # Vietnamese / Thai
    "pho": "vietnamese", "banh mi": "vietnamese",
    "thai": "thai", "pad thai": "thai", "tom yum": "thai",
    # Italian
    "pizzeria": "italian", "trattoria": "italian", "osteria": "italian",
    "ristorante": "italian", "pizza": "italian", "pasta": "italian",
    # Mexican
    "taqueria": "mexican", "cantina": "mexican", "burrito": "mexican",
    "taco": "mexican", "tacos": "mexican", "mexican": "mexican",
    # Indian (incl. sweets/mithai -- cashew/pistachio heavy)
    "tandoor": "indian", "tandoori": "indian", "biryani": "indian",
    "masala": "indian", "tikka": "indian", "dosa": "indian", "chaat": "indian",
    "curry": "indian", "punjabi": "indian", "mithai": "indian", "indian": "indian",
    # Middle Eastern / Mediterranean
    "kebab": "middle_eastern", "shawarma": "middle_eastern",
    "falafel": "middle_eastern", "hummus": "middle_eastern", "gyro": "middle_eastern",
    "mediterranean": "mediterranean", "greek": "mediterranean", "souvlaki": "mediterranean",
    # Chinese / pan-Asian
    "dim sum": "chinese", "szechuan": "chinese", "sichuan": "chinese",
    "wok": "chinese", "dumpling": "chinese", "dumplings": "chinese", "chinese": "chinese",
    "noodle": "asian", "noodles": "asian",
    # Korean
    "korean": "korean", "bulgogi": "korean", "kimchi": "korean",
    # BBQ
    "bbq": "bbq", "barbecue": "bbq", "smokehouse": "bbq",
    # Sweet / baked
    "gelato": "ice_cream", "creamery": "ice_cream", "ice cream": "ice_cream",
    "patisserie": "bakery", "boulangerie": "bakery", "bakery": "bakery",
    "bakehouse": "bakery", "bakeshop": "bakery", "donut": "bakery",
    "donuts": "bakery", "doughnut": "bakery", "doughnuts": "bakery",
    "sweets": "dessert", "candy": "dessert", "confectionery": "dessert",
    # Cafe / French
    "cafe": "cafe", "café": "cafe", "coffee": "cafe", "espresso": "cafe",
    "teahouse": "cafe", "boba": "cafe",
    "bistro": "french", "brasserie": "french", "creperie": "french", "crêperie": "french",
    # Others
    "tapas": "spanish", "poke": "hawaiian", "ceviche": "peruvian", "peruvian": "peruvian",
    "churrasco": "brazilian", "churrascaria": "brazilian",
    "ethiopian": "ethiopian", "seafood": "seafood", "oyster": "seafood",
    "diner": "american", "steakhouse": "american", "burger": "american", "deli": "american",
    "vegan": "vegan", "plant based": "vegan", "plant-based": "vegan", "vegetarian": "vegetarian",
}


def infer_cuisine_from_name(name: str | None) -> list[str]:
    """Best-effort canonical cuisines from a restaurant NAME, used only when the
    provider categories carry no cuisine signal. Whole-word matched and order-
    preserving. Returns ``[]`` when nothing distinctive is found."""
    text = _normalize(name or "")
    if not text:
        return []
    found: list[str] = []
    for cue, canonical in NAME_CUISINE_HINTS.items():
        if canonical in found:
            continue
        if re.search(r"\b" + re.escape(cue) + r"\b", text):
            found.append(canonical)
    return found


def cuisines_for(categories: list[str] | None, name: str | None = None) -> list[str]:
    """Canonical cuisines from provider categories, falling back to name inference
    when the categories carry no cuisine signal (e.g. only 'restaurant')."""
    cuisines = normalize_cuisine(categories)
    if not cuisines and name:
        cuisines = infer_cuisine_from_name(name)
    return cuisines


# Coarse country bounding boxes (lat_min, lat_max, lon_min, lon_max). Used ONLY as a
# fallback when the address string can't resolve a country -- enough to pick the right
# allergen-labeling/absence-inference region (country granularity), not exact borders.
# Ordered so the US (the primary market) is checked first; the US/Canada border is
# approximated at the 49th parallel.
_COUNTRY_BBOXES: list[tuple[str, float, float, float, float]] = [
    ("US", 24.5, 49.4, -125.0, -66.9),    # contiguous
    ("US", 51.0, 71.5, -169.0, -129.0),   # Alaska
    ("US", 18.9, 22.3, -160.3, -154.8),   # Hawaii
    ("CA", 49.4, 83.2, -141.1, -52.6),
    ("GB", 49.9, 60.9, -8.7, 1.8),
    ("IE", 51.4, 55.5, -10.6, -5.9),
    ("AU", -43.7, -10.6, 113.1, 153.7),
    ("NZ", -47.3, -34.3, 166.3, 178.7),
]


def _country_from_coords(latitude: float | None, longitude: float | None) -> str | None:
    if latitude is None or longitude is None:
        return None
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    for code, lat_min, lat_max, lon_min, lon_max in _COUNTRY_BBOXES:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return code
    return None


def region_from_address(
    address: str | None,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
) -> str:
    """Best-effort ISO-ish country code. Prefers the address string (most reliable),
    then falls back to coordinates so a place with an unrecognized address but known
    lat/lon still resolves a region (region drives allergen-labeling/absence-inference,
    so 'unknown' under-credits clean menus). 'unknown' only if both fail."""
    # Split on commas AND " - " (space-dash-space). Google returns many addresses --
    # especially across the Gulf / MENA -- delimited by dashes with no commas, e.g.
    # "35HJ+JF - Al Thanyah Second - Dubai - United Arab Emirates". Splitting on commas
    # alone left that as one segment, so the trailing country ("United Arab Emirates",
    # a known alias) was never isolated. Requiring surrounding spaces keeps hyphenated
    # names intact ("Stratford-upon-Avon", "Al-Thanyah").
    segments = [seg.strip() for seg in re.split(r",|\s[-–]\s", address or "") if seg.strip()]
    seg_lowers = [s.lower() for s in segments]
    # A segment's leading words with any trailing postcode digits stripped
    # ("Georgia 30301" -> "georgia"), for full-state-name matching.
    seg_names = [re.sub(r"\s+\d.*$", "", s).strip() for s in seg_lowers]

    # Strong US signals that disambiguate a US locale named like a country (e.g. the
    # state "Georgia" vs the country): an explicit US country word, a 2-letter US state
    # code, or a 5-digit US ZIP (NOT a bare numeric postcode alone -- many countries use
    # those, but a 5-digit ZIP alongside the state name "Georgia" is decisive).
    has_us_word = any(COUNTRY_ALIASES.get(s) == "US" for s in seg_lowers)
    # A US state code, but only where it sits like one in a real US address: the last
    # segment's final token ("Portland, OR") or immediately before a ZIP ("San Jose,
    # CA 95129"). Without this, an interior foreign token that happens to spell a state
    # code -- the Arabic article "Al" (Alabama), Iberian/French "La" (Louisiana) -- would
    # falsely imply US.
    last_tokens = re.split(r"\s+", segments[-1].upper()) if segments else []
    has_state_code = any(
        token in _US_STATE_CODES
        and (
            i == len(last_tokens) - 1
            or bool(re.fullmatch(r"\d{5}(?:-\d{4})?", last_tokens[i + 1]))
        )
        for i, token in enumerate(last_tokens)
    )
    has_us_zip = bool(re.search(r"\b\d{5}(?:-\d{4})?\b", address or ""))
    us_context = has_us_word or has_state_code or has_us_zip

    # 1) Trailing segments as a country name ("..., Australia") -- but a name that is
    # also a US state ("Georgia") resolves to US when the address has a strong US signal.
    for seg in reversed(seg_lowers):
        country = COUNTRY_ALIASES.get(seg)
        if country:
            if seg in _US_STATE_COUNTRY_COLLISIONS and us_context:
                return "US"
            return country
    # 2) A US state code in the last segment ("San Jose, CA 95129").
    if has_state_code:
        return "US"
    # 3) A full US state name. Unambiguous names ("Oregon") always imply US; the
    # collision name ("Georgia 30301") needs a US signal so country "Georgia" isn't
    # mislabeled.
    for name in seg_names:
        if name in _US_STATE_NAMES_UNAMBIGUOUS:
            return "US"
        if name in _US_STATE_COUNTRY_COLLISIONS and us_context:
            return "US"
    # 4) Coordinates fallback (coarse country bbox).
    return _country_from_coords(latitude, longitude) or "unknown"


def labeling_trust_for_region(region: str) -> float:
    return HIGH_LABELING_TRUST if region in HIGH_LABELING_COUNTRIES else LOW_LABELING_TRUST


# Regions that legally REQUIRE restaurants to disclose allergen info, so a menu
# that does NOT name an allergen is meaningfully more likely to be free of it.
# This is distinct from labeling_trust (how much an EXPLICIT chart can be trusted):
# an allergen chart is real data regardless of region, but ABSENCE only implies
# absence where disclosure is mandated. The US mandates packaged-food labeling
# (FALCPA) but NOT restaurant per-dish disclosure -- so "the menu didn't mention
# nuts" is weak evidence there, and weaker still where nothing is mandated.
MANDATE_LABELING_REGIONS = {
    "GB", "IE", "AU", "NZ",
    "FR", "DE", "IT", "ES", "NL", "BE", "SE", "DK", "FI", "NO",
    "PT", "AT", "PL", "CZ", "GR", "CH",
}


def absence_inference_factor(region: str) -> float:
    """How much a clean (allergen-not-mentioned) menu may lower risk, by region.
    1.0 where restaurant allergen disclosure is mandated; partial in the US/CA
    (packaged-food labeling only); low elsewhere."""
    if region in MANDATE_LABELING_REGIONS:
        return 1.0
    if region in {"US", "CA"}:
        return 0.55
    return 0.4


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def score_menu_item_prior(
    *,
    item_name: str | None,
    description: str | None = None,
    cuisines: list[str] | None = None,
    region: str = "unknown",
    allergen: str = NUTS,
    baseline: "AllergenPrior | None" = None,
    wanted: set[str] | None = None,
    wanted_nuts: frozenset[str] | None = None,
) -> AllergenPrior:
    """Prior risk that a specific menu item involves ``allergen``.

    Dish knowledge (specific) overrides the cuisine baseline (general); an
    explicit nut-free claim lowers the prior. ``baseline`` lets a caller scoring
    many items for one restaurant pass the (identical) cuisine/location prior in
    once instead of having it recomputed per item; ``wanted`` similarly lets the
    caller pass the pre-expanded allergen family. ``wanted_nuts`` (a specific-nut
    selection) further restricts dish matches to the user's actual nuts.
    """
    if wanted is None:
        wanted = _expand_allergen(allergen)
    text = _normalize(f"{item_name or ''} {description or ''}")
    trust = labeling_trust_for_region(region)

    if any(pattern in text for pattern in NUT_FREE_PATTERNS):
        return AllergenPrior(
            allergen=allergen,
            risk=0.08,
            confidence=0.6,
            basis="nut_free_claim",
            rationale=["menu text states a nut-free claim (still verify directly)"],
            labeling_trust=trust,
        )

    dish_match = _best_dish_match(text, wanted, wanted_nuts)
    if baseline is None:
        baseline = score_restaurant_prior(
            cuisines=cuisines, region=region, allergen=allergen
        )

    if dish_match is not None:
        risk, note = dish_match
        # Authentic-region preparations are a little less likely to omit nuts.
        risk = _apply_home_boost(risk, cuisines, region, weight=0.10)
        rationale = [f"dish knowledge: {note}"]
        if baseline.basis == "cuisine_baseline":
            rationale.extend(baseline.rationale)
        return AllergenPrior(
            allergen=allergen,
            risk=_clamp(risk),
            confidence=0.8,
            basis="dish_knowledge",
            rationale=rationale,
            labeling_trust=trust,
        )

    # RECALL: the name/description didn't NAME a nut, but the dish TYPE often hides
    # one. Make the assumption, at moderate risk + LOW confidence (never below the
    # cuisine floor). Lets the scorer treat it as "uncertain", not "clearly safe".
    suspected = _suspected_match(text, wanted, cuisines)
    if suspected is not None:
        return AllergenPrior(
            allergen=allergen,
            risk=_clamp(max(baseline.risk, _SUSPECTED_RISK)),
            confidence=_SUSPECTED_CONF,
            basis="suspected_nuts",
            rationale=[f"possible nuts: {suspected} (low confidence -- an assumption, not stated)"],
            labeling_trust=trust,
        )

    return baseline


def score_restaurant_prior(
    *,
    cuisines: list[str] | None,
    region: str = "unknown",
    allergen: str = NUTS,
) -> AllergenPrior:
    """Cuisine x location prior — the fallback when no menu evidence exists."""
    trust = labeling_trust_for_region(region)
    if not cuisines:
        return AllergenPrior(
            allergen=allergen,
            risk=DEFAULT_CUISINE_BASELINE,
            confidence=0.25,
            basis="default",
            rationale=["no cuisine signal; using default baseline"],
            labeling_trust=trust,
        )

    best_cuisine = max(
        cuisines, key=lambda c: CUISINE_NUT_BASELINE.get(c, DEFAULT_CUISINE_BASELINE)
    )
    base = CUISINE_NUT_BASELINE.get(best_cuisine, DEFAULT_CUISINE_BASELINE)
    boosted = _apply_home_boost(base, cuisines, region, weight=0.25)

    rationale = [f"cuisine baseline: {best_cuisine} ({base:.2f})"]
    if boosted > base + 1e-9:
        rationale.append(f"served in home region ({region}); prevalence boosted")

    return AllergenPrior(
        allergen=allergen,
        risk=_clamp(boosted),
        confidence=0.4,
        basis="cuisine_baseline",
        rationale=rationale,
        labeling_trust=trust,
    )


@dataclass(frozen=True)
class RestaurantNutRisk:
    risk: float
    confidence: float
    rationale: list[str]
    labeling_trust: float
    riskiest_items: list[tuple[str, float]]  # (item_name, risk), high to low
    # Per-dish detail (name, risk, confidence, basis) so the scorer can tell a NAMED
    # nut dish from a low-confidence SUSPECTED one. Defaulted for back-compat.
    item_details: list[dict[str, Any]] = field(default_factory=list)


def restaurant_nut_risk(
    *,
    cuisines: list[str] | None,
    region: str = "unknown",
    menu_items: list[dict[str, str]] | None = None,
    allergen: str = NUTS,
    risky_threshold: float = 0.5,
    baseline: "AllergenPrior | None" = None,
    wanted_nuts: frozenset[str] | None = None,
) -> RestaurantNutRisk:
    """Combine the cuisine/location prior with per-item dish priors.

    The cuisine/location prior is the floor (works with no menu); known risky
    dishes raise it. This is a prior summary, NOT a final safety verdict — the
    menu-evidence stage should still refine it. ``baseline`` lets a caller that has
    already computed the (identical) cuisine/location prior pass it in instead of
    having it recomputed here. ``wanted_nuts`` (a specific-nut selection) restricts
    the per-dish matches to the user's actual nuts; None keeps the family default.
    """
    base = baseline if baseline is not None else score_restaurant_prior(
        cuisines=cuisines, region=region, allergen=allergen
    )
    # The wanted allergen family is constant across the restaurant's items; expand it
    # once and thread it into the per-item prior instead of rebuilding it per item.
    # A specific-nut selection narrows the family to just the families it touches.
    wanted = (
        families_for_nut_types(wanted_nuts) if wanted_nuts is not None
        else _expand_allergen(allergen)
    )
    item_scores: list[tuple[str, float]] = []
    item_details: list[dict[str, Any]] = []
    for item in menu_items or []:
        prior = score_menu_item_prior(
            item_name=item.get("item_name") or item.get("name"),
            description=item.get("description"),
            cuisines=cuisines,
            region=region,
            allergen=allergen,
            baseline=base,  # reuse the one cuisine/location prior; don't recompute per item
            wanted=wanted,
            wanted_nuts=wanted_nuts,
        )
        name = (item.get("item_name") or item.get("name") or "").strip()
        if name:
            item_scores.append((name, prior.risk))
            item_details.append({
                "name": name, "risk": prior.risk,
                "confidence": prior.confidence, "basis": prior.basis,
            })

    item_scores.sort(key=lambda pair: pair[1], reverse=True)
    item_details.sort(key=lambda d: d["risk"], reverse=True)
    risky = [pair for pair in item_scores if pair[1] >= risky_threshold]

    if item_scores:
        top = item_scores[0][1]
        risk = max(base.risk, top)
        confidence = 0.8 if risky else 0.55
        rationale = list(base.rationale)
        if risky:
            rationale.append(
                f"{len(risky)} menu item(s) match known nut-risk dishes "
                f"(e.g. {risky[0][0]})"
            )
        else:
            rationale.append("no listed items matched known nut-risk dishes")
    else:
        risk, confidence, rationale = base.risk, base.confidence, list(base.rationale)
        rationale.append("no menu items available; cuisine/location prior only")

    return RestaurantNutRisk(
        risk=_clamp(risk),
        confidence=confidence,
        rationale=rationale,
        labeling_trust=base.labeling_trust,
        riskiest_items=item_scores[:5],
        item_details=item_details,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
# Short dish patterns that are literal substrings of unrelated, non-nut words.
# As in ``menu_text._ALLERGEN_FALSE_FRIENDS`` we keep substring matching (so
# "satays", "kormas", "moles" still match) but ignore an occurrence that sits
# entirely inside one of these known false-friend words. Without this guard
# "mole" (Mexican mole sauce) fires on every "guacamole" / "molecular".
_DISH_FALSE_FRIENDS: dict[str, set[str]] = {
    "mole": {"guacamole", "guacamoles", "molecular"},
    # "macaron" (almond) is a substring of "macaroon" (typically coconut); don't let a
    # coconut macaroon read as an almond dish.
    "macaron": {"macaroon", "macaroons"},
}


def _best_dish_match(
    text: str, wanted: set[str], wanted_nuts: frozenset[str] | None = None
) -> tuple[float, str] | None:
    best: tuple[float, str] | None = None
    for pattern, allergens, risk, note in DISH_NUT_KNOWLEDGE:
        if not (allergens & wanted):
            continue
        # Strict per-nut: skip a dish that names ONLY nuts the user didn't select
        # (unspecified-within-family dishes still pass -- can't rule the user's out).
        if wanted_nuts is not None and not _entry_in_selection(pattern, wanted_nuts):
            continue
        if _pattern_present(pattern, text):
            if best is None or risk > best[0]:
                best = (risk, note)
                # The table maxes out at MAX_RISK; once we've matched a top-risk dish
                # no later entry can beat it (strict >), so stop scanning. Same result,
                # fewer substring checks. (Home boost is applied once, afterward.)
                if best[0] >= MAX_RISK:
                    break
    return best


def _suspected_match(text: str, wanted: set[str], cuisines: list[str] | None) -> str | None:
    """A low-confidence assumption that a dish HIDES nuts based on its type, even
    though no nut is named. Returns a human note, or None. Plant-based kitchens get
    an extra check (dairy analogues are usually cashew-based)."""
    for pattern, allergens, note in SUSPECTED_NUT_PATTERNS:
        if (allergens & wanted) and _pattern_present(pattern, text):
            return note
    if cuisines and (wanted & {TREE_NUTS}) and any(
        c in ("vegan", "vegetarian") for c in cuisines
    ):
        for pattern in _VEGAN_SUSPECTED_PATTERNS:
            if pattern in text:
                return f"plant-based '{pattern}' is usually cashew-based"
    return None


def _pattern_present(pattern: str, text: str) -> bool:
    """``pattern in text``, but a pattern with known false friends only counts
    when at least one occurrence is not buried inside a false-friend word."""
    false_friends = _DISH_FALSE_FRIENDS.get(pattern)
    if not false_friends:
        return pattern in text
    index = text.find(pattern)
    while index != -1:
        if _enclosing_word(text, index, len(pattern)) not in false_friends:
            return True
        index = text.find(pattern, index + 1)
    return False


def _enclosing_word(text: str, start: int, length: int) -> str:
    begin = start
    while begin > 0 and text[begin - 1].isalpha():
        begin -= 1
    end = start + length
    while end < len(text) and text[end].isalpha():
        end += 1
    return text[begin:end]


def _apply_home_boost(
    risk: float, cuisines: list[str] | None, region: str, *, weight: float
) -> float:
    if region == "unknown" or not cuisines:
        return risk
    for cuisine in cuisines:
        if region in CUISINE_HOME_REGIONS.get(cuisine, set()):
            return _clamp(risk * (1.0 + weight))
    return risk


# Canonical text key (lowercase + collapse whitespace + trim); shared via textutil.
_normalize = norm_ws


# Shared risk ceiling for the whole pipeline: a verdict is never presented as 100%
# certain. The prior layer and the scorer must agree on it, so it lives here (the
# scorer imports clamp_risk) and cannot drift between the two layers.
MAX_RISK = 0.97


def clamp_risk(value: float) -> float:
    return max(0.0, min(MAX_RISK, value))


_clamp = clamp_risk


# --------------------------------------------------------------------------- #
# Generic per-allergen prior (registry-driven twin of the nut-focused layer
# above). Reads small JSON knowledge bases under data/allergen_kb/ instead of
# the hand-tuned Python nut tables, so any allergen in the Task-1 registry gets
# a (coarser, still non-zero) prior even before its own KB is built out.
#
# Do NOT edit anything above this line — the nut quality gate depends on those
# symbols/tables staying byte-identical.
# --------------------------------------------------------------------------- #

# Defined locally (not imported from safeplate.common) to avoid a risk of an
# import cycle: safeplate.common pulls in modules that may import back into
# allergen_prior's package during app startup.
_ALLERGEN_KB_DIR = Path(__file__).resolve().parents[1] / "data" / "allergen_kb"


@lru_cache(maxsize=None)
def _load_cuisine_baseline_table() -> dict:
    path = _ALLERGEN_KB_DIR / "cuisine_baselines.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def load_allergen_kb(allergen: str) -> tuple[tuple[str, float, str], ...]:
    """(dish_pattern, risk, note) entries for a canonical allergen key; () if none."""
    path = _ALLERGEN_KB_DIR / f"{allergen}.json"
    if not path.exists():
        return ()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple((str(p).lower(), float(r), str(n)) for p, r, n in raw)


def allergen_cuisine_baseline(
    allergen: str, cuisines: list[str] | None, region: str = "unknown"
) -> "AllergenPrior":
    """Cuisine x location baseline for an arbitrary allergen (generic twin of the
    CUISINE_NUT_BASELINE lookup). Unknown allergen/cuisine -> low, non-zero default."""
    table = _load_cuisine_baseline_table()
    per_allergen = table.get(allergen, {})
    global_default = float(table.get("_default", 0.15))
    base = float(per_allergen.get("_default", global_default))
    norm = normalize_cuisine(cuisines)
    for cuisine in norm:
        if cuisine in per_allergen:
            base = max(base, float(per_allergen[cuisine]))
    trust = labeling_trust_for_region(region)
    risk = _apply_home_boost(base, norm, region, weight=0.25)
    basis = "cuisine_baseline" if per_allergen else "default"
    return AllergenPrior(
        allergen=allergen, risk=clamp_risk(risk), confidence=0.5,
        basis=basis, rationale=[f"{allergen} cuisine baseline"], labeling_trust=trust,
    )


def restaurant_allergen_risk(
    *,
    allergen: str,
    cuisines: list[str] | None,
    region: str = "unknown",
    menu_items: list[dict[str, str]] | None = None,
    baseline: "AllergenPrior | None" = None,
) -> RestaurantNutRisk:
    """Combine the cuisine/location baseline (floor) with per-dish KB matches for an
    arbitrary allergen. Generic twin of restaurant_nut_risk."""
    base = baseline or allergen_cuisine_baseline(allergen, cuisines, region)
    kb = load_allergen_kb(allergen)
    risk = base.risk
    rationale = list(base.rationale)
    details: list[dict[str, Any]] = []
    riskiest: list[tuple[str, float]] = []
    for item in menu_items or []:
        name = str(item.get("name") or "")
        low = name.lower()
        best = 0.0
        note = ""
        for pattern, dish_risk, dish_note in kb:
            if pattern in low and dish_risk > best:
                best, note = dish_risk, dish_note
        if best > 0.0:
            boosted = clamp_risk(_apply_home_boost(best, normalize_cuisine(cuisines), region, weight=0.10))
            details.append({"name": name, "risk": boosted, "confidence": 0.6,
                            "basis": f"suspected_{allergen}", "note": note})
            riskiest.append((name, boosted))
            risk = max(risk, boosted)
    riskiest.sort(key=lambda t: t[1], reverse=True)
    confidence = 0.6 if details else base.confidence
    return RestaurantNutRisk(
        risk=clamp_risk(risk), confidence=confidence, rationale=rationale,
        labeling_trust=base.labeling_trust, riskiest_items=riskiest[:5], item_details=details,
    )
