"""The backend client's proxy comes from the environment.

httpx reads HTTPS_PROXY/ALL_PROXY only when it builds the transport itself
(`allow_env_proxies = trust_env and transport is None`), and this client names
a transport to get retries. The proxy is therefore passed explicitly, and these
tests pin that: a worker behind a SOCKS forward is the case that fails silently
otherwise, resolving the backend hostname on a host with no DNS for it.
"""

import httpx
import pytest

from daemon.client import BackendClient, _env_proxy


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch):
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


def _proxy_url(client: httpx.AsyncClient):
    """The proxy origin the client's transport dials, or None."""
    pool = client._transport._pool
    origin = getattr(pool, "_proxy_url", None) or getattr(pool, "_proxy_origin", None)
    return origin


def test_no_proxy_env_means_direct():
    client = BackendClient("https://api.example.edu", "w-1")._get_client()
    assert _proxy_url(client) is None


def test_all_proxy_is_used(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example.edu:3128")
    client = BackendClient("https://api.example.edu", "w-1")._get_client()
    assert _proxy_url(client) is not None


def test_socks_proxy_is_usable(monkeypatch):
    """The SSH-forward case: `socksio` ships with the daemon, so this builds."""
    monkeypatch.setenv("ALL_PROXY", "socks5h://127.0.0.1:1080")
    client = BackendClient("https://api.example.edu", "w-1")._get_client()
    assert _proxy_url(client) is not None


def test_https_proxy_wins_over_all_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5h://127.0.0.1:1080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.edu:3128")
    assert _env_proxy() == "http://proxy.example.edu:3128"


def test_empty_value_is_not_a_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "")
    assert _env_proxy() is None
