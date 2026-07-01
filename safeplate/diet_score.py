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
    for it in items:
        name = str(getattr(it, "item_name", "") or "")
        terms = list(getattr(it, "allergen_terms", []) or [])
        if _item_conflicts(spec, name.lower(), terms):
            offending.append(name)
        else:
            compatible.append(name)
    share = len(compatible) / len(items)
    if not compatible:
        verdict = "not_compatible"
    elif share >= 0.4:
        verdict = "good_options"
    else:
        verdict = "limited"
    rationale = [f"{len(compatible)}/{len(items)} menu items appear {spec.display.lower()}-compatible"]
    if offending:
        rationale.append(f"{len(offending)} contain excluded ingredients (e.g. {offending[0]})")
    return DietAssessment(diet=diet, verdict=verdict, support=round(share, 2),
                          rationale=rationale, offending_items=offending[:10],
                          compatible_items=compatible[:10])


def assess_diets(diets, *, menu_items, cuisines=None) -> list[DietAssessment]:
    return [assess_diet(d, menu_items=menu_items, cuisines=cuisines) for d in sorted(diets)]
