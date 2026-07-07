from __future__ import annotations

from safeplate.config import get_user_agent
from safeplate.extraction2.classify import IMAGE_EXTS, classify_html
from safeplate.extraction2.schema import Payload, PayloadKind
from safeplate.http_client import http_get
from safeplate.page_fetch import fetch_html_page


def payload_from_html(
    url: str,
    html: str,
    *,
    source_type: str = "website_link",
    restaurant_name: str | None = None,
    restaurant_source_id: str | None = None,
) -> Payload:
    return Payload(
        url=url,
        source_type=source_type,
        kind=classify_html(html),
        text=html,
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
    )


def payload_from_pdf_text(
    url: str,
    text: str,
    *,
    source_type: str = "pdf",
    restaurant_name: str | None = None,
    restaurant_source_id: str | None = None,
) -> Payload:
    """A PDF whose text layer has already been extracted -- routed as TEXT so the
    LLM interprets it (Phase 2); structured parse yields nothing on flat text."""
    return Payload(
        url=url,
        source_type=source_type,
        kind=PayloadKind.TEXT,
        text=text,
        restaurant_name=restaurant_name,
        restaurant_source_id=restaurant_source_id,
    )


def acquire(url: str, *, source_type: str, user_agent: str | None = None,
            use_cache: bool = True, fetch_mode: str = "static") -> Payload:
    """Live acquisition: fetch a URL and normalize it to a Payload. Reuses v1's
    pooled HTTP / robots infra. (The offline eval harness builds payloads from
    snapshots via the helpers above and does not call this.) ``use_cache=False``
    forces a live fetch (the 'raw' / no-cache test path). fetch_mode is forwarded
    to fetch_html_page for HTML pages ("auto" lets the dossier render JS-built
    menus); images and PDFs are plain HTTP and ignore it."""
    user_agent = user_agent or get_user_agent()
    from safeplate.config import get_fetch_read_timeout

    read_timeout = get_fetch_read_timeout()
    low = url.lower().split("?")[0]  # ignore ?v= cache-busters (Shopify etc.) for type sniffing
    if source_type == "image" or low.endswith(IMAGE_EXTS):
        resp = http_get(url, user_agent=user_agent, timeout=read_timeout, use_cache=use_cache)
        return Payload(url=url, source_type="image", kind=PayloadKind.VISUAL,
                       content=resp.content, mime="image")
    if source_type == "pdf" or low.endswith(".pdf"):
        resp = http_get(url, user_agent=user_agent, timeout=read_timeout, use_cache=use_cache)
        # Carry the bytes (for the allergen-matrix table parser) AND the extracted
        # text (for the LLM), routed as TEXT so structured-then-LLM both run.
        try:
            from safeplate.menu_text import _pdf_text_from_bytes
            text = _pdf_text_from_bytes(resp.content)
        except Exception:
            text = ""
        return Payload(url=url, source_type="pdf", kind=PayloadKind.TEXT,
                       text=text, content=resp.content, mime="application/pdf")
    html = fetch_html_page(
        url, user_agent=user_agent, use_cache=use_cache, fetch_mode=fetch_mode
    ).html
    return payload_from_html(url, html, source_type=source_type)
