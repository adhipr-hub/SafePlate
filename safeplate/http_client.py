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
try:
    import requests

    _HAS_REQUESTS = True
except ImportError:  # pragma: no cover - exercised only without requests
    requests = None  # type: ignore[assignment]
    _HAS_REQUESTS = False


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
# of hitting the network again. Keyed by user agent + URL.
_CACHE: "OrderedDict[str, HttpResponse]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX_ENTRIES = 256


def _session() -> "requests.Session":
    # One Session per thread keeps urllib3 connection pools thread-safe while
    # still reusing TCP/TLS connections across requests on the same thread.
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def http_get(
    url: str,
    *,
    user_agent: str,
    timeout: float = 30,
    use_cache: bool = False,
) -> HttpResponse:
    """Fetch a URL, reusing connections and decompressing gzip/deflate."""
    cache_key = f"{user_agent}\n{url}" if use_cache else None
    if cache_key is not None:
        with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
            if cached is not None:
                _CACHE.move_to_end(cache_key)
                return cached
        disk_cached = _disk_get(cache_key)  # opt-in, across separate runs
        if disk_cached is not None:
            with _CACHE_LOCK:
                _CACHE[cache_key] = disk_cached
                _CACHE.move_to_end(cache_key)
            return disk_cached

    if _HAS_REQUESTS:
        response = _http_get_requests(url, user_agent=user_agent, timeout=timeout)
    else:
        response = _http_get_urllib(url, user_agent=user_agent, timeout=timeout)

    if cache_key is not None:
        with _CACHE_LOCK:
            _CACHE[cache_key] = response
            _CACHE.move_to_end(cache_key)
            while len(_CACHE) > _CACHE_MAX_ENTRIES:
                _CACHE.popitem(last=False)
        _disk_put(cache_key, response)
    return response


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
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    try:
        response = _session().get(url, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as exc:  # type: ignore[union-attr]
        raise HttpConnectionError(f"Could not fetch {url}: {exc}") from exc

    if response.status_code >= 400:
        raise HttpError(
            response.status_code,
            f"HTTP {response.status_code} while fetching {url}",
        )
    return HttpResponse(
        status=response.status_code,
        final_url=response.url,
        content=response.content,
        content_type=response.headers.get("Content-Type", ""),
    )


def _http_get_urllib(url: str, *, user_agent: str, timeout: float) -> HttpResponse:
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
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
