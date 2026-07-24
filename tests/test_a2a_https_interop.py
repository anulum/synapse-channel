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

from a2a_server_helpers import RecordingAgent, _default_bridge, _free_port
from synapse_channel.a2a_http import A2AHTTPServer, make_a2a_http_server
from synapse_channel.a2a_interop_trace import RECEIPT_SCHEMA, run_local_interop_trace
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.core.tls import build_server_ssl_context


def _write_server_tls_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Write a self-signed server cert + key PEM pair for loopback HTTPS."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
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
    port = _free_port()
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


def test_https_interop_trace_twice_with_consistent_observables(tmp_path: Path) -> None:
    """Run the independent client twice over native HTTPS; both must succeed."""
    certfile, keyfile = _write_server_tls_pair(tmp_path)
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=_default_bridge().agent_card,
        target="WORKER",
        store=A2ATaskStore(),
    )
    server, port, _thread = _serve_https(bridge, certfile, keyfile)
    receipts: list[dict[str, object]] = []
    try:
        for index in (1, 2):
            receipt = run_local_interop_trace(
                host="127.0.0.1",
                port=port,
                scheme="https",
                ca_file=certfile,
                message_text=f"https-probe-{index}",
                timeout=5.0,
            )
            receipts.append(receipt)
    finally:
        server.shutdown()
        server.server_close()

    assert len(receipts) == 2
    for index, receipt in enumerate(receipts, start=1):
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

    # Both runs produced non-empty distinct task ids and matching message content.
    ids = [str(r["task_lifecycle"]["task_id"]) for r in receipts]  # type: ignore[index]
    assert ids[0] and ids[1]
    assert ids[0] != ids[1]
    assert any("https-probe-1" in text for _t, text in bridge.agent.messages)
    assert any("https-probe-2" in text for _t, text in bridge.agent.messages)
