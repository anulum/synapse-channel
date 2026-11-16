# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native HTTPS a2a-serve + interop-trace dual run
"""Prove discovery → send → get over native TLS with the independent client."""

from __future__ import annotations

import datetime
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from a2a_server_helpers import RecordingAgent, _default_bridge, free_port
from synapse_channel.a2a_client import A2AOutboundClient
from synapse_channel.a2a_http import A2AHTTPServer, make_a2a_http_server
from synapse_channel.a2a_interop_trace import RECEIPT_SCHEMA, run_local_interop_trace
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.core.tls import build_server_ssl_context


def _write_server_tls_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Write a self-signed server cert + key PEM pair for loopback HTTPS."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(__import__("ipaddress").IPv4Address("127.0.0.1"))]
            ),
            critical=False,
        )
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


def _serve_https(
    bridge: A2ABridge, certfile: Path, keyfile: Path
) -> tuple[A2AHTTPServer, int, threading.Thread]:
    port = free_port()
    bridge.allowed_authorities = (f"127.0.0.1:{port}",)
    interfaces = bridge.agent_card.get("supportedInterfaces")
    if isinstance(interfaces, list) and interfaces and isinstance(interfaces[0], dict):
        interfaces[0]["url"] = f"https://127.0.0.1:{port}"
    ssl_context = build_server_ssl_context(certfile=certfile, keyfile=keyfile)
    assert ssl_context is not None
    server = make_a2a_http_server(
        bridge=bridge,
        host="127.0.0.1",
        port=port,
        ssl_context=ssl_context,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


def _https_trace(port: int, certfile: Path, *, index: int) -> dict[str, object]:
    """Run one independent client trace over the native HTTPS server."""
    return run_local_interop_trace(
        host="127.0.0.1",
        port=port,
        scheme="https",
        ca_file=certfile,
        message_text=f"https-probe-{index}",
        timeout=5.0,
    )


def _assert_https_receipt(receipt: dict[str, object], *, index: int) -> None:
    """Check the observables of one native HTTPS trace."""
    assert receipt["schema"] == RECEIPT_SCHEMA
    endpoint = receipt["endpoint"]
    assert isinstance(endpoint, dict)
    assert endpoint["scheme"] == "https"
    assert endpoint["url"].startswith("https://")
    discovery = receipt["discovery"]
    assert isinstance(discovery, dict)
    assert discovery["url_scheme"] == "https"
    assert discovery["http_status"] == 200
    lifecycle = receipt["task_lifecycle"]
    assert isinstance(lifecycle, dict)
    assert lifecycle["task_id"]
    assert lifecycle["message_text"] == f"https-probe-{index}"
    assert lifecycle["send_http_status"] == 200
    assert lifecycle["get_http_status"] == 200
    dimensions = receipt["dimensions"]
    assert isinstance(dimensions, dict)
    assert dimensions["proxy_tls"] == "recorded"


def _open_https(tmp_path: Path) -> tuple[A2ABridge, A2AHTTPServer, int, Path]:
    """Start one native HTTPS bridge and return it with its port and certificate."""
    certfile, keyfile = _write_server_tls_pair(tmp_path)
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=_default_bridge().agent_card,
        target="WORKER",
        store=A2ATaskStore(),
    )
    server, port, _thread = _serve_https(bridge, certfile, keyfile)
    return bridge, server, port, certfile


def test_https_interop_trace_records_the_first_run(tmp_path: Path) -> None:
    """The first independent client run over native HTTPS succeeds."""
    _bridge, server, port, certfile = _open_https(tmp_path)
    try:
        receipt = _https_trace(port, certfile, index=1)
    finally:
        server.shutdown()
        server.server_close()
    _assert_https_receipt(receipt, index=1)


def test_https_interop_trace_records_the_second_run(tmp_path: Path) -> None:
    """The second independent client run over native HTTPS succeeds."""
    _bridge, server, port, certfile = _open_https(tmp_path)
    try:
        receipt = _https_trace(port, certfile, index=2)
    finally:
        server.shutdown()
        server.server_close()
    _assert_https_receipt(receipt, index=2)


def test_https_interop_traces_keep_distinct_task_ids(tmp_path: Path) -> None:
    """Two runs produce different task ids and both messages arrive."""
    bridge, server, port, certfile = _open_https(tmp_path)
    try:
        receipts = [
            _https_trace(port, certfile, index=1),
            _https_trace(port, certfile, index=2),
        ]
    finally:
        server.shutdown()
        server.server_close()

    assert len(receipts) == 2
    ids: list[str] = []
    for receipt in receipts:
        assert isinstance(receipt, dict)
        lifecycle = receipt["task_lifecycle"]
        assert isinstance(lifecycle, dict)
        ids.append(str(lifecycle["task_id"]))
    assert ids[0] and ids[1]
    assert ids[0] != ids[1]
    assert any("https-probe-1" in text for _t, text in bridge.agent.messages)
    assert any("https-probe-2" in text for _t, text in bridge.agent.messages)


def test_outbound_client_accepts_explicit_ca_and_local_tls_drill(tmp_path: Path) -> None:
    """The shipped client performs the complete task journey over both TLS modes."""
    certfile, keyfile = _write_server_tls_pair(tmp_path)
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=_default_bridge().agent_card,
        target="WORKER",
        store=A2ATaskStore(),
    )
    server, port, thread = _serve_https(bridge, certfile, keyfile)
    endpoint = f"https://127.0.0.1:{port}"
    try:
        verified = A2AOutboundClient(endpoint, ca_file=certfile, timeout=5)
        receipt = verified.discover_send_get("trusted-ca", message_id="tls-ca-1")
        assert receipt["task_id"] == receipt["task"]["id"]
        local_drill = A2AOutboundClient(endpoint, tls_insecure=True, timeout=5)
        drill = local_drill.discover_send_get("local-drill", message_id="tls-drill-1")
        assert drill["task_id"] == drill["task"]["id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert any("trusted-ca" in body for _target, body in bridge.agent.messages)
    assert any("local-drill" in body for _target, body in bridge.agent.messages)
