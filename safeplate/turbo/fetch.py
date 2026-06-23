from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


CONNECT_TIMEOUT_S = _env_float("SAFEPLATE_TURBO_CONNECT_TIMEOUT_S", 3.0)
READ_TIMEOUT_S = _env_float("SAFEPLATE_TURBO_READ_TIMEOUT_S", 8.0)
MAX_DOWNLOAD_BYTES = _env_int("SAFEPLATE_TURBO_MAX_DOWNLOAD_BYTES", 8 * 1024 * 1024)


@dataclass(frozen=True)
class Fetched:
    url: str
    final_url: str
    status: int
    content_type: str
    content: bytes
    ok: bool
    error: str = ""


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "application/pdf;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }


async def _fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
) -> Fetched:
    try:
        async with sem:
            async with client.stream("GET", url) as resp:
                final_url = str(resp.url)
                status = resp.status_code
                content_type = resp.headers.get("content-type", "")
                chunks: list[bytes] = []
                total = 0
                aborted = False
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        aborted = True
                        break
                content = b"".join(chunks)
                if aborted:
                    content = content[:MAX_DOWNLOAD_BYTES]
                ok = status < 400
                error = "" if ok else f"HTTP {status}"
                return Fetched(
                    url=url,
                    final_url=final_url,
                    status=status,
                    content_type=content_type,
                    content=content,
                    ok=ok,
                    error=error,
                )
    except Exception as e:  # noqa: BLE001 - never raise; surface per-url error
        return Fetched(
            url=url,
            final_url=url,
            status=0,
            content_type="",
            content=b"",
            ok=False,
            error=str(e),
        )


async def _fetch_all(
    urls: list[str],
    *,
    user_agent: str,
    concurrency: int,
) -> list[Fetched]:
    timeout = httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        follow_redirects=True,
        http2=False,
        headers=_headers(user_agent),
        timeout=timeout,
    ) as client:
        tasks = [_fetch_one(client, sem, url) for url in urls]
        return await asyncio.gather(*tasks)


def fetch_all(
    urls,
    *,
    user_agent: str,
    concurrency: int = 12,
) -> list[Fetched]:
    url_list = list(urls)
    if not url_list:
        return []
    return asyncio.run(
        _fetch_all(url_list, user_agent=user_agent, concurrency=concurrency)
    )
