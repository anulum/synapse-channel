# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — SSRF-resistant webhook transport tests

from __future__ import annotations

import socket
import ssl
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request

import pytest

from synapse_channel import safe_webhook_transport as transport


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("2606:4700:4700::1111", True),
        ("::ffff:8.8.8.8", True),
        ("127.0.0.1", False),
        ("10.0.0.1", False),
        ("192.168.1.5", False),
        ("169.254.1.1", False),
        ("100.64.0.1", False),
        ("224.0.0.1", False),
        ("240.0.0.1", False),
        ("0.0.0.0", False),
        ("::1", False),
        ("fe80::1", False),
        ("::ffff:127.0.0.1", False),
        ("fe80::1%eth0", False),
        ("not-an-ip", False),
    ],
)
def test_is_public_address_admits_only_globally_routable(address: str, public: bool) -> None:
    assert transport.is_public_address(address) is public


def _patch_getaddrinfo(monkeypatch: pytest.MonkeyPatch, answers: object) -> None:
    monkeypatch.setattr("synapse_channel.safe_webhook_transport.socket.getaddrinfo", answers)


def test_resolve_pinned_endpoints_returns_every_public_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_getaddrinfo(
        monkeypatch,
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
        ],
    )
    assert transport.resolve_pinned_endpoints("host.invalid", 443, allow_local=False) == [
        "8.8.8.8",
        "1.1.1.1",
    ]


def test_resolve_pinned_endpoints_rejects_any_non_public_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_getaddrinfo(
        monkeypatch,
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(URLError, match="must not target local networks"):
        transport.resolve_pinned_endpoints("mixed.invalid", 443, allow_local=False)


def test_resolve_pinned_endpoints_allows_local_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_getaddrinfo(
        monkeypatch,
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )
    assert transport.resolve_pinned_endpoints("localhost", 80, allow_local=True) == ["127.0.0.1"]


def test_resolve_pinned_endpoints_wraps_resolver_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> list[Any]:
        raise OSError("no such host")

    _patch_getaddrinfo(monkeypatch, boom)
    with pytest.raises(URLError, match="could not resolve webhook target"):
        transport.resolve_pinned_endpoints("nx.invalid", 80, allow_local=False)


def test_resolve_pinned_endpoints_rejects_empty_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_getaddrinfo(
        monkeypatch,
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ())],
    )
    with pytest.raises(URLError, match="could not resolve webhook target"):
        transport.resolve_pinned_endpoints("empty.invalid", 80, allow_local=False)


def test_open_pinned_socket_falls_back_to_the_next_address() -> None:
    with _Receiver() as receiver:
        # 127.0.0.2 is loopback with nothing listening; the fallback reaches the
        # server on 127.0.0.1 without re-resolving.
        sock = transport._open_pinned_socket(["127.0.0.2", "127.0.0.1"], receiver.port, 2.0)
        sock.close()


def test_open_pinned_socket_raises_when_no_address_is_reachable() -> None:
    port = _free_port()
    with pytest.raises(OSError):
        transport._open_pinned_socket(["127.0.0.2", "127.0.0.3"], port, 2.0)


def test_safe_opener_delivers_to_a_pinned_public_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    with _Receiver() as receiver:
        original = socket.getaddrinfo

        def resolve(host: str, port: int, *_a: object, **_k: object) -> list[Any]:
            calls.append(host)
            if host == "pinned.test":
                # First resolution is loopback; any later resolution would rebind
                # to an unroutable address — pinning must connect to the first.
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
            return original(host, port, type=socket.SOCK_STREAM)

        monkeypatch.setattr("synapse_channel.safe_webhook_transport.socket.getaddrinfo", resolve)
        opener = transport.build_safe_opener(allow_local=True)
        req = Request(
            f"http://pinned.test:{receiver.port}/hook",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=2.0) as response:
            transport.read_bounded(response)

    assert receiver.paths == ["/hook"]
    assert calls.count("pinned.test") == 1


def test_safe_opener_follows_a_307_redirect_and_preserves_the_post(
    tmp_path: Path,
) -> None:
    with _Receiver() as receiver:
        with _Redirect(location=f"http://localhost:{receiver.port}/final", code=307) as proxy:
            opener = transport.build_safe_opener(allow_local=True)
            req = Request(
                f"http://localhost:{proxy.port}/start",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(req, timeout=2.0) as response:
                transport.read_bounded(response)

    assert proxy.paths == ["/start"]
    assert receiver.paths == ["/final"]
    assert receiver.methods == ["POST"]


def test_safe_opener_follows_a_plain_302_redirect() -> None:
    with _Receiver() as receiver:
        with _Redirect(location=f"http://localhost:{receiver.port}/moved", code=302) as proxy:
            opener = transport.build_safe_opener(allow_local=True)
            with opener.open(f"http://localhost:{proxy.port}/start", timeout=2.0) as response:
                transport.read_bounded(response)

    assert receiver.paths == ["/moved"]
    assert receiver.methods == ["GET"]


@pytest.mark.parametrize("code", [307, 308])
def test_authenticated_same_origin_redirect_preserves_post_and_sensitive_header(
    code: int,
) -> None:
    with _Redirect(location="/final", code=code) as endpoint:
        opener = transport.build_safe_opener(allow_local=True)
        req = Request(
            f"{endpoint.url}/start",
            data=b'{"task":"safe"}',
            headers={
                "Authorization": "Bearer test-only-secret",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with opener.open(req, timeout=2.0) as response:
            transport.read_bounded(response)

    assert endpoint.paths == ["/start", "/final"]
    assert endpoint.methods == ["POST", "POST"]
    assert endpoint.sensitive_header_names == [{"authorization"}, {"authorization"}]
    assert endpoint.bodies == [b'{"task":"safe"}', b'{"task":"safe"}']


@pytest.mark.parametrize("code", [301, 302, 303])
def test_authenticated_redirect_refuses_method_rewrite_statuses(code: int) -> None:
    with _Redirect(location="/final", code=code) as endpoint:
        opener = transport.build_safe_opener(allow_local=True)
        req = Request(
            f"{endpoint.url}/start",
            data=b"{}",
            headers={"Authorization": "Bearer test-only-secret"},
            method="POST",
        )
        with pytest.raises(URLError, match="require status 307 or 308"):
            opener.open(req, timeout=2.0)

    assert endpoint.paths == ["/start"]


@pytest.mark.parametrize(
    "header_name",
    [
        "Authorization",
        "authorization",
        "Proxy-Authorization",
        "Cookie",
        "Cookie2",
    ],
)
@pytest.mark.parametrize("code", [307, 308])
def test_sensitive_header_never_crosses_origin(
    header_name: str,
    code: int,
) -> None:
    with _Receiver() as receiver:
        with _Redirect(location=f"{receiver.url}/final", code=code) as proxy:
            opener = transport.build_safe_opener(allow_local=True)
            req = Request(
                f"{proxy.url}/start",
                data=b"{}",
                headers={header_name: "test-only-secret"},
                method="POST",
            )
            with pytest.raises(URLError, match="must not cross origins"):
                opener.open(req, timeout=2.0)

    assert proxy.paths == ["/start"]
    assert receiver.paths == []


def test_sensitive_header_never_crosses_hostname_on_the_same_port() -> None:
    with _Redirect(location="/unused", code=307) as endpoint:
        endpoint.location = f"http://127.0.0.1:{endpoint.port}/final"
        opener = transport.build_safe_opener(allow_local=True)
        req = Request(
            f"{endpoint.url}/start",
            data=b"{}",
            method="POST",
        )
        req.add_unredirected_header("Authorization", "Bearer test-only-secret")
        with pytest.raises(URLError, match="must not cross origins"):
            opener.open(req, timeout=2.0)

    assert endpoint.paths == ["/start"]


def test_authenticated_http_to_https_redirect_is_refused(tmp_path: Path) -> None:
    certfile, keyfile = _localhost_cert(tmp_path)
    with _Receiver(certfile=certfile, keyfile=keyfile) as receiver:
        with _Redirect(location=f"{receiver.url}/upgraded", code=307) as proxy:
            opener = transport.build_safe_opener(allow_local=True, ca_file=str(certfile))
            req = Request(
                f"{proxy.url}/start",
                data=b"{}",
                headers={"Authorization": "Bearer test-only-secret"},
                method="POST",
            )
            with pytest.raises(URLError, match="must not cross origins"):
                opener.open(req, timeout=2.0)

    assert proxy.paths == ["/start"]
    assert receiver.paths == []


@pytest.mark.parametrize("authenticated", [False, True])
def test_https_to_http_redirect_is_always_refused(
    tmp_path: Path,
    authenticated: bool,
) -> None:
    certfile, keyfile = _localhost_cert(tmp_path)
    headers = {"Authorization": "Bearer test-only-secret"} if authenticated else {}
    with _Receiver() as receiver:
        with _Redirect(
            location=f"{receiver.url}/downgraded",
            code=307,
            certfile=certfile,
            keyfile=keyfile,
        ) as proxy:
            opener = transport.build_safe_opener(allow_local=True, ca_file=str(certfile))
            req = Request(
                f"{proxy.url}/start",
                data=b"{}",
                headers=headers,
                method="POST",
            )
            with pytest.raises(URLError, match="must not downgrade HTTPS to HTTP"):
                opener.open(req, timeout=2.0)

    assert proxy.paths == ["/start"]
    assert receiver.paths == []


@pytest.mark.parametrize(
    "location",
    [
        "http://user@localhost/final",
        "http://localhost:/final",
        "http://localhost,attacker.test/final",
        "http://localhost\\@attacker.test/final",
        "http://%6cocalhost/final",
    ],
)
def test_redirect_refuses_credential_or_ambiguous_authority(location: str) -> None:
    with _Redirect(location=location, code=307) as proxy:
        opener = transport.build_safe_opener(allow_local=True)
        with pytest.raises(URLError, match="one exact HTTP\\(S\\) origin"):
            opener.open(f"{proxy.url}/start", timeout=2.0)

    assert proxy.paths == ["/start"]


def test_scheme_relative_same_origin_307_is_permitted() -> None:
    with _Redirect(location="/unused", code=307) as endpoint:
        endpoint.location = f"//localhost:{endpoint.port}/final"
        opener = transport.build_safe_opener(allow_local=True)
        req = Request(
            f"{endpoint.url}/start",
            data=b"{}",
            headers={"Authorization": "Bearer test-only-secret"},
            method="POST",
        )
        with opener.open(req, timeout=2.0) as response:
            transport.read_bounded(response)

    assert endpoint.paths == ["/start", "/final"]


def test_redirect_chain_limit_is_explicit_and_bounded() -> None:
    with _Redirect(location="/loop", code=307, always_redirect=True) as endpoint:
        opener = transport.build_safe_opener(allow_local=True)
        req = Request(
            f"{endpoint.url}/start",
            data=b"{}",
            headers={"Authorization": "Bearer test-only-secret"},
            method="POST",
        )
        with pytest.raises(URLError, match="redirect limit exceeded") as exc_info:
            opener.open(req, timeout=2.0)

    assert len(endpoint.paths) <= transport.WEBHOOK_MAX_REDIRECTS + 1
    assert "test-only-secret" not in str(exc_info.value)


def test_webhook_redirect_policy_descriptor_is_stable_and_value_free() -> None:
    assert transport.describe_webhook_redirect_policy() == {
        "https_downgrade": "deny",
        "sensitive_headers": [
            "authorization",
            "cookie",
            "cookie2",
            "proxy-authorization",
        ],
        "authenticated_statuses": [307, 308],
        "authenticated_origin": "exact",
        "max_redirects": 5,
    }


def test_safe_opener_refuses_a_redirect_to_a_non_http_scheme() -> None:
    with _Redirect(location="ftp://example.invalid/loot", code=302) as proxy:
        opener = transport.build_safe_opener(allow_local=True)
        with pytest.raises(URLError, match="one exact HTTP\\(S\\) origin"):
            opener.open(f"http://localhost:{proxy.port}/start", timeout=2.0)


def test_safe_opener_ignores_environment_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("http_proxy", "http://10.0.0.9:3128")
    with _Receiver() as receiver:
        opener = transport.build_safe_opener(allow_local=True)
        with opener.open(f"http://localhost:{receiver.port}/direct", timeout=2.0) as response:
            transport.read_bounded(response)

    assert receiver.paths == ["/direct"]


def test_https_pinned_connection_verifies_the_hostname(tmp_path: Path) -> None:
    certfile, keyfile = _localhost_cert(tmp_path)
    with _Receiver(certfile=certfile, keyfile=keyfile) as receiver:
        opener = transport.build_safe_opener(allow_local=True, ca_file=str(certfile))
        req = Request(
            f"https://localhost:{receiver.port}/secure",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=2.0) as response:
            transport.read_bounded(response)

    assert receiver.paths == ["/secure"]


def test_read_bounded_caps_the_response_body() -> None:
    class _Body:
        def __init__(self) -> None:
            self.requested: int | None = None

        def read(self, amount: int) -> bytes:
            self.requested = amount
            return b"x" * amount

    body = _Body()
    transport.read_bounded(body)  # type: ignore[arg-type]
    assert body.requested == transport.WEBHOOK_MAX_RESPONSE_BYTES


class _Receiver:
    def __init__(self, *, certfile: Path | None = None, keyfile: Path | None = None) -> None:
        self.paths: list[str] = []
        self.methods: list[str] = []
        self.port = _free_port()
        self._scheme = "https" if certfile is not None else "http"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                owner.paths.append(self.path)
                owner.methods.append(self.command)
                length = int(self.headers.get("Content-Length") or "0")
                if length:
                    self.rfile.read(length)
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = _handle
            do_POST = _handle

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        if certfile is not None and keyfile is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(certfile, keyfile)
            self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _Receiver:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)

    @property
    def url(self) -> str:
        return f"{self._scheme}://localhost:{self.port}"


class _Redirect:
    def __init__(
        self,
        *,
        location: str,
        code: int,
        certfile: Path | None = None,
        keyfile: Path | None = None,
        always_redirect: bool = False,
    ) -> None:
        self.location = location
        self.code = code
        self.always_redirect = always_redirect
        self.port = _free_port()
        self.paths: list[str] = []
        self.methods: list[str] = []
        self.sensitive_header_names: list[set[str]] = []
        self.bodies: list[bytes] = []
        self._scheme = "https" if certfile is not None else "http"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                owner.paths.append(self.path)
                owner.methods.append(self.command)
                owner.sensitive_header_names.append(
                    {
                        str(name).casefold()
                        for name in self.headers
                        if str(name).casefold() in transport.SENSITIVE_WEBHOOK_HEADERS
                    }
                )
                length = int(self.headers.get("Content-Length") or "0")
                owner.bodies.append(self.rfile.read(length) if length else b"")
                should_redirect = owner.always_redirect or self.path == "/start"
                self.send_response(owner.code if should_redirect else HTTPStatus.NO_CONTENT)
                if should_redirect:
                    self.send_header("Location", owner.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = _handle
            do_POST = _handle

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        if certfile is not None and keyfile is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(certfile, keyfile)
            self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"{self._scheme}://localhost:{self.port}"

    def __enter__(self) -> _Redirect:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _localhost_cert(tmp_path: Path) -> tuple[Path, Path]:
    certfile = tmp_path / "cert.pem"
    keyfile = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(keyfile),
            "-out",
            str(certfile),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return certfile, keyfile
