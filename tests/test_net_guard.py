"""SSRF egress guard: the server fetches client-supplied URLs, so it must refuse to
reach private/loopback/link-local/cloud-metadata addresses or non-http(s) schemes."""
import pytest

from safeplate.net_guard import assert_public_url, BlockedUrlError


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://127.0.0.1:8765/admin",                # loopback
    "http://[::1]/",                               # IPv6 loopback
    "http://10.0.0.5/menu",                        # RFC1918
    "http://192.168.1.1/",                         # RFC1918
    "http://172.16.0.9/",                          # RFC1918
    "https://0.0.0.0/",                            # unspecified
    "http://[::ffff:127.0.0.1]/",                  # IPv4-mapped loopback
    "file:///etc/passwd",                          # non-http scheme
    "gopher://127.0.0.1/",                         # non-http scheme
    "ftp://10.0.0.1/",                             # non-http scheme
    "http:///nohost",                             # missing host
])
def test_blocks_private_and_bad_schemes(url):
    with pytest.raises(BlockedUrlError):
        assert_public_url(url)


@pytest.mark.parametrize("url", [
    "http://8.8.8.8/",            # public IP (no DNS needed)
    "https://93.184.216.34/menu", # public IP
])
def test_allows_public(url):
    assert assert_public_url(url) == url
