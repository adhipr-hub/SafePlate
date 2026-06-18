from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import threading
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

from safeplate.config import get_cache_dir
from safeplate.http_client import HttpConnectionError, HttpError, http_get


@dataclass(frozen=True)
class RobotsDecision:
    allowed: bool
    reason: str


# Outcome kinds we persist so a parser can be rebuilt without re-fetching.
_KIND_CONTENT = "content"
_KIND_ALLOW_ALL = "allow_all"
_KIND_DISALLOW_ALL = "disallow_all"

_ROBOTS_CACHE: dict[str, urllib.robotparser.RobotFileParser] = {}
_CACHE_LOCK = threading.Lock()
_DISK_TTL_SECONDS = 24 * 60 * 60


def can_fetch_url(url: str, *, user_agent: str) -> RobotsDecision:
    parsed = urlparse(url)
    if parsed.scheme not in ["http", "https"] or not parsed.netloc:
        return RobotsDecision(False, f"not an HTTP URL: {url}")

    base_url = f"{parsed.scheme}://{parsed.netloc}"
    parser = _get_parser(base_url, user_agent=user_agent)

    if parser.can_fetch(user_agent, url):
        return RobotsDecision(True, f"allowed by {base_url}/robots.txt")
    return RobotsDecision(False, f"disallowed by {base_url}/robots.txt")


def _get_parser(
    base_url: str, *, user_agent: str
) -> urllib.robotparser.RobotFileParser:
    # Fast path: already resolved in this process.
    parser = _ROBOTS_CACHE.get(base_url)
    if parser is not None:
        return parser

    with _CACHE_LOCK:
        parser = _ROBOTS_CACHE.get(base_url)
        if parser is not None:
            return parser

        parser = _load_from_disk(base_url)
        if parser is None:
            parser = _load_robots_parser(base_url, user_agent=user_agent)
        _ROBOTS_CACHE[base_url] = parser
        return parser


def _load_robots_parser(
    base_url: str,
    *,
    user_agent: str,
) -> urllib.robotparser.RobotFileParser:
    robots_url = f"{base_url}/robots.txt"
    parser = urllib.robotparser.RobotFileParser(robots_url)

    try:
        response = http_get(robots_url, user_agent=user_agent, timeout=15)
        content = response.content.decode("utf-8", errors="replace")
    except HttpError as exc:
        if exc.status == 404:
            parser.parse([])
            _save_to_disk(base_url, _KIND_ALLOW_ALL, "")
            return parser
        if exc.status in [401, 403] or exc.status >= 500:
            parser.disallow_all = True
            _save_to_disk(base_url, _KIND_DISALLOW_ALL, "")
            return parser
        parser.parse([])
        _save_to_disk(base_url, _KIND_ALLOW_ALL, "")
        return parser
    except HttpConnectionError:
        parser.disallow_all = True
        # Do not persist transient connection failures; retry next run.
        return parser

    parser.parse(content.splitlines())
    _save_to_disk(base_url, _KIND_CONTENT, content)
    return parser


def _parser_from_outcome(
    base_url: str, kind: str, content: str
) -> urllib.robotparser.RobotFileParser:
    parser = urllib.robotparser.RobotFileParser(f"{base_url}/robots.txt")
    if kind == _KIND_DISALLOW_ALL:
        parser.disallow_all = True
    elif kind == _KIND_CONTENT:
        parser.parse(content.splitlines())
    else:
        parser.parse([])
    return parser


def _cache_path(base_url: str) -> Path:
    digest = hashlib.sha1(base_url.encode("utf-8")).hexdigest()
    return get_cache_dir() / "robots" / f"{digest}.json"


def _load_from_disk(base_url: str) -> urllib.robotparser.RobotFileParser | None:
    path = _cache_path(base_url)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    fetched_at = payload.get("fetched_at", 0)
    if not isinstance(fetched_at, (int, float)):
        return None
    if time.time() - fetched_at > _DISK_TTL_SECONDS:
        return None

    return _parser_from_outcome(
        base_url,
        str(payload.get("kind", _KIND_ALLOW_ALL)),
        str(payload.get("content", "")),
    )


def _save_to_disk(base_url: str, kind: str, content: str) -> None:
    path = _cache_path(base_url)
    payload = {"fetched_at": time.time(), "kind": kind, "content": content}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        # Caching is best-effort; never fail a fetch because the cache is unwritable.
        pass
