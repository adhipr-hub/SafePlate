from __future__ import annotations

from dataclasses import dataclass

from safeplate.http_client import HttpConnectionError, HttpError, http_get
from safeplate.robots import can_fetch_url


@dataclass(frozen=True)
class HtmlPage:
    requested_url: str
    final_url: str
    html: str
    fetch_method: str


class PageFetchError(RuntimeError):
    """Raised when an HTML page cannot be fetched or rendered."""


def fetch_html_page(
    url: str,
    *,
    user_agent: str,
    fetch_mode: str = "static",
) -> HtmlPage:
    """fetch_mode: 'static' (default), 'dynamic' (render JS), or 'auto'
    (static first, render only if static fails or looks JS-empty)."""
    if fetch_mode == "static":
        return _fetch_static_html(url, user_agent=user_agent)
    if fetch_mode == "dynamic":
        return _fetch_dynamic_html(url, user_agent=user_agent)
    if fetch_mode == "auto":
        try:
            page = _fetch_static_html(url, user_agent=user_agent)
        except PageFetchError:
            return _fetch_dynamic_html(url, user_agent=user_agent)
        if _looks_js_empty(page.html):
            try:
                return _fetch_dynamic_html(url, user_agent=user_agent)
            except PageFetchError:
                return page
        return page
    raise PageFetchError(f"Unknown fetch_mode: {fetch_mode}")


def _looks_js_empty(html: str) -> bool:
    # Heuristic: very little visible text + a JS app root usually means the real
    # content is client-rendered. Cheap signal to decide whether to spend a render.
    lowered = html.lower()
    has_app_root = any(
        marker in lowered
        for marker in ('id="root"', 'id="app"', "__next_data__", "ng-version", "data-reactroot")
    )
    return has_app_root and len(html) < 60000


def _fetch_dynamic_html(url: str, *, user_agent: str) -> HtmlPage:
    from safeplate.dynamic_fetch import DynamicFetchError, render_html

    robots_decision = can_fetch_url(url, user_agent=user_agent)
    if not robots_decision.allowed:
        raise PageFetchError(f"Blocked by robots.txt: {robots_decision.reason}")
    try:
        html = render_html(url, user_agent=user_agent, timeout=30)
    except DynamicFetchError as exc:
        raise PageFetchError(str(exc)) from exc
    return HtmlPage(
        requested_url=url, final_url=url, html=html, fetch_method="dynamic_html"
    )


def _fetch_static_html(url: str, *, user_agent: str) -> HtmlPage:
    robots_decision = can_fetch_url(url, user_agent=user_agent)
    if not robots_decision.allowed:
        raise PageFetchError(f"Blocked by robots.txt: {robots_decision.reason}")

    try:
        response = http_get(url, user_agent=user_agent, timeout=30, use_cache=True)
    except HttpError as exc:
        raise PageFetchError(str(exc)) from exc
    except HttpConnectionError as exc:
        raise PageFetchError(str(exc)) from exc

    content_type = response.content_type
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        raise PageFetchError(f"Expected HTML from {url}, got {content_type}")

    return HtmlPage(
        requested_url=url,
        final_url=response.final_url,
        html=response.content.decode("utf-8", errors="replace"),
        fetch_method="static_html",
    )
