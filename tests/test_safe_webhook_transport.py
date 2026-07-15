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


def test_resolve_pinned_endpoint_returns_first_of_several_public_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transport.socket,
        "getaddrinfo",
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
        ],
    )
    assert transport.resolve_pinned_endpoint("host.invalid", 443, allow_local=False) == "8.8.8.8"


def test_resolve_pinned_endpoint_rejects_any_non_public_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transport.socket,
        "getaddrinfo",
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(URLError, match="must not target local networks"):
        transport.resolve_pinned_endpoint("mixed.invalid", 443, allow_local=False)


def test_resolve_pinned_endpoint_allows_local_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transport.socket,
        "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )
    assert transport.resolve_pinned_endpoint("localhost", 80, allow_local=True) == "127.0.0.1"


def test_resolve_pinned_endpoint_wraps_resolver_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> list[Any]:
        raise OSError("no such host")

    monkeypatch.setattr(transport.socket, "getaddrinfo", boom)
    with pytest.raises(URLError, match="could not resolve webhook target"):
        transport.resolve_pinned_endpoint("nx.invalid", 80, allow_local=False)


def test_resolve_pinned_endpoint_rejects_empty_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transport.socket,
        "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ())],
    )
    with pytest.raises(URLError, match="could not resolve webhook target"):
        transport.resolve_pinned_endpoint("empty.invalid", 80, allow_local=False)


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

        monkeypatch.setattr(transport.socket, "getaddrinfo", resolve)
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


def test_safe_opener_refuses_a_redirect_to_a_non_http_scheme() -> None:
    with _Redirect(location="ftp://example.invalid/loot", code=302) as proxy:
        opener = transport.build_safe_opener(allow_local=True)
        with pytest.raises(URLError, match="must use http or https"):
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


class _Redirect:
    def __init__(self, *, location: str, code: int) -> None:
        self.location = location
        self.code = code
        self.port = _free_port()
        self.paths: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                owner.paths.append(self.path)
                self.send_response(owner.code)
                self.send_header("Location", owner.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = _handle
            do_POST = _handle

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

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
