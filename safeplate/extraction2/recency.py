"""Recency signals for menu sources -- prefer the most up-to-date PDF.

Restaurant menus are re-published over time; an older copy lingers at a stale URL
while the current one sits at a newer path. Filenames and CMS upload paths leak the
date: a WordPress ``/2024/03/`` upload folder, a season range ``...2022-2023menu``,
or a stamp ``...020422.pdf`` (MMDDYY). ``source_recency(url)`` distils those into a
sortable score (higher = newer; ``0.0`` when no date is present) so discovery can
rank a current menu ahead of a stale one, and ``dated_duplicate_key(url)`` lets a
re-upload of the SAME menu collapse to its newest copy.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# /YYYY/MM/ CMS upload folder (WordPress, etc.) -- the most reliable upload date.
_WP_PATH = re.compile(r"/(20[0-3]\d)/(0[1-9]|1[0-2])/")
# A season range in the filename ("2022-2023menu") -> use the LATER year.
_RANGE = re.compile(r"(20[0-3]\d)[-_](20[0-3]\d)")
# YYYYMMDD (e.g. 20240317) and MMDDYY (e.g. 020422), each isolated by non-digits.
_YMD = re.compile(r"(?<!\d)(20[0-3]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")
_MDY6 = re.compile(r"(?<!\d)(0[1-9]|1[0-2])([0-2]\d|3[01])(\d\d)(?!\d)")
# A bare 4-digit year, isolated so a phone/zip fragment can't register. [0-3] caps it
# at 2000-2039 so "2055-5512" style strings don't read as a far-future year.
_YEAR = re.compile(r"(?<!\d)(20[0-3]\d)(?!\d)")


def source_recency(url: str) -> float:
    """A sortable freshness score from date signals in ``url`` (higher = newer).
    Returns ``0.0`` when the URL carries no recognizable date."""
    if not url:
        return 0.0
    base = url.lower().split("?", 1)[0].split("#", 1)[0]
    fname = base.rsplit("/", 1)[-1]
    best = 0.0

    m = _WP_PATH.search(base)
    if m:
        best = max(best, int(m.group(1)) + int(m.group(2)) / 12.0)

    for r in _RANGE.finditer(base):
        best = max(best, float(max(int(r.group(1)), int(r.group(2)))))

    for r in _YMD.finditer(base):
        best = max(best, int(r.group(1)) + int(r.group(2)) / 12.0)

    for r in _MDY6.finditer(fname):  # MMDDYY is ambiguous; only trust it in the filename
        best = max(best, 2000 + int(r.group(3)) + int(r.group(1)) / 12.0)

    for r in _YEAR.finditer(base):
        best = max(best, float(int(r.group(1))))

    return best


def dated_duplicate_key(url: str) -> str:
    """A key that is identical for the same menu re-uploaded under a different date,
    so the freshest copy can win. Built from the host + the filename stem with all
    digits/date tokens stripped: ``/2023/01/dinner-menu.pdf`` and
    ``/2024/05/dinner-menu.pdf`` share a key; ``brunch-menu.pdf`` does not."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    fname = parsed.path.rsplit("/", 1)[-1].lower()
    if fname.endswith(".pdf"):
        fname = fname[:-4]
    stem = re.sub(r"20[0-3]\d[-_]?20[0-3]\d|\d{2,8}", " ", fname)
    stem = re.sub(r"[^a-z]+", " ", stem).strip()
    return f"{host}|{stem}"
