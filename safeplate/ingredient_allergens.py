"""Ingredient -> allergen inference: the implicit-ingredient layer.

A literal allergen WORD ("peanut", "almond", "sesame") is already grounded by
``menu_text.ALLERGEN_TERMS``. This module covers the harder, more common case on
real menus: an ingredient or dish name that IMPLIES an allergen without naming it
-- ``tahini`` -> sesame, ``paneer`` -> milk, ``miso`` -> soy, ``calamari`` ->
mollusc -- across cuisines.

``infer_allergens(text)`` returns ``(definite, may_contain)``:

* ``definite``   -- the named thing near-certainly CONTAINS the allergen
  (tahini IS sesame paste). Folds into a record's ``allergen_terms`` and is
  treated as confirmed presence by the scorer, exactly like a literal word.
* ``may_contain`` -- the named thing OFTEN but not always contains it
  (pesto usually has pine nuts, sometimes walnut/cashew; satay is usually
  peanut). Folds into ``cross_contact_terms`` -- the softer "may have" channel --
  so it raises risk without falsely asserting presence.

Design stance (mirrors the existing ALLERGEN_TERMS philosophy): only >=~0.95-certain
ingredients are ``definite``; merely-"usually" ones stay ``may_contain``. Accuracy
matters -- word-boundary matching plus per-rule exclusions keep near-miss words
(eggplant, coconut milk, butternut, oyster mushroom, water chestnut, buckwheat)
from firing, and an explicit free-from qualifier ("gluten-free", "vegan",
"dairy-free") suppresses the matching allergen.

Nut tokens are emitted as the scorer's canonical labels (``peanut``, ``tree nut``,
or a specific nut) so the inference drives the nut score; non-nut tokens are
surfaced for the UI and the forthcoming multi-allergen scorer.
"""

from __future__ import annotations

import re

# Canonical nut tokens the scorer recognizes (allergen_score._PEANUT_TERMS /
# _TREE_NUT_TERMS). Specific nuts are emitted where the ingredient pins one down
# (marzipan -> almond), otherwise the family token "tree nut".
_ALL_NUT_TOKENS = {
    "peanut", "tree nut", "almond", "walnut", "cashew", "hazelnut",
    "pistachio", "pecan", "pine nut", "macadamia", "brazil nut",
}

# (variants, tokens, tier, exclusions). `tier` is "def" (definite -> allergen_terms)
# or "may" (often -> cross_contact_terms). `exclusions` are phrases blanked out of a
# LOCAL copy of the text before this rule matches, so a near-miss compound
# ("coconut milk", "oyster mushroom") cannot trigger the base ingredient.
_SPEC: list[tuple[list[str], list[str], str, list[str]]] = [
    # ---------------- Sesame ----------------
    (["tahini", "tahina", "tahin"], ["sesame"], "def", []),
    (["za'atar", "zaatar", "za atar"], ["sesame"], "def", []),
    (["halva", "halvah", "halwa"], ["sesame"], "def", []),
    (["gomashio", "gomasio"], ["sesame"], "def", []),
    (["benne"], ["sesame"], "def", []),
    (["furikake"], ["sesame"], "def", []),
    (["hummus", "houmous", "hommus"], ["sesame"], "may", []),  # usually tahini, not always
    (["baba ganoush", "babaganoush", "baba ghanoush"], ["sesame"], "may", []),

    # ---------------- Milk / dairy ----------------
    (["paneer"], ["milk"], "def", []),
    (["ghee"], ["milk"], "def", []),
    (["labneh", "labne", "labane"], ["milk"], "def", []),
    (["alfredo"], ["milk"], "def", []),
    (["bechamel", "béchamel"], ["milk"], "def", []),
    (["au gratin", "gratin"], ["milk"], "def", []),
    (["queso", "crema"], ["milk"], "def", []),
    (["mozzarella", "parmesan", "parmigiano", "ricotta", "mascarpone",
      "burrata", "cheddar", "gouda", "brie", "feta", "halloumi",
      "provolone", "gorgonzola", "camembert"], ["milk"], "def", []),
    (["custard", "creme brulee", "crème brûlée", "panna cotta", "gelato",
      "kulfi"], ["milk"], "may", []),  # kulfi/gelato dairy; also nutty -> see nuts
    (["malai", "khoya", "raita", "lassi", "kheer"], ["milk"], "def", []),
    (["cheese"], ["milk"], "def", ["vegan cheese", "nut cheese"]),
    (["yogurt", "yoghurt", "yoghourt"], ["milk"], "def",
     ["coconut yogurt", "soy yogurt", "almond yogurt", "oat yogurt"]),
    (["butter"], ["milk"], "def",
     ["peanut butter", "almond butter", "cashew butter", "hazelnut butter",
      "sunflower butter", "seed butter", "nut butter", "cocoa butter",
      "shea butter", "apple butter", "fruit butter", "body butter",
      "butter lettuce", "butter bean"]),
    (["cream"], ["milk"], "def",
     ["coconut cream", "cashew cream", "almond cream", "oat cream",
      "soy cream", "cream of tartar", "ice cream cone"]),
    (["buttermilk", "condensed milk", "evaporated milk", "whey", "casein",
      "milk"], ["milk"], "def",
     ["coconut milk", "almond milk", "soy milk", "soya milk", "oat milk",
      "rice milk", "cashew milk", "hemp milk", "pea milk", "macadamia milk",
      "flax milk", "hazelnut milk", "walnut milk", "peanut milk",
      "milk thistle"]),

    # ---------------- Egg ----------------
    (["mayonnaise", "mayo", "aioli", "aïoli"], ["egg"], "def",
     ["vegan mayo", "vegan mayonnaise", "vegan aioli"]),
    (["hollandaise", "bearnaise", "béarnaise"], ["egg"], "def", []),
    (["meringue", "pavlova", "zabaglione", "financier"], ["egg"], "def", []),
    (["carbonara"], ["egg"], "def", []),
    (["frittata", "omelet", "omelette", "quiche", "shakshuka",
      "tamagoyaki", "chawanmushi"], ["egg"], "def", []),
    (["egg"], ["egg"], "def", ["egg plant", "eggfruit"]),
    (["brioche", "challah", "custard"], ["egg"], "may", []),

    # ---------------- Soy ----------------
    (["tofu", "bean curd"], ["soy"], "def", []),
    (["edamame"], ["soy"], "def", []),
    (["tempeh"], ["soy"], "def", []),
    (["miso"], ["soy"], "def", []),
    (["natto"], ["soy"], "def", []),
    (["doenjang", "gochujang"], ["soy"], "def", []),
    (["tamari"], ["soy"], "def", []),  # soy, but GF -> no wheat
    (["soy sauce", "soya sauce", "shoyu"], ["soy", "wheat", "gluten"], "def", []),
    (["teriyaki", "hoisin", "ponzu"], ["soy"], "def", []),
    (["edamame", "soybean", "soya bean"], ["soy"], "def", []),

    # ---------------- Wheat / gluten ----------------
    (["seitan"], ["wheat", "gluten"], "def", []),
    (["panko", "breadcrumb", "bread crumb"], ["wheat", "gluten"], "def", []),
    (["tempura", "katsu", "schnitzel", "milanese", "breaded"],
     ["wheat", "gluten"], "def", []),
    (["udon", "ramen", "somen", "lo mein", "chow mein", "spaetzle"],
     ["wheat", "gluten"], "def", []),
    (["naan", "roti", "paratha", "chapati", "pita", "focaccia", "ciabatta",
      "couscous", "bulgur", "semolina", "farro", "orzo"],
     ["wheat", "gluten"], "def", []),
    (["barley", "rye", "malt", "spelt", "kamut", "farina"], ["gluten"], "def",
     ["malt vinegar"]),
    (["gnocchi", "pierogi", "wonton", "dumpling", "pretzel", "crouton"],
     ["wheat", "gluten"], "may", []),

    # ---------------- Fish ----------------
    (["anchovy", "anchovies"], ["fish"], "def", []),
    (["fish sauce", "nuoc mam", "nam pla", "patis", "colatura"], ["fish"], "def", []),
    (["bonito", "katsuobushi", "dashi"], ["fish"], "def", ["kombu dashi"]),
    (["surimi"], ["fish"], "def", []),
    (["tuna", "salmon", "cod", "halibut", "tilapia", "mackerel", "sardine",
      "herring", "trout", "snapper", "branzino", "sea bass", "mahi",
      "swordfish", "haddock", "pollock", "catfish", "barramundi"],
     ["fish"], "def", []),
    (["caviar", "roe", "ikura", "tobiko", "masago", "taramasalata",
      "lox", "gravlax"], ["fish"], "def", []),
    (["worcestershire", "caesar"], ["fish"], "may",
     ["vegan caesar", "vegan worcestershire"]),

    # ---------------- Crustacean shellfish ----------------
    (["shrimp", "prawn"], ["shellfish"], "def", []),
    (["lobster", "crab", "crawfish", "crayfish", "langoustine", "scampi",
      "krill"], ["shellfish"], "def", ["crab apple", "crabapple"]),
    (["shrimp paste", "belacan", "terasi", "bagoong", "xo sauce"],
     ["shellfish"], "def", []),

    # ---------------- Mollusc ----------------
    (["mussel", "clam", "scallop", "squid", "calamari", "octopus",
      "cuttlefish", "escargot", "snail", "abalone", "whelk", "cockle",
      "periwinkle", "geoduck"], ["mollusc"], "def", []),
    (["oyster"], ["mollusc"], "def", ["oyster mushroom"]),

    # ---------------- Mustard ----------------
    (["mustard", "dijon", "colman"], ["mustard"], "def", ["mustard green"]),

    # ---------------- Celery ----------------
    (["celery", "celeriac", "celery salt"], ["celery"], "def", []),

    # ---------------- Lupin ----------------
    (["lupin", "lupini", "lupine"], ["lupin"], "def", []),

    # ---------------- Peanut ----------------
    (["groundnut", "goober"], ["peanut"], "def", ["groundnut-free"]),
    (["gado gado", "gado-gado", "gadogado"], ["peanut"], "def", []),
    (["peanut sauce", "satay sauce", "peanut oil", "groundnut oil"],
     ["peanut"], "def", []),
    (["satay", "sate"], ["peanut"], "may", []),
    (["pad thai", "phad thai"], ["peanut"], "may", []),
    (["kung pao", "gong bao", "kung po"], ["peanut"], "may", []),
    (["massaman"], ["peanut"], "may", []),

    # ---------------- Tree nut ----------------
    (["baklava", "baklawa"], ["tree nut"], "def", []),
    (["marzipan"], ["almond"], "def", []),
    (["frangipane"], ["almond"], "def", []),
    (["amaretto", "amaretti"], ["almond"], "def", []),
    (["gianduja", "gianduia"], ["hazelnut"], "def", []),
    (["nutella", "ferrero"], ["hazelnut"], "def", []),
    (["praline", "pralines"], ["tree nut"], "may", []),
    (["nougat", "turron", "turrón"], ["tree nut"], "may", []),
    (["pesto"], ["tree nut"], "may", []),  # pine nut usually; walnut/cashew sometimes
    (["korma", "pasanda"], ["tree nut"], "may", []),
    (["kulfi"], ["tree nut"], "may", []),
    (["romesco"], ["tree nut"], "may", []),
]


def _compile(variants: list[str]) -> list[re.Pattern]:
    # Whole-word, allowing a trailing plural 's'. Lookarounds (not \b) so hyphens
    # and apostrophes at edges behave; the trailing 's?' is bounded by the same
    # no-letter lookahead so "egg" never matches inside "eggplant".
    return [re.compile(r"(?<![a-z])" + re.escape(v) + r"s?(?![a-z])") for v in variants]


_RULES = [(_compile(variants), tokens, tier, exclusions)
          for variants, tokens, tier, exclusions in _SPEC]

# Free-from qualifiers: presence suppresses the listed tokens (an over-warn the menu
# explicitly rules out). "vegan" rules out the animal-derived allergens.
_NUT_TOKENS_TUPLE = tuple(_ALL_NUT_TOKENS - {"peanut"})
_FREE_FROM: list[tuple[list[str], set[str]]] = [
    (["dairy free", "dairy-free", "non dairy", "non-dairy"], {"milk"}),
    (["gluten free", "gluten-free"], {"wheat", "gluten"}),
    (["wheat free", "wheat-free"], {"wheat"}),
    (["soy free", "soy-free", "soya free", "soya-free"], {"soy"}),
    (["sesame free", "sesame-free"], {"sesame"}),
    (["egg free", "egg-free", "eggless"], {"egg"}),
    (["peanut free", "peanut-free"], {"peanut"}),
    (["tree nut free", "tree-nut-free", "treenut free"], set(_NUT_TOKENS_TUPLE)),
    (["nut free", "nut-free"], {"peanut", *_NUT_TOKENS_TUPLE}),
    (["shellfish free", "shellfish-free"], {"shellfish", "mollusc"}),
    (["fish free", "fish-free"], {"fish"}),
    (["vegan", "plant based", "plant-based"], {"milk", "egg", "fish", "shellfish", "mollusc"}),
]


_FREE_FROM_RULES = [(_compile(phrases), tokens) for phrases, tokens in _FREE_FROM]


def _suppressed(text: str) -> set[str]:
    # Word-boundary matched so "peanut-free" (which contains "nut-free") suppresses
    # only peanut, never the tree nuts -- a substring match there would hide a real
    # tree-nut allergen, the dangerous direction.
    out: set[str] = set()
    for patterns, tokens in _FREE_FROM_RULES:
        if any(p.search(text) for p in patterns):
            out |= tokens
    return out


def infer_allergens(text: str) -> tuple[list[str], list[str]]:
    """Infer allergens implied by ingredient/dish names in ``text``.

    Returns ``(definite, may_contain)`` as sorted lists of canonical allergen
    tokens. A token is never in both; an explicit free-from qualifier drops it.
    """
    if not text or not text.strip():
        return [], []
    norm = " " + text.lower() + " "
    suppressed = _suppressed(norm)

    definite: set[str] = set()
    maybe: set[str] = set()
    for patterns, tokens, tier, exclusions in _RULES:
        local = norm
        for ex in exclusions:
            if ex in local:
                local = local.replace(ex, "  ")
        if any(p.search(local) for p in patterns):
            (definite if tier == "def" else maybe).update(tokens)

    definite -= suppressed
    maybe -= suppressed
    maybe -= definite  # a confirmed allergen need not also be listed as may-contain
    return sorted(definite), sorted(maybe)
