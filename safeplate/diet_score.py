"""Diet compatibility (vegetarian/vegan). A distinct concept from allergen RISK:
ingredient membership, no severity/cross-contact. Asymmetry: unknown/unlabeled
dishes are NOT assumed compatible; an empty/unknown menu yields 'unknown', never
'good_options'."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from safeplate.allergens import DIETS, canonical

_MEAT_KB_PATH = Path(__file__).resolve().parents[1] / "data" / "allergen_kb" / "meat_animal.json"


@lru_cache(maxsize=None)
def _meat_terms() -> dict[str, tuple[str, ...]]:
    if not _MEAT_KB_PATH.exists():
        return {}
    raw = json.loads(_MEAT_KB_PATH.read_text(encoding="utf-8"))
    return {cat: tuple(t.lower() for t in terms) for cat, terms in raw.items()}


@dataclass(frozen=True)
class DietAssessment:
    diet: str
    verdict: str            # not_compatible | limited | good_options | unknown
    support: float
    rationale: list[str] = field(default_factory=list)
    offending_items: list[str] = field(default_factory=list)
    compatible_items: list[str] = field(default_factory=list)


def _item_conflicts(spec, name_low: str, terms: list[str]) -> bool:
    if any(canonical(t) in spec.excluded_allergens for t in (terms or [])):
        return True
    meat = _meat_terms()
    for cat in spec.excluded_categories:
        if any(term in name_low for term in meat.get(cat, ())):
            return True
    return False


_VEGAN_LABELS = frozenset({"vegan", "plant-based", "plant based"})
_VEGETARIAN_LABELS = frozenset({"vegetarian", "vegan", "plant-based", "plant based"})

_DIET_POSITIVE_LABELS = {
    "vegan": _VEGAN_LABELS,
    "vegetarian": _VEGETARIAN_LABELS,
}


def _has_positive_label(diet: str, dietary_terms: list[str]) -> bool:
    labels = _DIET_POSITIVE_LABELS.get(diet)
    if not labels:
        return False
    return any(str(t).lower() in labels for t in dietary_terms)


def assess_diet(diet: str, *, menu_items: list, cuisines: list[str] | None = None) -> DietAssessment:
    spec = DIETS.get(diet)
    if spec is None:
        return DietAssessment(diet=diet, verdict="unknown", support=0.0,
                              rationale=[f"unknown diet {diet!r}"])
    items = menu_items or []
    if not items:
        return DietAssessment(diet=diet, verdict="unknown", support=0.0,
                              rationale=["no menu evidence"])
    offending, compatible = [], []
    unknown_count = 0
    for it in items:
        name = str(getattr(it, "item_name", "") or "")
        name_low = name.lower()
        terms = list(getattr(it, "allergen_terms", []) or [])
        dietary = [str(t).lower() for t in (getattr(it, "dietary_terms", []) or [])]
        conflict = _item_conflicts(spec, name_low, terms)
        if conflict:
            offending.append(name)
        elif _has_positive_label(diet, dietary):
            compatible.append(name)
        else:
            unknown_count += 1

    total = len(items)
    n_off = len(offending)
    n_ok = len(compatible)

    if n_ok == 0 and n_off == total:
        verdict = "not_compatible"
    elif n_ok == 0:
        verdict = "unknown"
    else:
        share = n_ok / total
        verdict = "good_options" if share >= 0.4 else "limited"

    support = round(n_ok / total, 2) if n_ok else 0.0

    if n_ok == 0 and n_off == total:
        rationale = [
            f"{n_off}/{total} menu items contain excluded ingredients for {spec.display.lower()}"
        ]
    elif n_ok == 0:
        rationale = [
            f"0/{total} menu items carry a positive {spec.display.lower()} label "
            "(allergen data alone can't confirm compatibility)"
        ]
        if n_off:
            rationale.append(f"{n_off} contain excluded ingredients (e.g. {offending[0]})")
        if unknown_count:
            rationale.append(f"{unknown_count} item(s) have no diet info")
    else:
        rationale = [
            f"{n_ok}/{total} menu items are labeled {spec.display.lower()}-compatible"
        ]
        if n_off:
            rationale.append(f"{n_off} contain excluded ingredients (e.g. {offending[0]})")
        if unknown_count:
            rationale.append(f"{unknown_count} item(s) have no diet info")

    return DietAssessment(diet=diet, verdict=verdict, support=support,
                          rationale=rationale, offending_items=offending[:10],
                          compatible_items=compatible[:10])


def assess_diets(diets, *, menu_items, cuisines=None) -> list[DietAssessment]:
    return [assess_diet(d, menu_items=menu_items, cuisines=cuisines) for d in sorted(diets)]
