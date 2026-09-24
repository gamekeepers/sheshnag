"""The backend client's proxy comes from the environment.

httpx reads proxy variables only when it builds the transport itself
(`trust_env` and no explicit transport), and this client names a transport
to get retries. The proxy is therefore passed explicitly, and these tests
pin what that selection means by dialing what the client actually dials:
a local TCP listener stands in for the proxy, a local HTTP server stands
in for the control plane. A worker behind a SOCKS forward is the case that
fails silently otherwise, resolving the backend hostname on a host with no
DNS for it.
"""

import asyncio
import http.server
import threading

import httpx
import pytest

from daemon.client import BackendClient, _env_proxy


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch):
    for name in (
        "HTTP_PROXY", "http_proxy",
        "HTTPS_PROXY", "https_proxy",
        "ALL_PROXY", "all_proxy",
        "NO_PROXY", "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


class _DialProbe:
    """A TCP server that records what each connection sends first.

    Stands in for a proxy: whatever bytes arrive are the proof of which
    host the client dialed, without reaching into httpcore's internals.
    """

    def __init__(self):
        self.seen: list[bytes] = []
        self._server: asyncio.Server | None = None

    @property
    def url(self) -> str:
        host, port = self._server.sockets[0].getsockname()[:2]
        return f"http://{host}:{port}"

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def _handle(self, reader, writer):
        try:
            data = await asyncio.wait_for(reader.read(256), timeout=5.0)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            data = b""
        self.seen.append(data)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()


def _make_poll_handler(hits: list):
    class _PollHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(204)  # 204 = no job available
            self.end_headers()
            hits.append(self.path)

        def log_message(self, format, *args):
            pass

    return _PollHandler


class _Backend:
    """A stand-in control plane: 204 No Content on POST /workers/poll."""

    def __init__(self):
        self.hits: list[str] = []

    def __enter__(self):
        self._httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _make_poll_handler(self.hits)
        )
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()


# ── End-to-end: what the client actually dials ────────────────────


@pytest.mark.asyncio
async def test_no_proxy_env_means_direct():
    """No proxy configured → the control plane is reached directly."""
    with _Backend() as backend:
        client = BackendClient(backend.url, "w-1")
        assert await client.poll_job() is None
    assert backend.hits == ["/workers/poll"]


@pytest.mark.asyncio
async def test_all_proxy_is_used(monkeypatch):
    """ALL_PROXY set → the client dials the proxy (CONNECT), not the backend."""
    async with _DialProbe() as probe:
        monkeypatch.setenv("ALL_PROXY", probe.url)
        client = BackendClient("https://api.example.edu", "w-1")
        with pytest.raises(httpx.TransportError):
            await client.poll_job()
    assert any(b"CONNECT api.example.edu:443" in data for data in probe.seen)


@pytest.mark.asyncio
async def test_http_proxy_is_used_for_http_backend(monkeypatch):
    """HTTP_PROXY applies to a plain-http control plane."""
    async with _DialProbe() as probe:
        monkeypatch.setenv("HTTP_PROXY", probe.url)
        client = BackendClient("http://api.example.edu", "w-1")
        with pytest.raises(httpx.TransportError):
            await client.poll_job()
    assert any(
        b"POST http://api.example.edu/workers/poll HTTP/1.1" in data
        for data in probe.seen
    )


@pytest.mark.asyncio
async def test_https_proxy_is_ignored_for_http_backend(monkeypatch):
    """HTTPS_PROXY must not route a plain-http control plane through the
    proxy — it goes direct, like curl and requests do."""
    with _Backend() as backend:
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.edu:3128")
        client = BackendClient(backend.url, "w-1")
        assert await client.poll_job() is None
    assert backend.hits == ["/workers/poll"]


@pytest.mark.asyncio
async def test_no_proxy_bypasses_proxy_for_backend(monkeypatch):
    """NO_PROXY naming the backend host → direct, even with a proxy set."""
    with _Backend() as backend:
        async with _DialProbe() as probe:
            monkeypatch.setenv("ALL_PROXY", probe.url)
            monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
            client = BackendClient(backend.url, "w-1")
            assert await client.poll_job() is None
    assert probe.seen == []


@pytest.mark.asyncio
async def test_socks_proxy_is_usable(monkeypatch):
    """The SSH-forward case: `socksio` ships with the daemon, so a socks5h
    proxy builds and dials the forward."""
    async with _DialProbe() as probe:
        monkeypatch.setenv("ALL_PROXY", probe.url.replace("http://", "socks5h://"))
        client = BackendClient("https://api.example.edu", "w-1")
        # The probe closes the connection mid-handshake, so the request
        # fails — the point is that the SOCKS5 greeting reached the forward.
        with pytest.raises(Exception):
            await client.poll_job()
    # A SOCKS5 greeting starts with the version byte 5; nothing else
    # dials the probe in this test, so that proves the forward was used.
    assert any(data.startswith(b"\x05") for data in probe.seen)


# ── Selection rules (pure) ────────────────────────────────────────


def test_scheme_specific_variable_wins(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://http-proxy.example.edu:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://https-proxy.example.edu:3128")
    assert _env_proxy("http://api.example.edu") == "http://http-proxy.example.edu:3128"
    assert _env_proxy("https://api.example.edu") == "http://https-proxy.example.edu:3128"


def test_https_proxy_wins_over_all_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5h://127.0.0.1:1080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.edu:3128")
    assert _env_proxy("https://api.example.edu") == "http://proxy.example.edu:3128"


def test_all_proxy_covers_both_schemes(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5h://127.0.0.1:1080")
    assert _env_proxy("http://api.example.edu") == "socks5h://127.0.0.1:1080"
    assert _env_proxy("https://api.example.edu") == "socks5h://127.0.0.1:1080"


def test_empty_value_is_not_a_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "")
    assert _env_proxy("https://api.example.edu") is None


@pytest.mark.parametrize(
    ("no_proxy", "url", "expected"),
    [
        ("api.example.edu", "https://api.example.edu", None),
        ("example.edu", "https://api.example.edu", None),
        (".example.edu", "https://api.example.edu", None),
        # ".example.edu" is subdomains only — the bare domain still proxies
        (".example.edu", "https://example.edu", "http://proxy:3128"),
        # label-boundary match: "other.example.edu" does not cover api.example.edu
        ("other.example.edu", "https://api.example.edu", "http://proxy:3128"),
        ("*", "https://api.example.edu", None),
        ("127.0.0.1, localhost", "http://127.0.0.1:8000", None),
    ],
)
def test_no_proxy_matching(monkeypatch, no_proxy, url, expected):
    monkeypatch.setenv("ALL_PROXY", "http://proxy:3128")
    monkeypatch.setenv("NO_PROXY", no_proxy)
    assert _env_proxy(url) == expected


def test_lowercase_no_proxy_also_honoured(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "http://proxy:3128")
    monkeypatch.setenv("no_proxy", "api.example.edu")
    assert _env_proxy("https://api.example.edu") is None
