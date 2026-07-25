# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A edge client-CA mTLS
"""Require client certs on native HTTPS A2A serve path."""

from __future__ import annotations

import datetime
import http.client
import ipaddress
import ssl
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from a2a_server_helpers import RecordingAgent, _default_bridge, _free_port
from synapse_channel.a2a_http import make_a2a_http_server
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.core.tls import build_mutual_tls_server_ssl_context


def _write_ca_and_certs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    """Return CA cert, server cert/key, client cert/key PEMs."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "a2a-test-ca")])
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        # Python 3.13 OpenSSL requires Authority Key Identifier on the chain.
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "ca.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    def _issue(cn: str, san_ip: str | None = None) -> tuple[Path, Path]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
        )
        if san_ip:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address(san_ip))]),
                critical=False,
            )
        cert = builder.sign(ca_key, hashes.SHA256())
        cert_path = tmp_path / f"{cn}-cert.pem"
        key_path = tmp_path / f"{cn}-key.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        return cert_path, key_path

    server_cert, server_key = _issue("a2a-server", san_ip="127.0.0.1")
    client_cert, client_key = _issue("a2a-client")
    return ca_path, server_cert, server_key, client_cert, client_key


def test_mtls_client_cert_required_and_accepted(tmp_path: Path) -> None:
    ca, server_cert, server_key, client_cert, client_key = _write_ca_and_certs(tmp_path)
    context = build_mutual_tls_server_ssl_context(
        certfile=server_cert, keyfile=server_key, client_ca_file=ca
    )
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card=_default_bridge().agent_card,
        target="WORKER",
        store=A2ATaskStore(),
    )
    port = _free_port()
    server = make_a2a_http_server(bridge=bridge, host="127.0.0.1", port=port, ssl_context=context)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # Missing client cert must fail closed
        no_client = ssl.create_default_context(cafile=str(ca))
        no_client.check_hostname = False
        try:
            conn = http.client.HTTPSConnection("127.0.0.1", port, context=no_client, timeout=3.0)
            conn.request("GET", "/.well-known/agent-card.json")
            conn.getresponse()
            conn.close()
            raised = False
        except ssl.SSLError:
            raised = True
        except OSError:
            raised = True
        assert raised, "expected TLS failure without client certificate"

        # Matching client cert succeeds
        with_client = ssl.create_default_context(cafile=str(ca))
        with_client.check_hostname = False
        with_client.load_cert_chain(certfile=str(client_cert), keyfile=str(client_key))
        conn = http.client.HTTPSConnection("127.0.0.1", port, context=with_client, timeout=5.0)
        conn.request("GET", "/.well-known/agent-card.json")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        assert response.status == 200
        assert b"name" in body
    finally:
        server.shutdown()
        server.server_close()
