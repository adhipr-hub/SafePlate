"""_finalize prefers more up-to-date PDFs and collapses dated re-uploads."""

from __future__ import annotations

from safeplate.extraction2.discover import Candidate, _finalize


def _menu(url):
    return Candidate(url=url, anchor_text="Menu", kind="menu", source="brave_menu_pdf")


def test_newer_menu_pdf_ranks_before_older():
    # Distinct menus (different stems, so both survive) -> the newer one ranks first.
    older = _menu("https://orens.com/wp/2018/06/breakfast-menu.pdf")
    newer = _menu("https://orens.com/wp/2024/03/catering-menu.pdf")
    out = _finalize([older, newer], max_candidates=12)
    assert [c.url for c in out].index(newer.url) < [c.url for c in out].index(older.url)


def test_dated_reupload_collapses_to_newest():
    old = _menu("https://x.com/wp/2021/01/dinner-menu.pdf")
    new = _menu("https://x.com/wp/2024/06/dinner-menu.pdf")
    out = _finalize([old, new], max_candidates=12)
    urls = [c.url for c in out]
    assert new.url in urls
    assert old.url not in urls  # same menu, older copy dropped


def test_different_menus_both_kept():
    brunch = _menu("https://x.com/wp/2024/01/brunch-menu.pdf")
    dinner = _menu("https://x.com/wp/2024/01/dinner-menu.pdf")
    out = _finalize([brunch, dinner], max_candidates=12)
    urls = {c.url for c in out}
    assert brunch.url in urls and dinner.url in urls


def test_undated_pdf_not_dropped_when_alone():
    only = _menu("https://x.com/menu/dinner.pdf")
    out = _finalize([only], max_candidates=12)
    assert [c.url for c in out] == [only.url]


def test_allergen_kind_still_outranks_menu_regardless_of_date():
    # Recency is only a tiebreak WITHIN a kind; a 2018 allergen chart must still beat
    # a 2024 plain menu (allergen data is the point).
    allergen = Candidate(url="https://x.com/wp/2018/01/allergens.pdf",
                         anchor_text="Allergens", kind="allergen", source="link")
    menu = _menu("https://x.com/wp/2024/01/menu.pdf")
    out = _finalize([menu, allergen], max_candidates=12)
    assert out[0].url == allergen.url
