"""OPT-3: the per-thread requests Session should mount an HTTPAdapter sized to our
concurrency so keep-alive connections survive a restaurant's many fetches + Gemini
POSTs to a few hosts, instead of inheriting urllib3's default pool_maxsize=10 and
churning TCP/TLS. Pure transport reuse -- output-identical."""

import pytest

requests = pytest.importorskip("requests")

from safeplate import http_client


def _fresh_session():
    # Drop any cached thread-local session so we build a new one.
    if hasattr(http_client._thread_local, "session"):
        del http_client._thread_local.session
    return http_client._session()


def test_session_mounts_sized_blocking_pool_adapter():
    session = _fresh_session()
    for prefix in ("https://", "http://"):
        adapter = session.get_adapter(prefix)
        assert adapter._pool_maxsize >= 12, prefix
        assert adapter._pool_block is True, prefix
