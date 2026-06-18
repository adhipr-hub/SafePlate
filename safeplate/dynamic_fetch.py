"""Headless-browser (Playwright) rendering for JavaScript menus.

Static fetching can't see menus that are built by client-side JS — which the
worldwide benchmark showed is the dominant remaining gap. This renders the page
in real Chromium so the menu HTML/prices land in the DOM. It is an optional,
opt-in fallback: if Playwright isn't installed, callers degrade to static.

Thread-safety: the Playwright *sync* API is single-thread-bound, so every render
is serialized under a lock and the dynamic pipeline path runs with one worker.
"""

from __future__ import annotations

import re
import threading

# Generic, site-agnostic interactions that commonly reveal menu content:
# dismiss a cookie/consent wall, then scroll to trigger lazy-loaded sections.
_CONSENT_LABELS = re.compile(
    r"^(accept|accept all|i agree|agree|got it|ok|allow all|continue|close|"
    r"akzeptieren|accepter|aceptar|同意)$",
    re.I,
)

try:
    from playwright.sync_api import sync_playwright

    _HAS_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    _HAS_PLAYWRIGHT = False


class DynamicFetchError(RuntimeError):
    """Raised when a page cannot be rendered."""


_LOCK = threading.Lock()
_CACHE: dict[str, str] = {}
_state: dict[str, object] = {"pw": None, "browser": None}


def has_dynamic_rendering() -> bool:
    return _HAS_PLAYWRIGHT


def _browser():
    if _state["browser"] is None:
        pw = sync_playwright().start()
        _state["pw"] = pw
        _state["browser"] = pw.chromium.launch(headless=True)
    return _state["browser"]


def render_html(
    url: str,
    *,
    user_agent: str,
    timeout: float = 30,
    use_cache: bool = True,
) -> str:
    """Return fully-rendered HTML for a URL. Raises DynamicFetchError on failure."""
    if not _HAS_PLAYWRIGHT:
        raise DynamicFetchError("playwright is not installed")
    if use_cache and url in _CACHE:
        return _CACHE[url]

    with _LOCK:
        if use_cache and url in _CACHE:
            return _CACHE[url]
        context = None
        try:
            context = _browser().new_context(user_agent=user_agent)
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            _reveal_content(page)
            html = page.content()
        except Exception as exc:
            raise DynamicFetchError(f"render failed for {url}: {exc}") from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass

    if use_cache:
        _CACHE[url] = html
    return html


def _reveal_content(page) -> None:
    # Dismiss a consent/cookie wall if present (best-effort, never fatal).
    try:
        button = page.get_by_role("button", name=_CONSENT_LABELS)
        if button.count() > 0:
            button.first.click(timeout=1500)
    except Exception:
        pass
    # Scroll to trigger lazy-loaded menu sections.
    try:
        for _ in range(4):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(350)
    except Exception:
        pass
    try:
        page.wait_for_timeout(600)
    except Exception:
        pass


def shutdown() -> None:
    with _LOCK:
        browser = _state.get("browser")
        pw = _state.get("pw")
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass
        _state["browser"] = None
        _state["pw"] = None
