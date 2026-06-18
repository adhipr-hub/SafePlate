"""Worldwide robustness eval for the allergen prior / normalization layer.

Measures where the system *silently makes assumptions* (falls back to a default
instead of recognizing real signal) across global cuisines, address formats, and
dishes. Deterministic and free, so it can be re-run every iteration to record
performance deltas. Run: python scripts/eval_worldwide.py
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from safeplate.allergen_prior import (  # noqa: E402
    CUISINE_NUT_BASELINE,
    DEFAULT_CUISINE_BASELINE,
    normalize_cuisine,
    region_from_address,
    score_menu_item_prior,
)

# --- World cuisines as providers actually emit them (OSM cuisine:x / Google x_restaurant) ---
CUISINE_TESTS = [
    "cuisine:thai", "cuisine:vietnamese", "cuisine:indian", "cuisine:pakistani",
    "cuisine:bangladeshi", "cuisine:sri_lankan", "cuisine:nepalese", "cuisine:afghan",
    "cuisine:persian", "cuisine:lebanese", "cuisine:turkish", "cuisine:syrian",
    "cuisine:israeli", "cuisine:egyptian", "cuisine:moroccan", "cuisine:ethiopian",
    "cuisine:nigerian", "cuisine:ghanaian", "cuisine:senegalese", "cuisine:kenyan",
    "cuisine:south_african", "cuisine:chinese", "cuisine:sichuan", "cuisine:cantonese",
    "cuisine:taiwanese", "cuisine:korean", "cuisine:japanese", "cuisine:filipino",
    "cuisine:indonesian", "cuisine:malaysian", "cuisine:singaporean", "cuisine:burmese",
    "cuisine:cambodian", "cuisine:laotian", "cuisine:mexican", "cuisine:peruvian",
    "cuisine:brazilian", "cuisine:argentinian", "cuisine:colombian", "cuisine:cuban",
    "cuisine:caribbean", "cuisine:jamaican", "cuisine:italian", "cuisine:french",
    "cuisine:spanish", "cuisine:greek", "cuisine:portuguese", "cuisine:german",
    "cuisine:polish", "cuisine:russian", "cuisine:georgian", "cuisine:ukrainian",
    "cuisine:american", "cuisine:hawaiian", "cuisine:british", "cuisine:uzbek",
    "cuisine:mongolian", "cuisine:soul_food", "indian_restaurant", "sushi_restaurant",
]

# --- Address formats from around the world (address, expected ISO country) ---
REGION_TESTS = [
    ("1 Infinite Loop, Cupertino, CA 95014, USA", "US"),
    ("221B Baker Street, London NW1 6XE, United Kingdom", "GB"),
    ("MG Road, Bengaluru, Karnataka 560001, India", "IN"),
    ("1 Chome-1 Yurakucho, Chiyoda City, Tokyo, Japan", "JP"),
    ("Hamra Street, Beirut, Lebanon", "LB"),
    ("Av. Paulista, São Paulo, Brazil", "BR"),
    ("Calle Madero, Centro, Mexico City, Mexico", "MX"),
    ("Sukhumvit Rd, Bangkok 10110, Thailand", "TH"),
    ("Victoria Island, Lagos, Nigeria", "NG"),
    ("Champs-Élysées, 75008 Paris, France", "FR"),
    ("Friedrichstraße, 10117 Berlin, Germany", "DE"),
    ("Plaza Mayor, 28012 Madrid, Spain", "ES"),
    ("Nanjing Road, Shanghai, China", "CN"),
    ("Myeongdong, Jung-gu, Seoul, South Korea", "KR"),
    ("Connaught Place, New Delhi, India", "IN"),
    ("Jalan Alor, Kuala Lumpur, Malaysia", "MY"),
    ("Orchard Road, Singapore", "SG"),
    ("Plaza de Armas, Lima, Peru", "PE"),
    ("Tahrir Square, Cairo, Egypt", "EG"),
    ("Jemaa el-Fnaa, Marrakesh, Morocco", "MA"),
    ("George Street, Sydney NSW 2000, Australia", "AU"),
    ("Queen Street, Auckland, New Zealand", "NZ"),
    ("Yonge Street, Toronto, ON, Canada", "CA"),
    ("Nevsky Prospect, St Petersburg, Russia", "RU"),
    ("Rustaveli Avenue, Tbilisi, Georgia", "GE"),
    ("Khreshchatyk, Kyiv, Ukraine", "UA"),
    ("Roma, Italy", "IT"),
    ("Damrak, 1012 Amsterdam, Netherlands", "NL"),
    ("Ben Yehuda St, Tel Aviv, Israel", "IL"),
    ("Jl. Malioboro, Yogyakarta, Indonesia", "ID"),
    ("Pham Ngu Lao, District 1, Ho Chi Minh City, Vietnam", "VN"),
    ("5175 Moorpark Ave, San Jose, CA", "US"),
    ("Grafton Street, Dublin, Ireland", "IE"),
    ("La Rambla, Barcelona, Spain", "ES"),
    ("Strøget, Copenhagen, Denmark", "DK"),
]

# --- Dishes with ground-truth nut status (recall + precision across cuisines) ---
NUT_DISHES = [
    "Pad Thai", "Chicken Satay", "Beef Massaman Curry", "Gado-Gado", "Kung Pao Chicken",
    "Chicken Korma", "Baklava", "Muhammara", "Romesco Chicken", "Dukkah-Crusted Salmon",
    "Pesto Genovese", "Bakewell Tart", "Waldorf Salad", "Almond Croissant",
    "Pecan Pie", "Cashew Chicken", "Walnut Brownie", "Pistachio Gelato",
    "Nutella Crepe", "Mole Poblano", "Groundnut Stew", "Pine Nut Couscous",
]
NUT_FREE_DISHES = [
    "Cheeseburger", "Margherita Pizza", "Caesar Salad", "Miso Soup", "Grilled Salmon",
    "Fish and Chips", "Steamed Jasmine Rice", "French Onion Soup", "Beef Tacos",
    "Tomato Bruschetta", "Spaghetti Carbonara", "Chicken Tikka Masala", "Pork Dumplings",
    "Greek Salad", "Vegetable Spring Rolls",
    # Adversarial: words that *contain or resemble* nut terms but are nut-free.
    "Coconut Curry", "Butternut Squash Soup", "Water Chestnut Stir-Fry",
    "Nutmeg Custard", "Doughnut Holes",
]
# Native-script names (stress non-Latin handling)
NATIVE_NUT_DISHES = [
    "バクラヴァ", "팟타이", "馬薩曼咖喱", "花生鶏 (peanut chicken)",
    "Pollo con Almendras", "Gâteau aux Amandes", "땅콩 소스 국수", "Тарт с фундуком",
]


def pct(num, den):
    return f"{(num / den * 100):4.0f}%" if den else "  n/a"


def main() -> None:
    print("=" * 64)
    print("WORLDWIDE ROBUSTNESS EVAL")
    print("=" * 64)

    # 1) Cuisine recognition (silent-default detection)
    recognized = specific = 0
    unrecognized = []
    for raw in CUISINE_TESTS:
        cz = normalize_cuisine([raw])
        if cz:
            recognized += 1
            if any(CUISINE_NUT_BASELINE.get(c, DEFAULT_CUISINE_BASELINE) != DEFAULT_CUISINE_BASELINE for c in cz):
                specific += 1
        else:
            unrecognized.append(raw.split(":")[-1])
    n = len(CUISINE_TESTS)
    print(f"\n[Cuisine] recognized {recognized}/{n} ({pct(recognized,n)}), "
          f"specific baseline {specific}/{n} ({pct(specific,n)})")
    print(f"  UNRECOGNIZED ({len(unrecognized)}): {', '.join(unrecognized)}")

    # 2) Region resolution
    resolved = correct = 0
    missed = []
    for addr, expected in REGION_TESTS:
        got = region_from_address(addr)
        if got != "unknown":
            resolved += 1
            if got == expected:
                correct += 1
            else:
                missed.append(f"{addr.split(',')[-1].strip()}={got}!={expected}")
        else:
            missed.append(addr.split(",")[-1].strip() + "=?")
    n = len(REGION_TESTS)
    print(f"\n[Region] resolved {resolved}/{n} ({pct(resolved,n)}), "
          f"correct {correct}/{n} ({pct(correct,n)})")
    print(f"  MISSED/WRONG: {', '.join(missed) if missed else 'none'}")

    # 3) Dish recall (nut dishes flagged) + precision (nut-free not flagged)
    def flagged(name):
        return score_menu_item_prior(item_name=name).basis == "dish_knowledge"
    recall_hits = [d for d in NUT_DISHES if flagged(d)]
    recall_miss = [d for d in NUT_DISHES if not flagged(d)]
    fp = [d for d in NUT_FREE_DISHES if flagged(d)]
    native_hits = [d for d in NATIVE_NUT_DISHES if flagged(d)]
    print(f"\n[Dish recall]  {len(recall_hits)}/{len(NUT_DISHES)} ({pct(len(recall_hits),len(NUT_DISHES))}) nut dishes flagged")
    print(f"  MISSED: {', '.join(recall_miss) if recall_miss else 'none'}")
    print(f"[Dish precision] {len(NUT_FREE_DISHES)-len(fp)}/{len(NUT_FREE_DISHES)} ({pct(len(NUT_FREE_DISHES)-len(fp),len(NUT_FREE_DISHES))}) nut-free correctly NOT flagged")
    print(f"  FALSE POSITIVES: {', '.join(fp) if fp else 'none'}")
    native_miss = [d for d in NATIVE_NUT_DISHES if not flagged(d)]
    print(f"[Non-Latin] {len(native_hits)}/{len(NATIVE_NUT_DISHES)} ({pct(len(native_hits),len(NATIVE_NUT_DISHES))}) non-Latin nut dishes flagged")
    print(f"  MISSED: {', '.join(native_miss) if native_miss else 'none'}")

    # 4) Composite: overall "no-silent-assumption" score
    comp = (recognized + correct + len(recall_hits)
            + (len(NUT_FREE_DISHES) - len(fp)) + len(native_hits))
    comp_total = (len(CUISINE_TESTS) + len(REGION_TESTS) + len(NUT_DISHES)
                  + len(NUT_FREE_DISHES) + len(NATIVE_NUT_DISHES))
    print("\n" + "-" * 64)
    print(f"COMPOSITE grounded-recognition score: {comp}/{comp_total} = {pct(comp, comp_total)}")
    print("-" * 64)


if __name__ == "__main__":
    main()
