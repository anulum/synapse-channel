# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — TLS/WSS hub deployment tests

from __future__ import annotations

import asyncio
import ssl
import subprocess
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from websockets.asyncio.client import connect

from hub_e2e_helpers import _await_listening, _free_port, read_until_type
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.tls import (
    HubTLSConfigError,
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    MTLSVerificationResult,
    build_mutual_tls_server_ssl_context,
    build_server_ssl_context,
    certificate_sha256_pin,
    certificate_sha256_pin_from_der,
)


def _write_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    certfile = tmp_path / "hub-cert.pem"
    keyfile = tmp_path / "hub-key.pem"
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


def test_server_ssl_context_requires_complete_chain(tmp_path: Path) -> None:
    certfile, keyfile = _write_self_signed_cert(tmp_path)

    assert build_server_ssl_context(certfile=None, keyfile=None) is None
    with pytest.raises(HubTLSConfigError, match="requires both"):
        build_server_ssl_context(certfile=certfile, keyfile=None)
    with pytest.raises(HubTLSConfigError, match="requires both"):
        build_server_ssl_context(certfile=None, keyfile=keyfile)


def test_server_ssl_context_reports_invalid_chain(tmp_path: Path) -> None:
    certfile = tmp_path / "bad-cert.pem"
    keyfile = tmp_path / "bad-key.pem"
    certfile.write_text("not a certificate\n", encoding="utf-8")
    keyfile.write_text("not a key\n", encoding="utf-8")

    with pytest.raises(HubTLSConfigError, match="could not load hub TLS certificate chain"):
        build_server_ssl_context(certfile=certfile, keyfile=keyfile)


async def test_hub_serves_real_wss_connection(tmp_path: Path) -> None:
    certfile, keyfile = _write_self_signed_cert(tmp_path)
    server_context = build_server_ssl_context(certfile=certfile, keyfile=keyfile)
    client_context = ssl.create_default_context()
    client_context.check_hostname = False
    client_context.verify_mode = ssl.CERT_NONE

    hub = SynapseHub(hub_id="syn-wss")
    port = _free_port()
    task = asyncio.create_task(hub.serve("localhost", port, ssl_context=server_context))
    try:
        await _await_listening(port)
        async with connect(f"wss://localhost:{port}", ssl=client_context) as websocket:
            welcome = await read_until_type(websocket, "welcome")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert welcome["hub_id"] == "syn-wss"


def test_hub_tls_public_docs_describe_native_wss() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    cli_docs = Path("docs/cli.md").read_text(encoding="utf-8")
    deployment_docs = Path("docs/deployment.md").read_text(encoding="utf-8")
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")

    for document in (readme, cli_docs, deployment_docs, changelog):
        assert "--tls-certfile" in document
        assert "--tls-keyfile" in document
        assert "wss://" in document

    assert "does not replace `--token`" in deployment_docs


def test_federation_proxy_path_docs_separate_termination_from_pinning() -> None:
    """Deployment docs must not present TLS termination as hub mTLS."""
    deployment_docs = Path("docs/deployment.md").read_text(encoding="utf-8")

    for required in (
        "--federation-path atelier=tls-passthrough",
        "--federation-path atelier=tls-terminating-proxy",
        "Direct native WSS/mTLS to the hub process.",
        "TCP/TLS passthrough",
        "private tailnet path",
        "not the hub certificate",
        "not the same as direct mTLS",
    ):
        assert required in deployment_docs


def test_mutual_tls_server_context_requires_client_certificate_authority(
    tmp_path: Path,
) -> None:
    certfile, keyfile = _write_self_signed_cert(tmp_path)

    context = build_mutual_tls_server_ssl_context(
        certfile=certfile,
        keyfile=keyfile,
        client_ca_file=certfile,
    )

    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is False
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    with pytest.raises(HubTLSConfigError, match="requires --mtls-client-ca-file"):
        build_mutual_tls_server_ssl_context(
            certfile=certfile,
            keyfile=keyfile,
            client_ca_file=None,
        )


def test_trust_bundle_verifies_peer_certificate_pins_and_project_scope(
    tmp_path: Path,
) -> None:
    certfile, _ = _write_self_signed_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    bundle = MTLSPeerTrustBundle(
        peers={
            "peer-a": MTLSTrustedPeer(
                peer_id="peer-a",
                certificate_pins=frozenset({pin}),
                signing_key_ids=frozenset({"SYNAPSE-CHANNEL:main:2026-06"}),
                projects=frozenset({"SYNAPSE-CHANNEL"}),
            )
        }
    )

    assert (
        bundle.verify_peer_certificate(
            "peer-a",
            certfile=certfile,
            project="SYNAPSE-CHANNEL",
            signing_key_id="SYNAPSE-CHANNEL:main:2026-06",
        )
        == MTLSVerificationResult.VALID
    )
    assert (
        bundle.verify_peer_certificate(
            "missing",
            certfile=certfile,
            project="SYNAPSE-CHANNEL",
            signing_key_id="SYNAPSE-CHANNEL:main:2026-06",
        )
        == MTLSVerificationResult.UNKNOWN_PEER
    )
    assert (
        bundle.verify_peer_certificate(
            "peer-a",
            certfile=certfile,
            project="OTHER",
            signing_key_id="SYNAPSE-CHANNEL:main:2026-06",
        )
        == MTLSVerificationResult.PROJECT_SCOPE_MISMATCH
    )


def test_trust_bundle_reports_bad_pin_revoked_peer_and_unknown_signing_key(
    tmp_path: Path,
) -> None:
    certfile, _ = _write_self_signed_cert(tmp_path)
    wrong_pin = "sha256:" + ("0" * 64)
    bundle = MTLSPeerTrustBundle(
        peers={
            "peer-a": MTLSTrustedPeer(
                peer_id="peer-a",
                certificate_pins=frozenset({wrong_pin}),
                signing_key_ids=frozenset({"key-a"}),
                projects=frozenset({"SYNAPSE-CHANNEL"}),
            ),
            "peer-b": MTLSTrustedPeer(
                peer_id="peer-b",
                certificate_pins=frozenset({certificate_sha256_pin(certfile)}),
                signing_key_ids=frozenset({"key-a"}),
                projects=frozenset({"SYNAPSE-CHANNEL"}),
                revoked=True,
            ),
        }
    )

    assert (
        bundle.verify_peer_certificate(
            "peer-a",
            certfile=certfile,
            project="SYNAPSE-CHANNEL",
            signing_key_id="key-a",
        )
        == MTLSVerificationResult.BAD_CERTIFICATE_PIN
    )
    assert (
        bundle.verify_peer_certificate(
            "peer-b",
            certfile=certfile,
            project="SYNAPSE-CHANNEL",
            signing_key_id="key-a",
        )
        == MTLSVerificationResult.REVOKED_PEER
    )
    assert (
        bundle.verify_peer_certificate(
            "peer-a",
            certfile=certfile,
            project="SYNAPSE-CHANNEL",
            signing_key_id="missing-key",
        )
        == MTLSVerificationResult.UNKNOWN_SIGNING_KEY
    )


def test_certificate_pin_supports_der_and_reports_parse_errors(tmp_path: Path) -> None:
    certfile, _ = _write_self_signed_cert(tmp_path)
    cert = x509.load_pem_x509_certificate(certfile.read_bytes())
    derfile = tmp_path / "hub-cert.der"
    derfile.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    badfile = tmp_path / "not-a-cert.pem"
    badfile.write_text("not a certificate\n", encoding="utf-8")

    assert certificate_sha256_pin(derfile) == certificate_sha256_pin(certfile)
    with pytest.raises(HubTLSConfigError, match="could not parse peer certificate"):
        certificate_sha256_pin(badfile)


def test_certificate_pin_from_der_matches_the_file_pin(tmp_path: Path) -> None:
    certfile, _ = _write_self_signed_cert(tmp_path)
    cert = x509.load_pem_x509_certificate(certfile.read_bytes())
    der = cert.public_bytes(serialization.Encoding.DER)

    assert certificate_sha256_pin_from_der(der) == certificate_sha256_pin(certfile)


def test_certificate_pin_from_der_rejects_empty_and_unparsable_bytes() -> None:
    with pytest.raises(HubTLSConfigError, match="no peer certificate presented"):
        certificate_sha256_pin_from_der(b"")
    with pytest.raises(HubTLSConfigError, match="could not parse peer certificate"):
        certificate_sha256_pin_from_der(b"not a certificate")


def test_verify_peer_pin_mirrors_certificate_verification(tmp_path: Path) -> None:
    certfile, _ = _write_self_signed_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    bundle = MTLSPeerTrustBundle(
        peers={
            "peer-a": MTLSTrustedPeer(
                peer_id="peer-a",
                certificate_pins=frozenset({pin}),
                signing_key_ids=frozenset({"key-a"}),
                projects=frozenset({"SYNAPSE-CHANNEL"}),
            ),
            "peer-b": MTLSTrustedPeer(
                peer_id="peer-b",
                certificate_pins=frozenset({pin}),
                signing_key_ids=frozenset({"key-a"}),
                projects=frozenset({"SYNAPSE-CHANNEL"}),
                revoked=True,
            ),
        }
    )

    def verify(peer_id: str, *, pin: str, project: str, key: str) -> MTLSVerificationResult:
        return bundle.verify_peer_pin(peer_id, pin=pin, project=project, signing_key_id=key)

    assert (
        verify("peer-a", pin=pin, project="SYNAPSE-CHANNEL", key="key-a")
        == MTLSVerificationResult.VALID
    )
    assert (
        verify("missing", pin=pin, project="SYNAPSE-CHANNEL", key="key-a")
        == MTLSVerificationResult.UNKNOWN_PEER
    )
    assert (
        verify("peer-b", pin=pin, project="SYNAPSE-CHANNEL", key="key-a")
        == MTLSVerificationResult.REVOKED_PEER
    )
    assert (
        verify("peer-a", pin=pin, project="OTHER", key="key-a")
        == MTLSVerificationResult.PROJECT_SCOPE_MISMATCH
    )
    assert (
        verify("peer-a", pin=pin, project="SYNAPSE-CHANNEL", key="missing")
        == MTLSVerificationResult.UNKNOWN_SIGNING_KEY
    )
    assert (
        verify("peer-a", pin="sha256:" + ("0" * 64), project="SYNAPSE-CHANNEL", key="key-a")
        == MTLSVerificationResult.BAD_CERTIFICATE_PIN
    )


def test_trust_bundle_reports_missing_peer_certificate(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pem"
    bundle = MTLSPeerTrustBundle(
        peers={
            "peer-a": MTLSTrustedPeer(
                peer_id="peer-a",
                certificate_pins=frozenset({"sha256:" + ("0" * 64)}),
                signing_key_ids=frozenset({"key-a"}),
                projects=frozenset({"SYNAPSE-CHANNEL"}),
            )
        }
    )

    assert (
        bundle.verify_peer_certificate(
            "peer-a",
            certfile=missing,
            project="SYNAPSE-CHANNEL",
            signing_key_id="key-a",
        )
        == MTLSVerificationResult.MISSING_CERTIFICATE
    )


def test_mutual_tls_context_reports_missing_server_material_and_bad_ca(
    tmp_path: Path,
) -> None:
    certfile, keyfile = _write_self_signed_cert(tmp_path)
    bad_ca = tmp_path / "bad-ca.pem"
    bad_ca.write_text("not a certificate authority\n", encoding="utf-8")

    with pytest.raises(HubTLSConfigError, match="requires --tls-certfile"):
        build_mutual_tls_server_ssl_context(
            certfile=None,
            keyfile=None,
            client_ca_file=certfile,
        )
    with pytest.raises(HubTLSConfigError, match="could not load mTLS client CA bundle"):
        build_mutual_tls_server_ssl_context(
            certfile=certfile,
            keyfile=keyfile,
            client_ca_file=bad_ca,
        )
