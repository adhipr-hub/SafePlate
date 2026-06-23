from __future__ import annotations

from urllib.parse import urljoin, urlparse

import trafilatura
from selectolax.parser import HTMLParser

from safeplate.menu_sources import STRICT_MENU_KEYWORDS

_STRIP_TAGS = ("script", "style", "nav", "footer", "header", "noscript")


def clean_text(html) -> str:
    try:
        extracted = trafilatura.extract(
            html,
            fast=True,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if extracted:
            return extracted
    except Exception:
        pass

    try:
        tree = HTMLParser(html)
        for tag in _STRIP_TAGS:
            for node in tree.css(tag):
                node.decompose()
        body = tree.body
        text = body.text(separator=" ") if body is not None else tree.text(separator=" ")
        return " ".join(text.split())
    except Exception:
        return ""


def menu_links(html, base_url, *, limit=6) -> list[str]:
    try:
        tree = HTMLParser(html)
        out: list[str] = []
        seen: set[str] = set()
        for node in tree.css("a"):
            href = node.attributes.get("href")
            if not href:
                continue
            href_lower = href.lower()
            text_lower = (node.text() or "").lower()
            is_pdf = href_lower.endswith(".pdf")
            matches = is_pdf or any(
                term in href_lower or term in text_lower
                for term in STRICT_MENU_KEYWORDS
            )
            if not matches:
                continue
            absolute = urljoin(base_url, href)
            scheme = urlparse(absolute).scheme
            if scheme not in ("http", "https"):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            out.append(absolute)
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []
