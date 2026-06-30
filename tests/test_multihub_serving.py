# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — serving-side deny-by-default gate for a multi-hub pull

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from synapse_channel.core.federation import (
    AUTHORISED,
    FederationBundle,
    FederationDenyReason,
    FederationPeer,
    ScopeGrant,
)
from synapse_channel.core.multihub_serving import (
    MultiHubServingGrant,
    MultiHubServingPolicy,
    live_peer_certificate_der,
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
_SENDER = "peer-b"
_SCOPE = (ScopeGrant(verb="read", namespace=_NAMESPACE),)


def _write_cert(tmp_path: Path) -> Path:
    """Write a self-signed certificate and return its path."""
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def _der(certfile: Path) -> bytes:
    """Return the certificate's canonical DER bytes, as a live socket would present them."""
    cert = x509.load_pem_x509_certificate(certfile.read_bytes())
    return cert.public_bytes(serialization.Encoding.DER)


def _federation(
    pin: str, *, namespaces: frozenset[str] = frozenset({_NAMESPACE})
) -> FederationBundle:
    return FederationBundle(
        [
            FederationPeer(
                domain_id=_DOMAIN,
                namespaces=namespaces,
                certificate_pins=frozenset({pin}),
                signing_key_ids=frozenset({_KEY}),
                scope_grants=_SCOPE,
            )
        ]
    )


def _mtls(pin: str, *, certificate_pins: frozenset[str] | None = None) -> MTLSPeerTrustBundle:
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


def _policy(
    pin: str,
    der: bytes | None,
    *,
    federation: FederationBundle | None = None,
    mtls: MTLSPeerTrustBundle | None = None,
    clock: Any = None,
) -> MultiHubServingPolicy:
    """Build a serving policy whose certificate source yields ``der`` for any socket."""
    return MultiHubServingPolicy(
        federation=federation if federation is not None else _federation(pin),
        mtls=mtls if mtls is not None else _mtls(pin),
        grants={
            _SENDER: MultiHubServingGrant(
                domain_id=_DOMAIN, namespace=_NAMESPACE, signing_key_id=_KEY
            )
        },
        clock=clock if clock is not None else (lambda: 0.0),
        cert_source=lambda _websocket: der,
    )


# --- live_peer_certificate_der -----------------------------------------------------------


class _FakeSSL:
    def __init__(self, der: bytes) -> None:
        self._der = der

    def getpeercert(self, binary_form: bool = False) -> bytes:
        assert binary_form is True
        return self._der


class _FakeTransport:
    def __init__(self, ssl_object: Any) -> None:
        self._ssl = ssl_object

    def get_extra_info(self, name: str) -> Any:
        return self._ssl if name == "ssl_object" else None


class _FakeWebsocket:
    def __init__(self, transport: Any) -> None:
        self.transport = transport


def test_live_certificate_is_none_without_a_transport() -> None:
    assert live_peer_certificate_der(object()) is None
    assert live_peer_certificate_der(_FakeWebsocket(None)) is None


def test_live_certificate_is_none_without_an_ssl_object() -> None:
    assert live_peer_certificate_der(_FakeWebsocket(_FakeTransport(None))) is None


def test_live_certificate_is_none_when_the_peer_presented_none() -> None:
    websocket = _FakeWebsocket(_FakeTransport(_FakeSSL(b"")))
    assert live_peer_certificate_der(websocket) is None


def test_live_certificate_returns_the_peer_der() -> None:
    websocket = _FakeWebsocket(_FakeTransport(_FakeSSL(b"der-bytes")))
    assert live_peer_certificate_der(websocket) == b"der-bytes"


def test_default_certificate_source_is_the_live_reader() -> None:
    policy = MultiHubServingPolicy(
        federation=FederationBundle(),
        mtls=MTLSPeerTrustBundle(peers={}),
        grants={},
        clock=lambda: 0.0,
    )
    assert policy.cert_source is live_peer_certificate_der


# --- MultiHubServingPolicy.authorise -----------------------------------------------------


def test_authorises_a_trusted_peer(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    decision = _policy(pin, _der(certfile)).authorise(sender=_SENDER, websocket=object())
    assert decision.allowed
    assert decision.reason == AUTHORISED
    assert decision.scope == _SCOPE


def test_refuses_a_sender_without_a_grant(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    decision = _policy(pin, _der(certfile)).authorise(sender="stranger", websocket=object())
    assert not decision.allowed
    assert decision.reason == MTLSVerificationResult.UNKNOWN_PEER.value


def test_refuses_a_connection_with_no_certificate(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    decision = _policy(pin, None).authorise(sender=_SENDER, websocket=object())
    assert not decision.allowed
    assert decision.reason == MTLSVerificationResult.MISSING_CERTIFICATE.value


def test_refuses_an_unparsable_certificate(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    decision = _policy(pin, b"not a certificate").authorise(sender=_SENDER, websocket=object())
    assert not decision.allowed
    assert decision.reason == MTLSVerificationResult.MISSING_CERTIFICATE.value


def test_refuses_a_live_certificate_the_policy_does_not_pin(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    other = _write_cert(tmp_path / "other")
    pin = certificate_sha256_pin(certfile)
    # The peer is trusted for ``certfile``'s pin but presents ``other``'s certificate live, so
    # the live pin is rejected — by the federation layer first, which the reason ladder reports.
    decision = _policy(pin, _der(other)).authorise(sender=_SENDER, websocket=object())
    assert not decision.allowed
    assert decision.reason == FederationDenyReason.CERTIFICATE_PIN_NOT_ACCEPTED


def test_refuses_when_federation_does_not_grant_the_namespace(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    federation = _federation(pin, namespaces=frozenset({"OTHER"}))
    policy = _policy(pin, _der(certfile), federation=federation)
    decision = policy.authorise(sender=_SENDER, websocket=object())
    assert not decision.allowed
    assert decision.reason == FederationDenyReason.NAMESPACE_NOT_GRANTED


def test_clock_is_sampled_per_request(tmp_path: Path) -> None:
    certfile = _write_cert(tmp_path)
    pin = certificate_sha256_pin(certfile)
    federation = FederationBundle(
        [
            FederationPeer(
                domain_id=_DOMAIN,
                namespaces=frozenset({_NAMESPACE}),
                certificate_pins=frozenset({pin}),
                signing_key_ids=frozenset({_KEY}),
                scope_grants=_SCOPE,
                expires_at=10.0,
            )
        ]
    )
    times = iter([5.0, 20.0])
    policy = _policy(pin, _der(certfile), federation=federation, clock=lambda: next(times))

    assert policy.authorise(sender=_SENDER, websocket=object()).allowed
    later = policy.authorise(sender=_SENDER, websocket=object())
    assert not later.allowed
    assert later.reason == FederationDenyReason.EXPIRED_PEERING
