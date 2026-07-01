"""Diet compatibility (vegetarian/vegan). A distinct concept from allergen RISK:
ingredient membership, no severity/cross-contact. A non-conflict, unlabeled dish is
ASSUMED compatible from its name, tracked by a provenance basis (labeled >
ai_assessed > estimated); estimates are always marked 'estimated', never presented
as confirmed. Asymmetry preserved where it matters: an empty menu yields 'unknown'
(never 'good_options'), and a vegan verdict resting only on name-based 'estimated'
evidence is capped at 'limited' -- a dish name can't reveal hidden dairy/egg."""

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

    judgments = llm_judgments or {}
    labeled, assumed, ai_ok, offending = [], [], [], []
    for it in items:
        name = str(getattr(it, "item_name", "") or "")
        terms = getattr(it, "allergen_terms", []) or []
        dietary = [str(t).lower() for t in (getattr(it, "dietary_terms", []) or [])]
        j = judgments.get(name.lower())
        if j is not None and j.verdict == "no":
            offending.append(name); continue
        if j is not None and j.verdict == "yes":
            ai_ok.append(name); continue
        kind = _classify_item_floor(spec, diet, name.lower(), terms, dietary)
        if kind == "conflict":
            offending.append(name)
        elif kind == "labeled":
            labeled.append(name)
        else:
            assumed.append(name)

    total = len(items)
    compatible = labeled + ai_ok + assumed
    n_ok, n_off = len(compatible), len(offending)

    if n_ok == 0:
        verdict, basis = ("not_compatible", "none") if n_off == total else ("unknown", "none")
    else:
        share = n_ok / total
        verdict = "good_options" if share >= _GOOD_SHARE else "limited"
        if ai_ok and not (labeled or assumed):
            basis = "ai_assessed"
        elif ai_ok:
            basis = "mixed"
        elif labeled and assumed:
            basis = "mixed"
        elif labeled:
            basis = "labeled"
        else:
            basis = "estimated"
        # Vegan cap: a dish name can't reveal hidden dairy/egg, so a vegan
        # 'good_options' must be carried by CONFIDENT evidence (labeled + AI-judged)
        # on its own. Otherwise name-only 'estimated' items sneaking in via a 'mixed'
        # basis (one AI-yes + many assumed) would bypass the cap. When estimates are
        # what push it over the bar, cap to 'limited'.
        if diet == "vegan" and verdict == "good_options" and assumed:
            if (len(labeled) + len(ai_ok)) / total < _GOOD_SHARE:
                verdict = "limited"

    support = round(n_ok / total, 2) if n_ok else 0.0
    rationale = _floor_rationale(spec, diet, basis, len(labeled), len(assumed), offending, total)
    if ai_ok:
        rationale.insert(0, f"{len(ai_ok)}/{total} judged {spec.display.lower()}-compatible by AI menu analysis")
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
