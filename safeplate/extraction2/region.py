"""Home-country / source-region provenance helpers (shared, dependency-free).

The global chain benchmark surfaced a safety-critical failure: the Brave web-search
fallback would win an allergen matrix from the WRONG country (US Burger King <-
Malta, Starbucks <- Switzerland). A foreign allergen chart is dangerous for an
allergy app -- different recipes, suppliers, and labelling laws.

Two layers use these helpers:
  * discovery -- bias web-search queries to the home region and rank home/official
    sources ABOVE foreign ones (foreign kept only as a last-resort fallback).
  * content-locale validation -- read the region OUT of the extracted PDF/matrix
    text and, when it differs from the diner's region, surface "this data is from
    <region>, not verified for your area" rather than silently trusting it.

Kept free of extraction/network imports so both discover.py and pipeline.py can
import it without an import cycle.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Country name / alias (lowercased) -> ISO2 home-country code.
_COUNTRY_NAMES = {
    "united states": "US", "usa": "US", "us": "US", "u.s.a.": "US", "u.s.": "US",
    "united kingdom": "GB", "uk": "GB", "u.k.": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "great britain": "GB", "britain": "GB",
    "canada": "CA", "australia": "AU", "new zealand": "NZ", "ireland": "IE",
    "germany": "DE", "deutschland": "DE", "france": "FR", "spain": "ES",
    "italy": "IT", "netherlands": "NL", "belgium": "BE", "austria": "AT",
    "switzerland": "CH", "sweden": "SE", "norway": "NO", "denmark": "DK",
    "finland": "FI", "portugal": "PT", "poland": "PL", "malta": "MT",
    "singapore": "SG", "philippines": "PH", "japan": "JP", "taiwan": "TW",
    "india": "IN", "mexico": "MX", "brazil": "BR", "south africa": "ZA",
    "south korea": "KR", "hong kong": "HK", "china": "CN",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA",
}

# ISO2 -> human display label (for UI copy: "this data is from New Zealand").
_COUNTRY_LABEL = {
    "US": "the United States", "GB": "the United Kingdom", "CA": "Canada",
    "AU": "Australia", "NZ": "New Zealand", "IE": "Ireland", "DE": "Germany",
    "FR": "France", "ES": "Spain", "IT": "Italy", "NL": "the Netherlands",
    "BE": "Belgium", "AT": "Austria", "CH": "Switzerland", "SE": "Sweden",
    "NO": "Norway", "DK": "Denmark", "FI": "Finland", "PT": "Portugal",
    "PL": "Poland", "MT": "Malta", "SG": "Singapore", "PH": "the Philippines",
    "JP": "Japan", "TW": "Taiwan", "IN": "India", "MX": "Mexico", "BR": "Brazil",
    "ZA": "South Africa", "KR": "South Korea", "HK": "Hong Kong", "CN": "China",
    "AE": "the UAE", "SA": "Saudi Arabia",
}

# ISO2 -> the geographic token to inject into web-search queries.
_REGION_TOKEN = {
    "US": "USA", "GB": "UK", "CA": "Canada", "AU": "Australia", "NZ": "New Zealand",
    "IE": "Ireland", "DE": "Germany", "FR": "France", "ES": "Spain", "IT": "Italy",
    "NL": "Netherlands", "BE": "Belgium", "AT": "Austria", "CH": "Switzerland",
    "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland",
    "PT": "Portugal", "PL": "Poland", "MT": "Malta", "SG": "Singapore",
    "PH": "Philippines", "JP": "Japan", "TW": "Taiwan", "IN": "India",
    "MX": "Mexico", "BR": "Brazil", "ZA": "South Africa", "KR": "South Korea",
    "HK": "Hong Kong", "CN": "China", "AE": "UAE", "SA": "Saudi Arabia",
}

# Final-label ccTLD -> ISO2 (covers two-level suffixes too: "foo.co.uk" -> "uk").
_CCTLD_COUNTRY = {
    "us": "US", "uk": "GB", "ca": "CA", "au": "AU", "nz": "NZ", "ie": "IE",
    "de": "DE", "fr": "FR", "es": "ES", "it": "IT", "nl": "NL", "be": "BE",
    "at": "AT", "ch": "CH", "se": "SE", "no": "NO", "dk": "DK", "fi": "FI",
    "pt": "PT", "pl": "PL", "mt": "MT", "sg": "SG", "ph": "PH", "jp": "JP",
    "tw": "TW", "in": "IN", "mx": "MX", "br": "BR", "za": "ZA", "kr": "KR",
    "hk": "HK", "cn": "CN", "ae": "AE", "sa": "SA",
}

# In-CONTENT region detection uses ONLY two low-false-positive signal classes:
#
#  1. DOMAIN tells -- any hostname-shaped token in the text (e.g. a footer URL
#     "burgerking.co.nz") whose ccTLD maps to a country via `host_country`. This
#     reuses the SAME reliable ccTLD logic as the URL host, so non-domains like
#     "order.php", "hero.jpg", "menu.items", "deliveroo.com" resolve to a
#     non-ccTLD final label and credit nothing. (An earlier version substring-
#     matched bare ".ph"/".jp"/".it"/... against prose and mis-fired on .php /
#     .jpg / menu.items / getmenu.info / deliveroo -- see test_region_locale.)
#  2. UNAMBIGUOUS MULTIWORD country names -- phrases that are not cuisines and
#     essentially never appear incidentally (e.g. "new zealand", "united
#     kingdom"). Single country WORDS (italy/japan/india/australia...) are
#     deliberately EXCLUDED: they pepper menu prose (cuisines, "Australian
#     wagyu", "India Pale Ale") and are too noisy to assert a region on.
#
# US is intentionally absent: it is the common home and is read from the address,
# not positively detected, so absence -> "unknown", never "foreign".
_DOMAIN_RE = re.compile(r"[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}")

# For HTML sources the pipeline hands us the RAW page markup (acquire.py routes
# html as payload.text). Markup is full of domain-SHAPED junk that says nothing
# about the menu's region: CSS class chains ("gallery-item-hover.no" -> Norway,
# seen on a Wix site), font-license credits inside <style> blocks
# ("hi@typemade.mx" -> Mexico -- the Yaba's Pittsburgh false positive), and
# minified-script property tokens ("t.hk"). So before scanning, HTML is reduced
# to its VISIBLE text: drop comments and <script>/<style> bodies, then all tags
# (attributes go with them). Regex, not a soup parse, to keep this module
# dependency-free (see module docstring) -- good enough for tell-scanning.
_HTML_HINT_RE = re.compile(r"<(?:!doctype|html|head|body|script|style|meta|link|div)[\s>]", re.I)
_HTML_DROP_RE = re.compile(
    r"<!--.*?-->|<script\b[^>]*>.*?</script\s*>|<style\b[^>]*>.*?</style\s*>",
    re.I | re.S,
)
_HTML_TAG_RE = re.compile(r"<[^>]*>")


def _visible_text(text: str) -> str:
    """The human-visible text of ``text`` when it looks like HTML; unchanged
    otherwise (PDF/plain text passes straight through)."""
    if not _HTML_HINT_RE.search(text):
        return text
    return _HTML_TAG_RE.sub(" ", _HTML_DROP_RE.sub(" ", text))

_STRONG_NAME_SIGNALS: dict[str, tuple[str, ...]] = {
    "GB": ("united kingdom", "great britain"),
    "NZ": ("new zealand", "aotearoa"),
    "ZA": ("south africa",),
    "SA": ("saudi arabia",),
    "AE": ("united arab emirates",),
    "KR": ("south korea",),
    "HK": ("hong kong",),
}

# Independent STRUCTURAL tells (calling code + unambiguous currency) for exactly the
# countries that have a multiword NAME signal above. A country NAME (e.g. "new
# zealand") may only assert a region when one of these ALSO appears -- a bare menu-
# prose mention (a wine's origin) must not brand the source foreign. Alpha currency
# codes are word-bounded so "sar" can't match inside "caesar", "aed" inside a word,
# etc. ccTLD tells are handled separately (the domain scan) and stay decisive alone.
_STRUCTURAL_TELL_RES: dict[str, re.Pattern[str]] = {
    "NZ": re.compile(r"\+64\b|nz\$|\bnzd\b"),
    "GB": re.compile(r"\+44\b|£|\bgbp\b"),
    "ZA": re.compile(r"\+27\b|\bzar\b"),
    "SA": re.compile(r"\+966\b|\bsar\b|﷼"),
    "AE": re.compile(r"\+971\b|\baed\b"),
    "KR": re.compile(r"\+82\b|₩|\bkrw\b"),
    "HK": re.compile(r"\+852\b|hk\$|\bhkd\b"),
}


def _structural_signals(low: str) -> set[str]:
    """ISO2 codes with an independent structural tell (calling code or unambiguous
    currency) present in the already-lowercased visible text. Used to corroborate a
    multiword country NAME before it may assert a source region (see
    detect_source_region). Returns an empty set when none are present."""
    return {code for code, rx in _STRUCTURAL_TELL_RES.items() if rx.search(low)}


def country_label(code: str | None) -> str:
    """Human label for UI copy (e.g. 'NZ' -> 'New Zealand'); the raw code if unknown."""
    if not code:
        return ""
    return _COUNTRY_LABEL.get(code, code)


def region_token(home_country: str | None) -> str:
    """Geographic query token for the home country (e.g. 'US' -> 'USA'); '' when
    unknown so query builders simply omit the bias."""
    return _REGION_TOKEN.get(home_country or "", "")


def host_country(host: str) -> str | None:
    """ISO2 country implied by a host's ccTLD, or None for a generic/global TLD
    (.com/.org/.net/...) that carries NO country signal. '.co' is treated as
    generic (overwhelmingly used as a global TLD, not Colombia)."""
    host = (host or "").lower().split(":")[0]
    last = host.rsplit(".", 1)[-1] if "." in host else ""
    return _CCTLD_COUNTRY.get(last)


# A US state abbreviation + 5(-4) ZIP tail, e.g. "Springfield, IL 62704". Many
# address sources (notably OSM) omit the country segment, so this recovers home=US
# from the state+ZIP shape rather than collapsing to None (which would disable the
# whole provenance guard -- see the code review's home-unknown finding).
_US_STATE_ZIP_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")


def home_country(address: str | None, website_url: str = "") -> str | None:
    """The diner's / restaurant's home country: read the country segment of the
    Places address (its last comma part), then a US state+ZIP tail, falling back to
    the official site's ccTLD. None when we can't tell -- callers then degrade
    gracefully (no demotion; a detected source still warns via region_notice)."""
    if address:
        parts = [p.strip() for p in address.split(",") if p.strip()]
        if parts:
            code = _COUNTRY_NAMES.get(parts[-1].lower())
            if code:
                return code
        if _US_STATE_ZIP_RE.search(address):
            return "US"
    return host_country(urlparse(website_url or "").netloc)


def is_foreign_source(url: str, home: str | None) -> bool:
    """True when the URL's host is a ccTLD belonging to a DIFFERENT country than
    home -- the wrong-country case to demote. Generic TLDs (no country signal) and
    the unknown-home case are never foreign."""
    if not home:
        return False
    host_cc = host_country(urlparse(url).netloc)
    return host_cc is not None and host_cc != home


def detect_source_region(text: str, url: str = "") -> str | None:
    """Best-effort ISO2 region of an extracted source: its URL ccTLD first
    (decisive), then reliable in-content tells -- ccTLD-bearing domains mentioned
    in the VISIBLE text (HTML is stripped of markup/scripts/styles first, so CSS
    classes and font-license credits can't vote) and unambiguous multiword
    country names. Conservative: returns None
    unless a single country clearly wins, so we never falsely brand home-region
    data as foreign. This is the content-locale check the benchmark called for --
    it catches a wrong-country chart hosted on a country-NEUTRAL CDN (e.g. a NZ
    Burger King PDF on an Azure blob whose footer cites burgerking.co.nz) that the
    ccTLD ranking alone can't see."""
    cc = host_country(urlparse(url or "").netloc)
    if cc:
        return cc
    low = _visible_text(text or "").lower()
    if not low:
        return None
    scores: dict[str, int] = {}
    # 1) ccTLD-bearing domains mentioned in the text (e.g. "burgerking.co.nz").
    for token in _DOMAIN_RE.findall(low):
        code = host_country(token)
        if code:
            scores[code] = scores.get(code, 0) + 1
    # 2) unambiguous multiword country names.
    for code, phrases in _STRONG_NAME_SIGNALS.items():
        if any(p in low for p in phrases):
            scores[code] = scores.get(code, 0) + 1
    if not scores:
        return None
    best = max(scores.values())
    winners = [c for c, v in scores.items() if v == best]
    return winners[0] if len(winners) == 1 else None


def region_notice(*, home: str | None, source_region: str | None) -> dict | None:
    """The UI notice describing where the SHOWN data came from relative to the
    diner's region. None only when no source region was detected (nothing to say).
    When the data is from another region we keep it (per product intent) but the UI
    states which region and that it isn't verified for the diner's area. If the home
    region itself is UNKNOWN we still warn (verified=False) rather than going silent
    -- silence would mean trusting clearly-localized data blindly (the missed-risk
    direction). `verified` is True only when a known home matches the source."""
    if not source_region:
        return None
    verified = bool(home) and source_region == home
    return {
        "verified": verified,
        "homeRegion": home or "",
        "homeLabel": country_label(home),
        "sourceRegion": source_region,
        "sourceLabel": country_label(source_region),
    }
