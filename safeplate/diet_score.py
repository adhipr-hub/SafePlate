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
    basis: str = "none"     # labeled | ai_assessed | estimated | mixed | none
    rationale: list[str] = field(default_factory=list)
    offending_items: list[str] = field(default_factory=list)
    compatible_items: list[str] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)   # [{"quote","url","source"}]


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


def _classify_item_floor(spec, diet: str, name_low: str, terms, dietary) -> str:
    """Return 'conflict' | 'labeled' | 'assumed'. (Floor never returns 'unknown':
    a non-conflict, unlabeled dish is ASSUMED compatible.)"""
    if _item_conflicts(spec, name_low, list(terms or [])):
        return "conflict"
    if _has_positive_label(diet, dietary):
        return "labeled"
    return "assumed"


_GOOD_SHARE = 0.4


def assess_diet(diet, *, menu_items, cuisines=None,
                llm_judgments=None, accommodation_signals=None) -> DietAssessment:
    spec = DIETS.get(diet)
    if spec is None:
        return DietAssessment(diet=diet, verdict="unknown", support=0.0,
                              rationale=[f"unknown diet {diet!r}"])
    items = menu_items or []
    if not items:
        return DietAssessment(diet=diet, verdict="unknown", support=0.0,
                              rationale=["no menu evidence"])

    labeled, assumed, offending = [], [], []
    for it in items:
        name = str(getattr(it, "item_name", "") or "")
        terms = getattr(it, "allergen_terms", []) or []
        dietary = [str(t).lower() for t in (getattr(it, "dietary_terms", []) or [])]
        kind = _classify_item_floor(spec, diet, name.lower(), terms, dietary)
        if kind == "conflict":
            offending.append(name)
        elif kind == "labeled":
            labeled.append(name)
        else:
            assumed.append(name)

    total = len(items)
    compatible = labeled + assumed
    n_ok, n_off = len(compatible), len(offending)

    if n_ok == 0:
        verdict, basis = ("not_compatible", "none") if n_off == total else ("unknown", "none")
    else:
        share = n_ok / total
        verdict = "good_options" if share >= _GOOD_SHARE else "limited"
        if labeled and assumed:
            basis = "mixed"
        elif labeled:
            basis = "labeled"
        else:
            basis = "estimated"
        # Vegan cap: name-only ('estimated') compatibility can't see hidden dairy/egg.
        if diet == "vegan" and basis == "estimated" and verdict == "good_options":
            verdict = "limited"

    support = round(n_ok / total, 2) if n_ok else 0.0
    rationale = _floor_rationale(spec, diet, basis, len(labeled), len(assumed), offending, total)
    return DietAssessment(diet=diet, verdict=verdict, support=support, basis=basis,
                          rationale=rationale, offending_items=offending[:10],
                          compatible_items=compatible[:10])


def _floor_rationale(spec, diet, basis, n_lab, n_asm, offending, total):
    d = spec.display.lower()
    out = []
    if basis == "labeled":
        out.append(f"{n_lab}/{total} menu items are labeled {d}-compatible")
    elif basis == "mixed":
        out.append(f"{n_lab}/{total} labeled {d}-compatible, {n_asm} estimated from dish names")
    elif basis == "estimated":
        out.append(f"{n_asm}/{total} menu items look {d}-compatible, estimated from dish names (not confirmed)")
    elif offending and len(offending) == total:
        out.append(f"all {total} menu items contain ingredients excluded from {d}")
    if offending and (basis not in ("none",) or len(offending) != total):
        out.append(f"{len(offending)} contain excluded ingredients (e.g. {offending[0]})")
    return out


def assess_diets(diets, *, menu_items, cuisines=None) -> list[DietAssessment]:
    return [assess_diet(d, menu_items=menu_items, cuisines=cuisines) for d in sorted(diets)]
