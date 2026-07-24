# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native HTTPS for synapse a2a-serve
"""Native TLS bind and CLI wiring for the A2A HTTP edge."""

from __future__ import annotations

import datetime
import ssl
import threading
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from synapse_channel import cli, cli_a2a
from synapse_channel.a2a_bind_exposure import a2a_bind_problems
from synapse_channel.a2a_http import make_a2a_http_server, wrap_a2a_server_socket
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.core.tls import build_server_ssl_context


def _write_server_tls_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Write a self-signed server cert + key PEM pair."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "a2a-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    certfile = tmp_path / "a2a-cert.pem"
    keyfile = tmp_path / "a2a-key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return certfile, keyfile


def test_parser_accepts_a2a_tls_flags() -> None:
    args = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--tls-certfile",
            "/tmp/cert.pem",
            "--tls-keyfile",
            "/tmp/key.pem",
        ]
    )
    assert args.tls_certfile == "/tmp/cert.pem"
    assert args.tls_keyfile == "/tmp/key.pem"


def test_bind_matrix_allows_non_loopback_bearer_with_tls() -> None:
    assert a2a_bind_problems("0.0.0.0", bearer_auth=True, tls_active=True) == []


def test_cmd_a2a_serve_refuses_partial_tls_flags(capsys: pytest.CaptureFixture[str]) -> None:
    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--tls-certfile",
            "/tmp/only-cert.pem",
        ]
    )
    assert cli_a2a._cmd_a2a_serve(ns) == 2
    assert "TLS configuration error" in capsys.readouterr().err


def test_cmd_a2a_serve_allows_off_loopback_bearer_when_tls_configured(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    certfile, keyfile = _write_server_tls_pair(tmp_path)

    async def unavailable(**_: Any) -> None:
        return None

    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--host",
            "0.0.0.0",
            "--bearer-auth",
            "--a2a-token",
            "a2a-secret",
            "--tls-certfile",
            str(certfile),
            "--tls-keyfile",
            str(keyfile),
        ]
    )
    # Reachability fails after bind gates; refuse must not fire for plaintext bearer.
    assert cli_a2a._cmd_a2a_serve(ns, manifest_fetcher=unavailable) == 1
    err = capsys.readouterr().err
    assert "Refusing to bind A2A bridge" not in err
    assert "Could not reach hub" in err


def test_cmd_a2a_serve_warns_when_endpoint_scheme_mismatches_tls(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    certfile, keyfile = _write_server_tls_pair(tmp_path)

    async def unavailable(**_: Any) -> None:
        return None

    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "http://example.test/a2a/v1",
            "--tls-certfile",
            str(certfile),
            "--tls-keyfile",
            str(keyfile),
        ]
    )
    assert cli_a2a._cmd_a2a_serve(ns, manifest_fetcher=unavailable) == 1
    err = capsys.readouterr().err
    assert "WARNING:" in err
    assert "https://" in err


def test_cmd_a2a_serve_passes_ssl_context_and_prints_https(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    certfile, keyfile = _write_server_tls_pair(tmp_path)
    captured: dict[str, Any] = {}

    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return []

    class Runtime:
        def __init__(self, agent: Any) -> None:
            self.agent = agent

        def start(self) -> bool:
            return True

        def run(self, coro: Any) -> Any:
            coro.close()
            return None

        def stop(self) -> None:
            return None

    def serve(**kwargs: Any) -> None:
        captured.update(kwargs)
        raise KeyboardInterrupt

    ns = cli.build_parser().parse_args(
        [
            "a2a-serve",
            "--endpoint-url",
            "https://example.test/a2a/v1",
            "--bearer-auth",
            "--a2a-token",
            "a2a-secret",
            "--tls-certfile",
            str(certfile),
            "--tls-keyfile",
            str(keyfile),
        ]
    )
    assert (
        cli_a2a._cmd_a2a_serve(
            ns,
            manifest_fetcher=manifest,
            runtime_factory=Runtime,
            server_runner=serve,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "https://" in out
    assert isinstance(captured.get("ssl_context"), ssl.SSLContext)


def test_make_a2a_http_server_wraps_socket_for_https(tmp_path: Path) -> None:
    certfile, keyfile = _write_server_tls_pair(tmp_path)
    context = build_server_ssl_context(certfile=certfile, keyfile=keyfile)
    assert context is not None

    class Agent:
        async def send(self, *_a: Any, **_k: Any) -> None:
            return None

    bridge = A2ABridge(
        agent=Agent(),
        agent_card={"name": "t", "url": "https://example.test/a2a/v1", "capabilities": {}},
        target="all",
    )
    server = make_a2a_http_server(
        bridge=bridge,
        host="127.0.0.1",
        port=0,
        ssl_context=context,
    )
    try:
        assert isinstance(server.socket, ssl.SSLSocket)
        # wrap is idempotent only via explicit helper on already-plaintext sockets
        plain = make_a2a_http_server(bridge=bridge, host="127.0.0.1", port=0)
        try:
            assert not isinstance(plain.socket, ssl.SSLSocket)
            wrap_a2a_server_socket(plain, None)
            assert not isinstance(plain.socket, ssl.SSLSocket)
        finally:
            plain.server_close()
    finally:
        server.server_close()


def test_https_agent_card_reachable_through_native_tls(tmp_path: Path) -> None:
    """Real HTTPS GET against a native-TLS A2A edge (stdlib server path)."""
    import http.client

    certfile, keyfile = _write_server_tls_pair(tmp_path)
    context = build_server_ssl_context(certfile=certfile, keyfile=keyfile)
    assert context is not None

    class Agent:
        async def send(self, *_a: Any, **_k: Any) -> None:
            return None

    bridge = A2ABridge(
        agent=Agent(),
        agent_card={
            "name": "TLS Bridge",
            "url": "https://127.0.0.1/a2a/v1",
            "capabilities": {},
        },
        target="all",
    )
    server = make_a2a_http_server(
        bridge=bridge,
        host="127.0.0.1",
        port=0,
        ssl_context=context,
    )
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client_ctx = ssl.create_default_context()
        client_ctx.check_hostname = False
        client_ctx.verify_mode = ssl.CERT_NONE
        # Prefer http.client so the test does not depend on urllib SSL context wiring.
        conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5.0)
        try:
            conn.request("GET", "/.well-known/agent-card.json")
            response = conn.getresponse()
            body = response.read()
            assert response.status == 200
            assert b"TLS Bridge" in body
        finally:
            conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
