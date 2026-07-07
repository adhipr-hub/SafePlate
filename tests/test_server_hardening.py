"""Server-tier hardening: rate-limit key can't be spoofed via X-Forwarded-For, POST
bodies are bounded, and a public bind without a password is refused."""
import io
import pytest

import safeplate.api_server as api_server


def _handler(monkeypatch, **env):
    for k in ("SAFEPLATE_TRUST_XFF", "SAFEPLATE_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    cls = api_server.create_app_handler()
    inst = cls.__new__(cls)  # bypass socket init
    inst.client_address = ("203.0.113.9", 5555)  # the real socket peer
    return inst


def test_client_ip_ignores_xff_by_default(monkeypatch):
    h = _handler(monkeypatch)
    h.headers = {"X-Forwarded-For": "1.1.1.1, 2.2.2.2"}
    assert h._client_ip() == "203.0.113.9"  # spoofed header NOT trusted


def test_client_ip_uses_rightmost_when_trusted(monkeypatch):
    h = _handler(monkeypatch, SAFEPLATE_TRUST_XFF="1")
    h.headers = {"X-Forwarded-For": "spoof1, spoof2, 9.9.9.9"}
    assert h._client_ip() == "9.9.9.9"  # the hop the trusted proxy appended


def test_read_json_rejects_oversized_body(monkeypatch):
    h = _handler(monkeypatch)
    h.headers = {"Content-Length": str(api_server._MAX_BODY_BYTES + 1)}
    h.rfile = io.BytesIO(b"")
    with pytest.raises(ValueError):
        h._read_json()


def test_read_json_rejects_negative_length(monkeypatch):
    h = _handler(monkeypatch)
    h.headers = {"Content-Length": "-5"}
    h.rfile = io.BytesIO(b"")
    with pytest.raises(ValueError):
        h._read_json()


def test_refuse_public_bind_without_password(monkeypatch):
    monkeypatch.delenv("SAFEPLATE_PASSWORD", raising=False)
    monkeypatch.delenv("SAFEPLATE_ALLOW_OPEN", raising=False)
    with pytest.raises(RuntimeError):
        api_server._assert_safe_to_bind("0.0.0.0")


def test_loopback_bind_is_allowed_without_password(monkeypatch):
    monkeypatch.delenv("SAFEPLATE_PASSWORD", raising=False)
    api_server._assert_safe_to_bind("127.0.0.1")  # must not raise


def test_public_bind_allowed_with_password(monkeypatch):
    monkeypatch.setenv("SAFEPLATE_PASSWORD", "hunter2hunter2")
    api_server._assert_safe_to_bind("0.0.0.0")  # must not raise


def _dossier_get(monkeypatch, **env):
    """Handler wired for GET-route tests: dossier handlers stubbed to record calls,
    _send_json stubbed to record response statuses."""
    h = _handler(monkeypatch, **env)
    h.headers = {}
    calls: list[str] = []
    h._handle_dossier_stream = lambda: calls.append("stream")
    h._handle_dossier_candidates = lambda: calls.append("candidates")
    statuses: list[int] = []
    h._send_json = lambda payload, status=200: statuses.append(status)
    return h, calls, statuses


def test_dossier_stream_is_rate_limited(monkeypatch):
    # /dossier/stream runs the same paid extraction pipeline as POST /api/menu, so it
    # must consult the same per-IP limiter instead of offering an unmetered side door.
    h, calls, statuses = _dossier_get(monkeypatch, SAFEPLATE_RATE_LIMIT_PER_MIN="1")
    h.path = "/dossier/stream?name=X&location=Y"
    h.do_GET()
    assert calls == ["stream"]
    h.do_GET()
    assert calls == ["stream"]  # second request must NOT reach the paid handler
    assert statuses == [429]


def test_dossier_candidates_counts_toward_daily_cap(monkeypatch):
    # /dossier/candidates hits Places search -- it must burn the same daily budget
    # as POST /api/search.
    h, calls, statuses = _dossier_get(
        monkeypatch,
        SAFEPLATE_DAILY_REQUEST_CAP="1",
        SAFEPLATE_RATE_LIMIT_PER_MIN="100",
    )
    h.path = "/dossier/candidates?q=pizza"
    h.do_GET()
    assert calls == ["candidates"]
    h.do_GET()
    assert calls == ["candidates"]  # capped out: paid handler not reached
    assert statuses == [429]
