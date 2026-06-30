"""SSRF egress guard.

SafePlate fetches URLs it did not author -- a restaurant ``websiteUrl`` from the
request body, links harvested off pages, Brave web-search results, off-site PDFs.
Without a guard, a caller can point any of those at ``169.254.169.254`` (cloud
metadata -> credential theft), ``127.0.0.1`` (the app's own admin surface), or any
RFC1918 host, and -- because the extracted bytes are reflected back to the user --
read the response. This module is the single validator every outbound fetch of a
non-API URL must pass; it is enforced inside ``http_client`` (initial URL + every
redirect hop via a guarded adapter) and at the headless-browser entry point.

Residual: a DNS-rebind between this check and the socket connect (TOCTOU) is not
closed here (would require pinning the resolved IP onto the connection); the literal
private-address and redirect-to-internal vectors -- the reported P0/P1 -- are.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


class BlockedUrlError(RuntimeError):
    """Raised when a URL targets a non-public address or a disallowed scheme."""


def _ip_is_blocked(raw_ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(raw_ip)
    except ValueError:
        return True  # unparseable -> refuse
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) so the v4 rules apply.
    if addr.version == 6 and getattr(addr, "ipv4_mapped", None) is not None:
        addr = addr.ipv4_mapped
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def assert_public_url(url: str) -> str:
    """Return ``url`` unchanged if it targets a PUBLIC host over http(s); otherwise
    raise :class:`BlockedUrlError`. Resolves the host and rejects if ANY resolved
    address is private/loopback/link-local/reserved/multicast/unspecified."""
    parsed = urlparse(url or "")
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise BlockedUrlError(f"blocked URL scheme: {parsed.scheme!r} in {url!r}")
    host = parsed.hostname
    if not host:
        raise BlockedUrlError(f"URL has no host: {url!r}")
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BlockedUrlError(f"cannot resolve host {host!r}") from exc
    resolved = {info[4][0] for info in infos}
    if not resolved:
        raise BlockedUrlError(f"host {host!r} resolved to no address")
    for ip in resolved:
        if _ip_is_blocked(ip):
            raise BlockedUrlError(f"blocked non-public address {ip} for host {host!r}")
    return url
