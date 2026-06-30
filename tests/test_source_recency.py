"""Spec for source recency: prefer the most up-to-date menu PDF.

Restaurant menu PDFs carry date signals -- a WordPress upload path
(/2024/03/...), a season range in the filename (...2022-2023menu.pdf), or a
date stamp (...020422.pdf). `source_recency(url)` turns those into a sortable
score (higher = newer; 0.0 when undated) so discovery can rank a current menu
ahead of a stale one and collapse a re-uploaded duplicate to its newest copy.
"""

from __future__ import annotations

from safeplate.extraction2.recency import dated_duplicate_key, source_recency

# Real Oren's Hummus PDFs (the case that motivated this).
A = "https://orenshummus.com/wp-content/uploads/2024/03/Orens-Catering-Menu_061722.pdf"
B = "https://orenshummus.com/wp-content/uploads/2022/11/Orens-Hummus-2022-2023menu.pdf"
C = "https://orenshummus.com/wp-content/uploads/2018/06/Breakfast_Menu_online.pdf"
D = "https://orenshummus.com/wp-content/uploads/2022/02/OH-Dine-In-Menu-020422.pdf"


def test_wordpress_upload_path_year_month():
    assert source_recency(A) > source_recency(B) > source_recency(D) > source_recency(C)


def test_newer_dine_in_menu_beats_older_one():
    # The motivating fix: the 2022-2023 dine-in menu must outrank the Feb-2022 one.
    assert source_recency(B) > source_recency(D)


def test_season_range_uses_later_year():
    assert source_recency("https://x.com/menu-2022-2023.pdf") >= 2023.0


def test_bare_year_in_filename():
    assert 2022.0 <= source_recency("https://x.com/files/Dinner-Menu-2022.pdf") < 2023.0


def test_mmddyy_stamp_in_filename():
    r = source_recency("https://x.com/m/Dine-In-020422.pdf")  # 02/04/22
    assert 2022.0 <= r < 2022.5


def test_undated_url_is_zero():
    assert source_recency("https://x.com/menu/dinner.pdf") == 0.0
    assert source_recency("") == 0.0


def test_does_not_misread_phone_or_zip_as_date():
    # A 7-digit phone fragment or a zip must not register as a date.
    assert source_recency("https://x.com/store/4085551234/menu.pdf") == 0.0


def test_dated_duplicate_key_groups_reuploads_ignoring_date():
    # Same menu re-uploaded under a dated path collapses to one key...
    k1 = dated_duplicate_key("https://x.com/wp/2023/01/dinner-menu.pdf")
    k2 = dated_duplicate_key("https://x.com/wp/2024/05/dinner-menu.pdf")
    assert k1 == k2
    # ...but a genuinely different menu does not.
    assert dated_duplicate_key("https://x.com/wp/2024/05/brunch-menu.pdf") != k1
