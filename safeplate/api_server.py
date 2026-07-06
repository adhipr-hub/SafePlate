from __future__ import annotations

import base64
from collections import deque
import hmac
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from safeplate.config import (
    get_brave_search_api_key,
    get_geoapify_api_key,
    get_gemini_api_key,
    get_gemini_fallback_models,
    get_gemini_model,
    get_google_places_api_key,
)
from safeplate.common import _default_provider, _int_env
from safeplate.menu_service import run_menu_extraction
from safeplate.search_service import run_restaurant_search
from safeplate.demo_fixtures import DEFAULT_DEMO_LOCATION
from safeplate.pages import get_page


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


# Hard cap on a request body. The POST endpoints take a small JSON object (a search
# query or a restaurant payload); anything larger is abuse/garbage. Without this an
# attacker could stream an unbounded body and tie up a worker thread.
_MAX_BODY_BYTES = 512 * 1024

# Security headers applied to every response. CSP keeps 'unsafe-inline' for script/style
# (the app is inline-heavy; a nonce refactor is future work) but locks down the
# high-value vectors: no framing (clickjacking), no plugins/base-uri, same-origin XHR.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com data:; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
}


def _basic_auth_credentials() -> tuple[str, str] | None:
    """The (username, password) the app requires, or None to run open.

    Auth turns on only when SAFEPLATE_PASSWORD is set, so local use stays
    friction-free; a public deploy MUST set it. Username defaults to 'safeplate'
    (override with SAFEPLATE_USERNAME)."""
    password = os.environ.get("SAFEPLATE_PASSWORD", "").strip()
    if not password:
        return None
    username = os.environ.get("SAFEPLATE_USERNAME", "safeplate").strip() or "safeplate"
    return username, password


# Cap on distinct per-client buckets the limiter retains. The client key is the
# spoofable first hop of X-Forwarded-For on a public deploy, so without an upper
# bound an attacker could grow this map without limit (memory exhaustion). When we
# exceed it, fully-expired buckets are swept; the cap is far above any real
# concurrent-client count.
_RATE_LIMIT_MAX_KEYS = 10_000


class _RateLimiter:
    """Per-client sliding-window limiter (in-memory, thread-safe). Bounds API
    spend/abuse on the paid endpoints even for authenticated users. A limit <= 0
    disables it."""

    def __init__(self, *, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        if self._max <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = self._hits[key] = deque()
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            # Bound the map: clients that have gone fully quiet leave empty buckets
            # behind, so sweep them once we cross the cap (keeps memory bounded).
            if len(self._hits) > _RATE_LIMIT_MAX_KEYS:
                self._sweep(cutoff)
            return True

    def _sweep(self, cutoff: float) -> None:
        """Drop buckets whose most recent hit is past the window. Caller holds the
        lock. The currently-active key always has a fresh hit, so it survives."""
        stale = [k for k, b in self._hits.items() if not b or b[-1] <= cutoff]
        for k in stale:
            del self._hits[k]


class _DailyCap:
    """Process-wide daily ceiling on calls to the PAID endpoints -- a hard backstop
    against runaway API spend even if the per-IP limiter is evaded. A limit <= 0 (the
    default) disables it; a public deploy should set SAFEPLATE_DAILY_REQUEST_CAP."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._count = 0
        self._day = ""
        self._lock = threading.Lock()

    def allow(self) -> bool:
        if self._limit <= 0:
            return True
        today = time.strftime("%Y-%m-%d", time.gmtime())
        with self._lock:
            if today != self._day:
                self._day, self._count = today, 0
            if self._count >= self._limit:
                return False
            self._count += 1
            return True


def create_app_handler(*, demo_mode: bool = False) -> type[BaseHTTPRequestHandler]:
    auth = _basic_auth_credentials()
    # Trust X-Forwarded-For only when explicitly behind a single trusted reverse proxy
    # (e.g. Render). Off by default so the limiter keys on the real socket peer instead
    # of an attacker-spoofable header.
    trust_xff = _truthy(os.environ.get("SAFEPLATE_TRUST_XFF"))
    rate_limiter = _RateLimiter(
        max_requests=_int_env("SAFEPLATE_RATE_LIMIT_PER_MIN", 30),
        window_seconds=60.0,
    )
    # Throttle repeated auth FAILURES per client so the password can't be brute-forced.
    auth_fail_limiter = _RateLimiter(
        max_requests=_int_env("SAFEPLATE_AUTH_FAILS_PER_MIN", 15),
        window_seconds=60.0,
    )
    daily_cap = _DailyCap(_int_env("SAFEPLATE_DAILY_REQUEST_CAP", 0))

    class SafePlateRequestHandler(BaseHTTPRequestHandler):
        server_version = "SafePlateLocalApp/0.1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/healthz":
                self._send_json({"status": "ok"})
                return
            if not self._check_auth():
                return
            if path == "/":
                self._send_html(app_html())
                return
            # Deep-Dive Dossier (prototype) -- additive routes; the production
            # search/menu paths above and below are untouched.
            if path == "/dossier":
                from safeplate.dossier import dossier_html
                self._send_html(dossier_html())
                return
            if path == "/dossier/stream":
                self._handle_dossier_stream()
                return
            static_page = get_page(path)
            if static_page is not None:
                self._send_html(static_page)
                return
            if path == "/api/config":
                self._send_json(
                    {
                        "demoMode": demo_mode,
                        "defaultDemoLocation": DEFAULT_DEMO_LOCATION if demo_mode else "",
                        "googleConfigured": bool(get_google_places_api_key()),
                        "geoapifyConfigured": bool(get_geoapify_api_key()),
                        "braveConfigured": bool(get_brave_search_api_key()),
                        "geminiConfigured": bool(get_gemini_api_key()),
                        "geminiModel": get_gemini_model(),
                        "geminiFallbackModels": get_gemini_fallback_models(),
                        "defaultProvider": _default_provider(),
                    }
                )
                return
            self.send_error(404)

        def do_POST(self) -> None:
            if not self._check_auth():
                return
            path = urlparse(self.path).path
            if path in ("/api/search", "/api/menu") and not rate_limiter.check(
                self._client_ip()
            ):
                self._send_json(
                    {"error": "Rate limit exceeded -- please wait a minute and try again."},
                    status=429,
                )
                return
            if path in ("/api/search", "/api/menu") and not daily_cap.allow():
                self._send_json(
                    {"error": "The app has hit today's request budget. Please try again tomorrow."},
                    status=429,
                )
                return
            if path == "/api/search":
                self._handle_search()
                return
            if path == "/api/menu":
                self._handle_menu()
                return
            self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle_search(self) -> None:
            try:
                payload = self._read_json()
                response = run_restaurant_search(payload, demo_mode=demo_mode)
            except UnicodeError:
                # UnicodeEncodeError/DecodeError subclass ValueError, but they come from
                # deep in the HTTP/library stack (e.g. a provider result whose text can't
                # be encoded into a request header), NOT our validation. Never surface the
                # raw codec message to the client; log the traceback so we can locate it.
                self._log_internal_error("search")
                self._send_json({"error": "Internal error while searching."}, status=500)
                return
            except ValueError as exc:
                # Our own validation/bad-input messages are safe to surface.
                self._send_json({"error": str(exc)}, status=400)
                return
            except Exception:
                self._log_internal_error("search")
                self._send_json({"error": "Internal error while searching."}, status=500)
                return
            self._send_json(response)

        def _handle_menu(self) -> None:
            try:
                payload = self._read_json()
                response = run_menu_extraction(payload, demo_mode=demo_mode)
            except UnicodeError:
                # See _handle_search: a library encode/decode error is internal, not a
                # user-input ValueError; log it, don't leak the raw codec message.
                self._log_internal_error("menu")
                self._send_json({"error": "Internal error reading the menu."}, status=500)
                return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except Exception:
                self._log_internal_error("menu")
                self._send_json({"error": "Internal error reading the menu."}, status=500)
                return
            self._send_json(response)

        def _handle_dossier_stream(self) -> None:
            """Stream the Deep-Dive Dossier (prototype) as Server-Sent Events. GET-only
            (EventSource can't POST), so the target + profile ride as query params. The
            generator runs the real stages and yields SSE frames; a client disconnect
            mid-crawl surfaces as a broken pipe, which we swallow like the other paths."""
            from safeplate.dossier import iter_dossier_events, params_from_query

            params = params_from_query(urlparse(self.path).query)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")  # ask proxies not to buffer the stream
            self.send_header("Connection", "close")
            self._apply_security_headers()
            self.end_headers()
            try:
                for chunk in iter_dossier_events(params, demo_mode=demo_mode):
                    self._write_body(chunk.encode("utf-8"))
                    try:
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return  # client navigated away mid-crawl
            except Exception:
                self._log_internal_error("dossier")

        def _log_internal_error(self, where: str) -> None:
            # Log the detail server-side; never echo raw exception/upstream text to the
            # client (it can leak internal paths or upstream response bodies).
            import logging
            import traceback
            logging.getLogger("safeplate").error("%s failed:\n%s", where, traceback.format_exc())

        def _check_auth(self) -> bool:
            """Gate every route except /healthz behind HTTP Basic auth when a
            password is configured. We guard the top-level page too (not just the
            APIs): a 401 from fetch() won't open the browser's login dialog -- only
            a navigation does -- and once the page has prompted, same-origin API
            fetches reuse the cached credentials automatically."""
            if auth is None:
                return True
            header = self.headers.get("Authorization", "")
            if header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(header[6:]).decode("utf-8")
                except Exception:
                    decoded = ""
                user, _, password = decoded.partition(":")
                if hmac.compare_digest(user, auth[0]) and hmac.compare_digest(
                    password, auth[1]
                ):
                    return True
            # Failed (or absent) credentials: throttle per client to stop brute force.
            if not auth_fail_limiter.check(self._client_ip()):
                self.send_response(429)
                self._apply_security_headers()
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="SafePlate"')
            self._apply_security_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def _client_ip(self) -> str:
            """Real client IP for rate limiting. Only honour X-Forwarded-For when the
            operator has opted in (behind a trusted proxy), and then use the RIGHTMOST
            hop -- the address the trusted proxy actually observed -- because the
            leftmost hops are attacker-supplied and would let a spoofed header dodge
            the limiter entirely."""
            peer = self.client_address[0] if self.client_address else "unknown"
            if trust_xff:
                forwarded = self.headers.get("X-Forwarded-For", "").strip()
                if forwarded:
                    return forwarded.split(",")[-1].strip() or peer
            return peer

        def _apply_security_headers(self) -> None:
            for name, value in _SECURITY_HEADERS.items():
                self.send_header(name, value)

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except (TypeError, ValueError):
                raise ValueError("Invalid Content-Length header")
            if length < 0:
                raise ValueError("Invalid Content-Length header")
            if length > _MAX_BODY_BYTES:
                raise ValueError("Request body too large")
            raw = self.rfile.read(length).decode("utf-8")
            if not raw:
                return {}
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Request JSON must be an object")
            return payload

        def _write_body(self, encoded: bytes) -> None:
            # The client can disconnect mid-response (common on the slow menu-backed
            # path); a broken pipe here is expected, not a server error, so swallow it
            # instead of letting it surface as an unhandled traceback per dropped conn.
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _send_html(self, html: str, status: int = 200) -> None:
            encoded = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self._apply_security_headers()
            self.end_headers()
            self._write_body(encoded)

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self._apply_security_headers()
            self.end_headers()
            self._write_body(encoded)

    return SafePlateRequestHandler


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", ""}


def _assert_safe_to_bind(host: str) -> None:
    """Refuse to bind to a NON-loopback (publicly reachable) host without auth, so a
    misconfigured deploy can't silently come up wide open. Loopback (local dev) stays
    friction-free; set SAFEPLATE_ALLOW_OPEN=1 to bind public intentionally (e.g. behind
    your own gateway)."""
    if host in _LOOPBACK_HOSTS:
        return
    if _basic_auth_credentials() is not None:
        return
    if _truthy(os.environ.get("SAFEPLATE_ALLOW_OPEN")):
        return
    raise RuntimeError(
        f"Refusing to bind to non-loopback host {host!r} without SAFEPLATE_PASSWORD set. "
        "Set SAFEPLATE_PASSWORD (recommended) or SAFEPLATE_ALLOW_OPEN=1 to override."
    )


def run_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    demo_mode: bool = False,
) -> ThreadingHTTPServer:
    _assert_safe_to_bind(host)
    return ThreadingHTTPServer((host, port), create_app_handler(demo_mode=demo_mode))


_APP_TEMPLATE_PATH = Path(__file__).resolve().parent / "app_template.html"
_app_html_cache: dict[str, Any] = {"mtime": None, "html": ""}
_app_html_lock = threading.Lock()


def app_html() -> str:
    """Serve the page template, re-reading it when the file changes so edits show on
    a plain browser refresh -- no server restart needed. Only re-reads when the file's
    mtime changes (a cheap stat per request); on a transient read error (e.g. the file
    caught mid-save) it keeps serving the last good copy.

    The lock makes the stat/read/return atomic under ThreadingHTTPServer: without it a
    reader could observe a new mtime paired with the old html, and concurrent first
    requests would all re-read the file at once."""
    with _app_html_lock:
        try:
            mtime = _APP_TEMPLATE_PATH.stat().st_mtime
            if mtime != _app_html_cache["mtime"]:
                _app_html_cache["html"] = _APP_TEMPLATE_PATH.read_text(encoding="utf-8")
                _app_html_cache["mtime"] = mtime
        except OSError:
            pass  # keep serving the last good copy
        return _app_html_cache["html"]



def server_namespace(host: str, port: int) -> SimpleNamespace:
    return SimpleNamespace(host=host, port=port, url=f"http://{host}:{port}")
