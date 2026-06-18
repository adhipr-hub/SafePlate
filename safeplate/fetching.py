from __future__ import annotations

from safeplate.http_client import HttpConnectionError, HttpError, http_get
from safeplate.robots import can_fetch_url


def fetch_url_bytes(
    url: str,
    *,
    user_agent: str,
    error_cls: type[RuntimeError],
    timeout: float = 30,
    use_cache: bool = True,
) -> tuple[bytes, str]:
    robots_decision = can_fetch_url(url, user_agent=user_agent)
    if not robots_decision.allowed:
        raise error_cls(f"Blocked by robots.txt: {robots_decision.reason}")

    try:
        response = http_get(
            url,
            user_agent=user_agent,
            timeout=timeout,
            use_cache=use_cache,
        )
    except (HttpError, HttpConnectionError) as exc:
        raise error_cls(str(exc)) from exc

    return response.content, response.content_type
