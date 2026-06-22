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
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - self._window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True


def create_app_handler(*, demo_mode: bool = False) -> type[BaseHTTPRequestHandler]:
    auth = _basic_auth_credentials()
    rate_limiter = _RateLimiter(
        max_requests=_int_env("SAFEPLATE_RATE_LIMIT_PER_MIN", 20),
        window_seconds=60.0,
    )

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
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(response)

        def _handle_menu(self) -> None:
            try:
                payload = self._read_json()
                response = run_menu_extraction(payload, demo_mode=demo_mode)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(response)

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
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="SafePlate"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def _client_ip(self) -> str:
            """Real client IP for rate limiting. Behind Render's proxy the socket
            peer is the proxy, so trust the first hop of X-Forwarded-For."""
            forwarded = self.headers.get("X-Forwarded-For", "").strip()
            if forwarded:
                return forwarded.split(",")[0].strip()
            return self.client_address[0] if self.client_address else "unknown"

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8")
            if not raw:
                return {}
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Request JSON must be an object")
            return payload

        def _send_html(self, html: str, status: int = 200) -> None:
            encoded = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return SafePlateRequestHandler


def run_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    demo_mode: bool = False,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), create_app_handler(demo_mode=demo_mode))


_APP_TEMPLATE_PATH = Path(__file__).resolve().parent / "app_template.html"
_app_html_cache: dict[str, Any] = {"mtime": None, "html": ""}


def app_html() -> str:
    """Serve the page template, re-reading it when the file changes so edits show on
    a plain browser refresh -- no server restart needed. Only re-reads when the file's
    mtime changes (a cheap stat per request); on a transient read error (e.g. the file
    caught mid-save) it keeps serving the last good copy."""
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
