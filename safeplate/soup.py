from __future__ import annotations

from importlib.util import find_spec

from bs4 import BeautifulSoup

# Prefer lxml's C parser when installed (several times faster on real pages);
# fall back to the stdlib parser so SafePlate still runs without the optional dep.
_PARSER = "lxml" if find_spec("lxml") is not None else "html.parser"


def make_soup(html: str) -> BeautifulSoup:
    """Parse HTML with the fastest available BeautifulSoup backend."""
    from safeplate.timing import span

    with span("make_soup"):
        return BeautifulSoup(html, _PARSER)


def remove_non_content_tags(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
