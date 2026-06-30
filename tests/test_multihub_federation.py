# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — deny-by-default authorisation for a cross-host multi-hub pull

from __future__ import annotations

import subprocess
from pathlib import Path

from synapse_channel.core.federation import (
    AUTHORISED,
    FederationBundle,
    FederationDenyReason,
    FederationPeer,
    ScopeGrant,
)
from synapse_channel.core.multihub_federation import (
    ACL_DENIED,
    SIGNATURE_UNVERIFIED,
    MultiHubPeerCredential,
    authorise_multihub_peer,
    authorise_multihub_pull,
    peer_authoriser,
)
from synapse_channel.core.tls import (
    MTLSPeerTrustBundle,
    MTLSTrustedPeer,
    MTLSVerificationResult,
    certificate_sha256_pin,
)

_DOMAIN = "domain-b"
_NAMESPACE = "SYNAPSE-CHANNEL"
_KEY = "SYNAPSE-CHANNEL:main:2026-06"
_SCOPE = (ScopeGrant(verb="read", namespace=_NAMESPACE),)


def _write_cert(tmp_path: Path) -> Path:
    """Write a self-signed certificate the way the TLS tests do, and return its path."""
    certfile = tmp_path / "peer-cert.pem"
    keyfile = tmp_path / "peer-key.pem"
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
            "/CN=peer-b",
            "-keyout",
            str(keyfile),
            "-out",
            str(certfile),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return certfile


def _credential(certfile: Path) -> MultiHubPeerCredential:
    return MultiHubPeerCredential(
        domain_id=_DOMAIN, namespace=_NAMESPACE, signing_key_id=_KEY, certfile=certfile
    )


def _federation(
    pin: str,
    *,
    namespaces: frozenset[str] = frozenset({_NAMESPACE}),
    expires_at: float | None = None,
) -> FederationBundle:
    """Build a federation bundle that grants the peer, overridable per test."""
    return FederationBundle(
        [
            FederationPeer(
                domain_id=_DOMAIN,
                namespaces=namespaces,
                certificate_pins=frozenset({pin}),
                signing_key_ids=frozenset({_KEY}),
                scope_grants=_SCOPE,
                expires_at=expires_at,
            )
        ]
    )


def _mtls(pin: str, *, certificate_pins: frozenset[str] | None = None) -> MTLSPeerTrustBundle:
    """Build a mutual-TLS bundle that trusts the peer, overridable per test."""
    return MTLSPeerTrustBundle(
        peers={
            _DOMAIN: MTLSTrustedPeer(
                peer_id=_DOMAIN,
                certificate_pins=frozenset({pin}) if certificate_pins is None else certificate_pins,
                signing_key_ids=frozenset({_KEY}),
                projects=frozenset({_NAMESPACE}),
            )
        }
    )


# --- authorise_multihub_pull -------------------------------------------------------------


def test_authorises_a_fully_granted_peer(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    decision = authorise_multihub_pull(
        federation=_federation(pin), mtls=_mtls(pin), credential=_credential(certfile), now=0.0
    )
    assert decision.allowed
    assert decision.reason == AUTHORISED
    assert decision.scope == _SCOPE


def test_unloadable_certificate_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "nope.pem"
    pin = "sha256:" + ("0" * 64)
    decision = authorise_multihub_pull(
        federation=_federation(pin), mtls=_mtls(pin), credential=_credential(missing), now=0.0
    )
    assert not decision.allowed
    assert decision.reason == MTLSVerificationResult.MISSING_CERTIFICATE.value
    assert decision.scope == ()


def test_federation_denial_is_reported(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    federation = _federation(pin, namespaces=frozenset({"OTHER"}))
    decision = authorise_multihub_pull(
        federation=federation, mtls=_mtls(pin), credential=_credential(certfile), now=0.0
    )
    assert not decision.allowed
    assert decision.reason == FederationDenyReason.NAMESPACE_NOT_GRANTED


def test_mtls_failure_is_reported_when_federation_allows(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    wrong_pin = "sha256:" + ("0" * 64)
    decision = authorise_multihub_pull(
        federation=_federation(pin),
        mtls=_mtls(pin, certificate_pins=frozenset({wrong_pin})),
        credential=_credential(certfile),
        now=0.0,
    )
    assert not decision.allowed
    assert decision.reason == MTLSVerificationResult.BAD_CERTIFICATE_PIN.value


def test_signature_gate_can_refuse(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    decision = authorise_multihub_pull(
        federation=_federation(pin),
        mtls=_mtls(pin),
        credential=_credential(certfile),
        now=0.0,
        signature_ok=False,
    )
    assert not decision.allowed
    assert decision.reason == SIGNATURE_UNVERIFIED


def test_acl_gate_can_refuse(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    decision = authorise_multihub_pull(
        federation=_federation(pin),
        mtls=_mtls(pin),
        credential=_credential(certfile),
        now=0.0,
        acl_ok=False,
    )
    assert not decision.allowed
    assert decision.reason == ACL_DENIED


# --- authorise_multihub_peer -------------------------------------------------------------


def test_authorise_peer_accepts_a_pin_directly(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    decision = authorise_multihub_peer(
        federation=_federation(pin),
        mtls=_mtls(pin),
        certificate_pin=pin,
        domain_id=_DOMAIN,
        namespace=_NAMESPACE,
        signing_key_id=_KEY,
        now=0.0,
    )
    assert decision.allowed
    assert decision.reason == AUTHORISED
    assert decision.scope == _SCOPE


def test_authorise_peer_reports_mtls_pin_mismatch(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    wrong_pin = "sha256:" + ("0" * 64)
    decision = authorise_multihub_peer(
        federation=_federation(pin),
        mtls=_mtls(pin, certificate_pins=frozenset({wrong_pin})),
        certificate_pin=pin,
        domain_id=_DOMAIN,
        namespace=_NAMESPACE,
        signing_key_id=_KEY,
        now=0.0,
    )
    assert not decision.allowed
    assert decision.reason == MTLSVerificationResult.BAD_CERTIFICATE_PIN.value


# --- peer_authoriser ---------------------------------------------------------------------


def test_peer_authoriser_binds_a_zero_argument_gate(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    gate = peer_authoriser(
        federation=_federation(pin),
        mtls=_mtls(pin),
        credential=_credential(certfile),
        clock=lambda: 0.0,
    )
    assert gate().allowed


def test_peer_authoriser_re_evaluates_expiry_per_call(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    clock = {"now": 50.0}
    gate = peer_authoriser(
        federation=_federation(pin, expires_at=100.0),
        mtls=_mtls(pin),
        credential=_credential(certfile),
        clock=lambda: clock["now"],
    )
    assert gate().allowed
    clock["now"] = 150.0
    refused = gate()
    assert not refused.allowed
    assert refused.reason == FederationDenyReason.EXPIRED_PEERING
