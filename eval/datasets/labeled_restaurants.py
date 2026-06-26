"""Labeled benchmark for calibrating + safety-testing the scorer.

WHAT THIS IS: a curated set of restaurant cases, each with a GROUND-TRUTH label
(``pos`` = a nut-allergic user would actually encounter nuts here; ``neg`` =
genuinely nut-safe) plus the evidence the scorer would see. Labels are seeded from
publicly-known cuisine norms and allergen practices, NOT a live audit -- they exist
so we can (a) measure the FALSE-NEGATIVE rate (the dangerous direction) and (b)
sanity-check whether the hand-picked cuisine baselines RANK cuisines the way real
prevalence does. Replace/augment these with live-labeled real restaurants over time;
the shape is intentionally simple so adding rows is trivial.

Each case is a dict:
    name, cuisines, region, truth ("pos"|"neg"),
    [menu_items], [signals], [community], [note]

Cases with no ``menu_items``/``signals`` are CUISINE-ONLY (no menu found) -- these
are the ones that drive cuisine-baseline calibration, since nothing restaurant-
specific enters the score.
"""

from __future__ import annotations

from safeplate.allergen_score import CommunitySignal, RestaurantSignals


# A real allergen matrix tracks nuts as a column, so a clean chart can vouch for
# nut-absence. Declare nut columns on matrix-method items so the labeled cases mirror
# real charts (the scorer only credits a clean chart that actually has a nut column).
_MATRIX_COLUMNS = ("peanut", "tree nut", "milk", "egg", "soy", "gluten")


def _item(name, *, allergen_terms=None, method="gemini_text", description=""):
    item = {"item_name": name, "description": description,
            "allergen_terms": allergen_terms or [], "extraction_method": method}
    if "matrix" in method:
        item["matrix_allergen_columns"] = _MATRIX_COLUMNS
    return item


def _sig(**kw):
    return RestaurantSignals(**kw)


# --------------------------------------------------------------------------- #
# GROUNDED-EVIDENCE cases: the label is confirmed by the evidence in the case.
# --------------------------------------------------------------------------- #
_GROUNDED = [
    dict(name="Satay House (chart)", cuisines=["thai"], region="US", truth="pos",
         menu_items=[_item("Chicken Satay", allergen_terms=["peanut"], method="allergen_matrix"),
                     _item("Steamed Rice", method="allergen_matrix")],
         note="allergen chart confirms peanut"),
    dict(name="Trattoria (pesto text)", cuisines=["italian"], region="US", truth="pos",
         menu_items=[_item("Pesto Genovese", allergen_terms=["pine nut", "walnut"])],
         note="menu text names nuts"),
    dict(name="Five Guys-style burgers", cuisines=["american"], region="US", truth="pos",
         menu_items=[_item("Cheeseburger", allergen_terms=["peanut"], method="allergen_matrix")],
         note="free peanuts / peanut oil in the kitchen"),
    dict(name="Wagamama-style", cuisines=["japanese", "asian"], region="GB", truth="pos",
         menu_items=[_item("Pad Thai", allergen_terms=["peanut"], method="allergen_matrix"),
                     _item("Chicken Katsu", method="allergen_matrix"),
                     _item("Edamame", method="allergen_matrix"),
                     _item("Miso Soup", method="allergen_matrix"),
                     _item("Steamed Gyoza", method="allergen_matrix")],
         note="UK chart: nuts in some dishes, many safe -> navigable"),
    dict(name="Clean-chart American grill", cuisines=["american"], region="US", truth="neg",
         menu_items=[_item("Burger", allergen_terms=["milk", "egg"], method="allergen_matrix"),
                     _item("Fries", method="allergen_matrix")],
         note="full chart, marks no nuts"),
    dict(name="Dedicated nut-free bakery", cuisines=["bakery"], region="US", truth="neg",
         signals=_sig(nut_free_claim=True, allergy_disclaimer=True),
         note="verified nut-free + disclaimer"),
    dict(name="Allergy-aware cafe (claim)", cuisines=["cafe"], region="US", truth="neg",
         signals=_sig(nut_free_claim=True), note="states a nut-free claim"),
    dict(name="Vegan kitchen (cashew cheese)", cuisines=["vegan"], region="US", truth="pos",
         menu_items=[_item("Cashew Cheese Pizza"), _item("Garden Salad"),
                     _item("Almond-milk Latte")],
         note="the 'vegan = safe' trap"),
    dict(name="Community adverse report", cuisines=["chinese"], region="US", truth="pos",
         community=[CommunitySignal(type="adverse_event", allergen="nuts",
                                    quote="had a peanut reaction here", age_days=40)],
         note="anecdotal but allergen-specific"),
    dict(name="Cross-contact warning bakery", cuisines=["bakery"], region="US", truth="pos",
         signals=_sig(cross_contact_warning=True),
         menu_items=[_item("Sourdough"), _item("Baguette")],
         note="'may contain nuts' shared kitchen"),
]


# --------------------------------------------------------------------------- #
# CUISINE-ONLY cases (no menu found). The label reflects whether a typical
# restaurant of this cuisine would expose a nut-allergic diner to nuts. Multiple
# per cuisine so empirical prevalence can be estimated. SEEDED FROM CUISINE NORMS.
# --------------------------------------------------------------------------- #
def _co(cuisine, region, truth, n, *, extra=None):
    cuisines = [cuisine] + (extra or [])
    return [dict(name=f"{cuisine}-only #{i+1}", cuisines=cuisines, region=region,
                 truth=truth, note="cuisine-only (no menu)") for i in range(n)]


_CUISINE_ONLY = (
    # High-nut cuisines: nuts are near-ubiquitous (peanut, cashew, almond).
    _co("thai", "US", "pos", 4)
    + _co("indonesian", "US", "pos", 2)
    + _co("vietnamese", "US", "pos", 2)
    + _co("indian", "US", "pos", 3) + _co("indian", "US", "neg", 1)
    + _co("middle_eastern", "US", "pos", 3)
    + _co("vegan", "US", "pos", 3) + _co("vegan", "US", "neg", 1)
    + _co("bakery", "US", "pos", 2) + _co("bakery", "US", "neg", 1)
    + _co("georgian", "US", "pos", 2)
    # Mid cuisines: nuts in some dishes, avoidable in many.
    + _co("chinese", "US", "pos", 2) + _co("chinese", "US", "neg", 2)
    + _co("italian", "US", "pos", 2) + _co("italian", "US", "neg", 2)
    + _co("mediterranean", "US", "pos", 2) + _co("mediterranean", "US", "neg", 1)
    + _co("french", "US", "pos", 1) + _co("french", "US", "neg", 2)
    # Low-nut cuisines: a nut-allergic diner is usually fine.
    + _co("japanese", "US", "neg", 4) + _co("japanese", "US", "pos", 1)
    + _co("american", "US", "neg", 3) + _co("american", "US", "pos", 1)
    + _co("korean", "US", "neg", 2) + _co("korean", "US", "pos", 1)
    + _co("mexican", "US", "neg", 2) + _co("mexican", "US", "pos", 1)
    + _co("bbq", "US", "neg", 2)
    + _co("seafood", "US", "neg", 2)
    # Region check: same low-nut cuisine under an EU allergen mandate.
    + _co("japanese", "GB", "neg", 2)
)


LABELED = _GROUNDED + _CUISINE_ONLY


def positives():
    return [c for c in LABELED if c["truth"] == "pos"]


def negatives():
    return [c for c in LABELED if c["truth"] == "neg"]


def score_kwargs(case):
    """Extract just the kwargs the scorers accept from a labeled case."""
    return {k: case[k] for k in ("cuisines", "region", "menu_items", "signals", "community")
            if k in case}
