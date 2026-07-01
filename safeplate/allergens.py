"""Canonical allergen + diet registry. The single source of truth that reconciles
the three allergen vocabularies in the codebase: the chart parser's space/singular
tokens (allergen_matrix._ALLERGEN_COLUMN_ALIASES, e.g. "tree nut"), the prior
layer's underscore-plural keys (allergen_prior, e.g. "tree_nuts"), and the term
substrings in menu_text.ALLERGEN_TERMS. Downstream code reads keys from here rather
than hardcoding. Nuts keep their existing super-family handling in allergen_prior /
allergen_score; this registry treats peanut and tree_nut as the two atomic nut keys."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AllergenSpec:
    key: str
    display: str
    matrix_tokens: frozenset[str]  # tokens the chart parser may emit for this allergen


@dataclass(frozen=True)
class DietSpec:
    key: str
    display: str
    excluded_allergens: frozenset[str]   # canonical allergen keys whose presence disqualifies
    excluded_categories: frozenset[str]  # non-allergen animal categories (need meat/animal KB)


# canonical key -> (display, matrix tokens, extra alias forms that canonicalize to it)
_DEFS: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    ("peanut",    "Peanut",    ("peanut",),    ("peanuts", "groundnut")),
    ("tree_nut",  "Tree nut",  ("tree nut",),  ("tree_nuts", "treenut", "tree nuts")),
    ("milk",      "Milk",      ("milk",),      ("dairy", "lactose")),
    ("egg",       "Egg",       ("egg",),       ("eggs",)),
    ("soy",       "Soy",       ("soy",),       ("soya", "soybean")),
    ("gluten",    "Gluten",    ("gluten",),    ("cereals",)),
    ("wheat",     "Wheat",     ("wheat",),     ()),
    ("fish",      "Fish",      ("fish",),      ()),
    ("shellfish", "Shellfish", ("shellfish",), ("crustacean", "crustaceans")),
    ("mollusc",   "Mollusc",   ("mollusc",),   ("mollusk", "molluscs")),
    ("sesame",    "Sesame",    ("sesame",),    ()),
    ("mustard",   "Mustard",   ("mustard",),   ()),
    ("celery",    "Celery",    ("celery",),    ()),
    ("sulphites", "Sulphites", ("sulphites",), ("sulphite", "sulfites", "sulfite")),
    ("lupin",     "Lupin",     ("lupin",),     ("lupine",)),
]

ALLERGENS: dict[str, AllergenSpec] = {
    key: AllergenSpec(key=key, display=display, matrix_tokens=frozenset(tokens))
    for key, display, tokens, _aliases in _DEFS
}

# every accepted surface form -> canonical key
_ALIAS_TO_KEY: dict[str, str] = {}
for _key, _display, _tokens, _aliases in _DEFS:
    for _form in (_key, *_tokens, *_aliases):
        _ALIAS_TO_KEY[_form.replace("_", " ").strip().lower()] = _key


DIETS: dict[str, DietSpec] = {
    "vegetarian": DietSpec(
        key="vegetarian", display="Vegetarian",
        excluded_allergens=frozenset({"fish", "shellfish", "mollusc"}),
        excluded_categories=frozenset({"meat", "poultry", "gelatin"}),
    ),
    "vegan": DietSpec(
        key="vegan", display="Vegan",
        excluded_allergens=frozenset({"milk", "egg", "fish", "shellfish", "mollusc"}),
        excluded_categories=frozenset({"meat", "poultry", "gelatin", "honey"}),
    ),
}


def canonical(token: str) -> str | None:
    if not token:
        return None
    return _ALIAS_TO_KEY.get(token.replace("_", " ").strip().lower())


def spec_for(key: str) -> AllergenSpec | None:
    return ALLERGENS.get(key)


def all_allergen_keys() -> tuple[str, ...]:
    return tuple(k for k, *_ in _DEFS)
