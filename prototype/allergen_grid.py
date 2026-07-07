"""PROTOTYPE: multilingual allergen lexicon (b) + DOM allergen-grid scraper (a).

(b) `canonical_allergens(text)` maps localized allergen names (EN/DE/FR/ES/IT/NL/JP
    + the existing nut vocab) to SafePlate's canonical English terms, and adds the
    EU-14 allergens the production list lacks (fish, celery, mustard, lupin, sulphites,
    crustacean, mollusc). Word-boundary matching avoids false hits ("ei" in "protein").

(a) `extract_from_allergen_grid(html)` reconstructs dish -> allergen rows from an HTML
    or ARIA allergen TABLE: it finds the header row whose cells name >=2 allergens
    (via the lexicon, so a German table works), then reads each data row's positive
    marks (tick / x / filled icon / "contains"). It does NOT attempt bespoke
    hashed-class div layouts (e.g. Nando's) -- those have no stable structure and would
    need brittle per-site selectors. Kept prototype-local; the lexicon is a candidate
    to upstream into menu_text.ALLERGEN_TERMS.
"""
from __future__ import annotations

import re
from dataclasses import replace

from safeplate.menu_text import MenuItemRecord
from safeplate.soup import make_soup

# canonical English allergen -> localized synonyms (lowercase). Canonical names align
# with SafePlate's vocabulary (soy/peanut/nuts/milk/...) and extend it with EU-14.
_LEXICON: dict[str, tuple[str, ...]] = {
    "milk": ("milk", "milch", "lait", "leche", "latte", "melk", "mjolk", "dairy", "乳", "牛乳", "ミルク", "우유"),
    "egg": ("egg", "eggs", "ei", "eier", "oeuf", "œuf", "huevo", "uovo", "ovo", "卵", "たまご", "계란", "달걀"),
    "soy": ("soy", "soya", "soja", "soia", "大豆", "콩"),
    "wheat": ("wheat", "weizen", "ble", "blé", "trigo", "frumento", "tarwe", "小麦", "밀"),
    "gluten": ("gluten", "glutine", "グルテン", "글루텐"),
    "fish": ("fish", "fisch", "poisson", "pescado", "pesce", "vis", "peixe", "魚",
             "さけ", "サケ", "鮭", "さば", "サバ", "鯖", "생선", "어류"),
    "crustacean": ("crustacean", "crustace", "crustacés", "crustacés", "krebstier", "krebstiere",
                   "crustaceo", "crostacei", "schaaldier", "甲殻類", "갑각류", "prawn"),
    "shellfish": ("shellfish",),
    "shrimp": ("shrimp", "crevette", "garnele", "gamba", "えび", "エビ", "海老", "새우"),
    "crab": ("crab", "crabe", "krabbe", "かに", "カニ", "蟹", "게"),
    "mollusc": ("mollusc", "mollusk", "molluscs", "weichtier", "weichtiere", "mollusque",
                "molusco", "mollusco", "weekdier", "軟体動物", "연체동물"),
    "sesame": ("sesame", "sesam", "sésame", "sesamo", "sésamo", "ごま", "胡麻", "참깨"),
    "peanut": ("peanut", "peanuts", "erdnuss", "cacahuete", "cacahuète", "arachide", "arachidi",
               "amendoim", "pinda", "落花生", "ピーナッツ", "땅콩"),
    "nuts": ("tree nut", "tree nuts", "treenut", "nuts", "nut", "nusse", "nüsse", "schalenfrucht",
             "schalenfrüchte", "fruits a coque", "fruits à coque", "frutos de cascara",
             "frutta a guscio", "noten", "木の実", "견과"),
    "almond": ("almond", "mandel", "amande", "almendra", "mandorla", "アーモンド", "아몬드"),
    "hazelnut": ("hazelnut", "haselnuss", "noisette", "avellana", "nocciola", "ヘーゼルナッツ"),
    "walnut": ("walnut", "walnuss", "noix", "nuez", "noce", "くるみ", "クルミ", "호두"),
    "cashew": ("cashew", "anacardo", "cajou", "カシューナッツ", "腰果"),
    "pistachio": ("pistachio", "pistazie", "pistache", "pistacho", "pistacchio", "ピスタチオ"),
    "celery": ("celery", "sellerie", "celeri", "céleri", "apio", "sedano", "selderij", "セロリ", "셀러리"),
    "mustard": ("mustard", "senf", "moutarde", "mostaza", "senape", "mosterd", "マスタード", "からし", "겨자"),
    "lupin": ("lupin", "lupine", "lupinen", "altramuces", "lupini", "ルピナス"),
    "sulphites": ("sulphite", "sulphites", "sulfite", "sulfites", "sulphur dioxide",
                  "sulfur dioxide", "sulfito", "sulfitos", "solfiti", "schwefeldioxid",
                  "anhydride sulfureux", "亜硫酸塩", "아황산"),
}

_HAS_LATIN = re.compile(r"[a-zà-ÿ]")


def canonical_allergens(text: str) -> set[str]:
    """Canonical English allergens named anywhere in `text`, across languages. Latin
    synonyms match on word boundaries (so 'ei' won't hit 'protein'); CJK synonyms match
    against a whitespace-STRIPPED copy, because Japanese allergen tables stack header
    characters vertically (e.g. '小\\n麦' for 小麦/wheat)."""
    low = " ".join((text or "").lower().split())
    if not low:
        return set()
    low_nospace = re.sub(r"\s+", "", low)
    found: set[str] = set()
    for canon, syns in _LEXICON.items():
        for syn in syns:
            if _HAS_LATIN.search(syn):
                if re.search(rf"(?<![a-zà-ÿ]){re.escape(syn)}(?![a-zà-ÿ])", low):
                    found.add(canon)
                    break
            elif syn in low_nospace:
                found.add(canon)
                break
    return found


# --- (a) DOM allergen-grid scraper -------------------------------------------
_ROLE_ROW = {"row"}
_ROLE_CELL = {"cell", "gridcell", "columnheader", "rowheader"}
_POS_CHARS = set("✓✔☑●▪■◆◼★✱xX×")        # a filled/tick/x mark = contains
_POS_WORDS = {"y", "yes", "ja", "oui", "si", "sì", "sim", "contains", "may contain",
              "traces", "trace", "spuren", "可", "有", "✓"}
_NEG_TOKENS = {"", "-", "–", "—", "○", "n", "no", "nein", "non", "✗", "✘", "free", "none"}


def _row_cells(row):
    cells = row.find_all(["td", "th"], recursive=False) or row.find_all(["td", "th"])
    if not cells:
        cells = row.find_all(attrs={"role": lambda v: v in _ROLE_CELL})
    return cells


def _grid_rows(grid):
    rows = grid.find_all("tr")
    if not rows:
        rows = grid.find_all(attrs={"role": lambda v: v in _ROLE_ROW})
    return rows


def _cell_positive(cell) -> bool:
    """A cell marks 'contains' ONLY via a recognizable mark: a tick/cross/filled symbol,
    a short yes/contains token, or an icon. Arbitrary long text is NOT a mark -- in a
    mis-aligned/merged table a stray text column (e.g. a crust name) would otherwise be
    read as a positive allergen, a dangerous false positive for a safety app."""
    txt = " ".join(cell.get_text(" ", strip=True).split())
    low = txt.lower()
    if any(ch in _POS_CHARS for ch in txt):
        return True
    if low in _POS_WORDS:                       # "yes"/"ja"/"contains"/"may contain"/...
        return True
    if not txt:                                  # icon-only cell
        icon = cell.find(["img", "svg", "i", "use"]) or cell.find(attrs={"class": re.compile("icon", re.I)})
        if icon is not None:
            meta = " ".join([
                (icon.get("alt") or ""), (icon.get("title") or ""),
                (icon.get("aria-label") or ""), " ".join(icon.get("class") or []),
            ]).lower()
            return not any(neg in meta for neg in ("free", "absent", "no-", "none", "not-"))
    return False


def _parse_grid(grid) -> list[MenuItemRecord]:
    rows = _grid_rows(grid)
    if len(rows) < 2:
        return []
    # Header = the row whose cells name the most distinct allergens (>=2).
    best_i, best_cols, best_map = -1, 0, {}
    for i, row in enumerate(rows[:6]):
        cells = _row_cells(row)
        col_map: dict[int, str] = {}
        for j, cell in enumerate(cells):
            canon = canonical_allergens(cell.get_text(" ", strip=True))
            if len(canon) == 1:
                col_map[j] = next(iter(canon))
        if len(col_map) > best_cols:
            best_i, best_cols, best_map = i, len(col_map), col_map
    if best_cols < 2:
        return []
    allergen_cols = best_map
    # Name column: first column that is NOT an allergen column.
    name_col = next((j for j in range(max(allergen_cols) + 1) if j not in allergen_cols), 0)

    out: list[MenuItemRecord] = []
    for row in rows[best_i + 1:]:
        cells = _row_cells(row)
        if len(cells) <= max(allergen_cols.keys(), default=0):
            continue
        name = " ".join(cells[name_col].get_text(" ", strip=True).split()) if name_col < len(cells) else ""
        # Skip only blanks. A dish name MAY contain an allergen word ("Garlic Shrimp",
        # "Egg Fried Rice"); repeated header rows are dropped naturally because their
        # text cells aren't recognized as positive marks (so they yield no allergens).
        if len(name) < 2:
            continue
        allergens = sorted({
            canon for col, canon in allergen_cols.items()
            if col < len(cells) and _cell_positive(cells[col])
        })
        if allergens:
            out.append(MenuItemRecord(
                restaurant_name="", restaurant_source_id="", menu_source_url="",
                category="", item_name=name, description="", price="",
                dietary_terms=[], allergen_terms=allergens, source_type="",
                extraction_method="allergen_grid", confidence=0.55,
                raw_text=f"{name}: {', '.join(allergens)}", fetched_at="",
            ))
    return out


def extract_from_allergen_grid(html: str, *, soup=None) -> list[MenuItemRecord]:
    """Dish x allergen rows recovered from any HTML/ARIA allergen TABLE on the page."""
    if soup is None:
        soup = make_soup(html)
    grids = list(soup.find_all("table"))
    grids += soup.find_all(attrs={"role": lambda v: v in ("table", "grid", "treegrid")})
    out: list[MenuItemRecord] = []
    seen: set[str] = set()
    for grid in grids:
        for rec in _parse_grid(grid):
            key = rec.item_name.lower()
            if key not in seen:
                seen.add(key)
                out.append(rec)
    # Safety guard: a complex/merged table can mis-align so that EVERY dish parses to
    # the same single allergen (an artifact, e.g. one stray text column read as a mark).
    # Real menus vary -- distrust a parse with no variety at all rather than emit
    # uniformly wrong (and likely incomplete) allergen data.
    if len(out) >= 5:
        distinct = {tuple(r.allergen_terms) for r in out}
        if len(distinct) == 1 and len(next(iter(distinct))) <= 1:
            return []
    return out
