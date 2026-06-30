from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import gzip
import hashlib
import json
from pathlib import Path
import threading
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Prefer requests for keep-alive connection pooling and transparent gzip when
# it is installed. Fall back to urllib (with manual gzip handling) so SafePlate
# still runs on a pure-stdlib environment.
from safeplate.net_guard import BlockedUrlError, assert_public_url

try:
    import requests

    _HAS_REQUESTS = True
except ImportError:  # pragma: no cover - exercised only without requests
    requests = None  # type: ignore[assignment]
    _HAS_REQUESTS = False


if _HAS_REQUESTS:
    class _GuardedHTTPAdapter(requests.adapters.HTTPAdapter):
        """SSRF guard at the transport layer: ``Session.send`` re-invokes the adapter
        for EVERY redirect hop, so validating ``request.url`` here blocks both a
        direct internal URL and a public page that redirects to an internal one."""

        def send(self, request, *args, **kwargs):  # type: ignore[override]
            try:
                assert_public_url(request.url)
            except BlockedUrlError as exc:
                raise requests.exceptions.ConnectionError(str(exc)) from exc
            return super().send(request, *args, **kwargs)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    final_url: str
    content: bytes
    content_type: str


class HttpError(RuntimeError):
    """Raised for HTTP responses with a >= 400 status code."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class HttpConnectionError(RuntimeError):
    """Raised when a request could not complete (DNS, timeout, TLS, etc.)."""


_thread_local = threading.local()

# Bounded, process-wide cache of GET responses. This lets multiple pipeline
# stages that run in the same process (e.g. menu-text and menu-item extraction,
# or the local app's discovery + extraction) reuse a page fetched once instead
# of hitting the network again. Keyed by user agent + URL. Each entry stores its
# fetch time so a long-running server can expire stale pages (see
# config.get_http_memory_cache_ttl) rather than serving startup data forever.
_CACHE: "OrderedDict[str, tuple[float, HttpResponse]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX_ENTRIES = 256


def _memory_cache_ttl() -> int:
    from safeplate.config import get_http_memory_cache_ttl

    return get_http_memory_cache_ttl()


def _session() -> "requests.Session":
    # One Session per thread keeps urllib3 connection pools thread-safe while
    # still reusing TCP/TLS connections across requests on the same thread.
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        # Size the pool to our concurrency. The default urllib3 pool_maxsize=10
        # and pool_connections=10 can evict warm host pools when a restaurant
        # fans many fetches + Gemini/Brave POSTs across a few hosts, forcing a
        # fresh TLS handshake. pool_block=True makes a saturated worker wait for a
        # warm socket instead of churning one. Same URLs/bytes -> output-identical.
        from safeplate.config import get_fetch_concurrency, get_gemini_concurrency

        size = max(get_fetch_concurrency(), get_gemini_concurrency(), 16)
        adapter = _GuardedHTTPAdapter(
            pool_connections=size, pool_maxsize=size, pool_block=True
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


def http_get(
    url: str,
    *,
    user_agent: str,
    timeout: float = 30,
    use_cache: bool = False,
) -> HttpResponse:
    """Fetch a URL, reusing connections and decompressing gzip/deflate.

    SSRF guard: the URL (and every redirect hop, via ``_GuardedHTTPAdapter``) must
    target a public host over http(s); a blocked URL raises ``HttpConnectionError``
    so callers treat it as an ordinary failed fetch (no data returned)."""
    try:
        assert_public_url(url)
    except BlockedUrlError as exc:
        raise HttpConnectionError(str(exc)) from exc
    cache_key = f"{user_agent}\n{url}" if use_cache else None
    if cache_key is not None:
        ttl = _memory_cache_ttl()
        with _CACHE_LOCK:
            entry = _CACHE.get(cache_key)
            if entry is not None:
                fetched_at, cached = entry
                if ttl <= 0 or (time.time() - fetched_at) <= ttl:
                    _CACHE.move_to_end(cache_key)
                    return cached
                del _CACHE[cache_key]  # expired -> refetch below
        disk_cached = _disk_get(cache_key)  # opt-in, across separate runs
        if disk_cached is not None:
            with _CACHE_LOCK:
                _cache_store(cache_key, disk_cached)
            return disk_cached

    from safeplate.timing import span

    with span("http_get"):
        if _HAS_REQUESTS:
            response = _http_get_requests(url, user_agent=user_agent, timeout=timeout)
        else:
            response = _http_get_urllib(url, user_agent=user_agent, timeout=timeout)

    if cache_key is not None:
        with _CACHE_LOCK:
            _cache_store(cache_key, response)
        _disk_put(cache_key, response)
    return response


def _cache_store(cache_key: str, response: "HttpResponse") -> None:
    """Insert/refresh an entry (timestamped) and evict LRU overflow. Caller holds
    ``_CACHE_LOCK``."""
    _CACHE[cache_key] = (time.time(), response)
    _CACHE.move_to_end(cache_key)
    while len(_CACHE) > _CACHE_MAX_ENTRIES:
        _CACHE.popitem(last=False)


def _disk_paths(cache_key: str) -> "tuple[Path, Path]":
    from safeplate.config import get_cache_dir

    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
    base = get_cache_dir() / "http"
    return base / f"{digest}.meta", base / f"{digest}.body"


def _disk_get(cache_key: str) -> "HttpResponse | None":
    from safeplate.config import get_http_cache_ttl

    ttl = get_http_cache_ttl()
    if ttl <= 0:
        return None
    meta_path, body_path = _disk_paths(cache_key)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if time.time() - float(meta.get("fetched_at", 0)) > ttl:
            return None
        body = body_path.read_bytes()
    except (OSError, ValueError):
        return None
    return HttpResponse(
        status=int(meta.get("status", 200)),
        final_url=str(meta.get("final_url", "")),
        content=body,
        content_type=str(meta.get("content_type", "")),
    )


def _disk_put(cache_key: str, response: "HttpResponse") -> None:
    from safeplate.config import get_http_cache_ttl

    if get_http_cache_ttl() <= 0:
        return
    meta_path, body_path = _disk_paths(cache_key)
    try:
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(response.content)
        meta_path.write_text(
            json.dumps({
                "fetched_at": time.time(),
                "status": response.status,
                "final_url": response.final_url,
                "content_type": response.content_type,
            }),
            encoding="utf-8",
        )
    except OSError:
        pass


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _http_get_requests(
    url: str, *, user_agent: str, timeout: float
) -> HttpResponse:
    from safeplate.config import get_connect_timeout, get_max_download_bytes

    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        # Real clients always send Accept / Accept-Language; some bot-protection (e.g.
        # Toast's order pages, which are robots-allowed) 403s requests that omit them.
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    # Split connect vs read: a short connect timeout fails fast on dead/blocking hosts
    # (the common slow case) while the read timeout stays generous for real downloads.
    try:
        response = _session().get(
            url, headers=headers, timeout=(get_connect_timeout(), timeout), stream=True
        )
    except requests.exceptions.RequestException as exc:  # type: ignore[union-attr]
        raise HttpConnectionError(f"Could not fetch {url}: {exc}") from exc

    try:
        if response.status_code >= 400:
            raise HttpError(
                response.status_code,
                f"HTTP {response.status_code} while fetching {url}",
            )
        # Stream with a size cap AND a TOTAL-time deadline. requests' read timeout is
        # per socket-read, so a server that dribbles bytes can keep a connection alive
        # far past it; enforce wall-clock here so one slow-trickle site can't eat the
        # whole per-restaurant budget.
        max_bytes = get_max_download_bytes()
        deadline = time.monotonic() + timeout
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(65536):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break  # size cap
                if time.monotonic() > deadline:
                    raise HttpConnectionError(
                        f"Read exceeded {timeout:.0f}s (slow trickle) for {url}"
                    )
        except requests.exceptions.RequestException as exc:  # type: ignore[union-attr]
            raise HttpConnectionError(f"Could not fetch {url}: {exc}") from exc
        return HttpResponse(
            status=response.status_code,
            final_url=response.url,
            content=b"".join(chunks),
            content_type=response.headers.get("Content-Type", ""),
        )
    finally:
        response.close()


def http_post(
    url: str,
    *,
    data: bytes,
    headers: dict[str, str],
    timeout: float = 90,
) -> HttpResponse:
    """POST bytes over a pooled keep-alive connection (gzip-negotiated).

    JSON APIs like Gemini and Brave are called many times against a single host
    in one run, so reusing the thread-local ``requests`` Session avoids a fresh
    TLS handshake per call. Unlike :func:`http_get`, this does NOT raise on HTTP
    >= 400 — those APIs return useful error bodies that callers surface in their
    own exceptions, so status + body are handed back as-is. Only transport
    failures raise (:class:`HttpConnectionError`).
    """
    if _HAS_REQUESTS:
        return _http_post_requests(url, data=data, headers=headers, timeout=timeout)
    return _http_post_urllib(url, data=data, headers=headers, timeout=timeout)


def _http_post_requests(
    url: str, *, data: bytes, headers: dict[str, str], timeout: float
) -> HttpResponse:
    merged = {"Accept-Encoding": "gzip, deflate", **headers}
    try:
        response = _session().post(url, data=data, headers=merged, timeout=timeout)
    except requests.exceptions.RequestException as exc:  # type: ignore[union-attr]
        raise HttpConnectionError(f"Could not POST {url}: {exc}") from exc
    return HttpResponse(
        status=response.status_code,
        final_url=response.url,
        content=response.content,
        content_type=response.headers.get("Content-Type", ""),
    )


def _http_post_urllib(
    url: str, *, data: bytes, headers: dict[str, str], timeout: float
) -> HttpResponse:
    merged = {"Accept-Encoding": "gzip, deflate", **headers}
    request = Request(url, data=data, headers=merged, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            content_encoding = (response.headers.get("Content-Encoding") or "").lower()
            status = getattr(response, "status", 200) or 200
            final_url = response.url
    except HTTPError as exc:
        # 4xx/5xx still carry a useful JSON body; hand it back instead of raising
        # so callers can build their own error message (matches the requests path).
        encoding = (exc.headers.get("Content-Encoding") or "").lower() if exc.headers else ""
        return HttpResponse(
            status=exc.code,
            final_url=url,
            content=_decompress(exc.read(), encoding),
            content_type=exc.headers.get("Content-Type", "") if exc.headers else "",
        )
    except (URLError, TimeoutError) as exc:
        raise HttpConnectionError(f"Could not POST {url}: {exc}") from exc

    return HttpResponse(
        status=status,
        final_url=final_url,
        content=_decompress(raw, content_encoding),
        content_type=content_type,
    )


def _http_get_urllib(url: str, *, user_agent: str, timeout: float) -> HttpResponse:
    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        # Real clients always send Accept / Accept-Language; some bot-protection (e.g.
        # Toast's order pages, which are robots-allowed) 403s requests that omit them.
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            content_encoding = (response.headers.get("Content-Encoding") or "").lower()
            final_url = response.url
            status = getattr(response, "status", 200) or 200
    except HTTPError as exc:
        raise HttpError(exc.code, f"HTTP {exc.code} while fetching {url}") from exc
    except (URLError, TimeoutError) as exc:
        raise HttpConnectionError(f"Could not fetch {url}: {exc}") from exc

    return HttpResponse(
        status=status,
        final_url=final_url,
        content=_decompress(raw, content_encoding),
        content_type=content_type,
    )


def _decompress(raw: bytes, content_encoding: str) -> bytes:
    if "gzip" in content_encoding:
        try:
            return gzip.decompress(raw)
        except (OSError, EOFError):
            return raw
    if "deflate" in content_encoding:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                return raw
    return raw
