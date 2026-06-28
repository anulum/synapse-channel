# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub TLS context construction
"""TLS helpers for native ``wss://`` hub deployment."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization


class HubTLSConfigError(ValueError):
    """Raised when the hub TLS certificate configuration is incomplete or invalid."""


class MTLSVerificationResult(str, Enum):
    """Stable mutual-TLS peer verification result strings."""

    VALID = "valid"
    MISSING_CERTIFICATE = "missing_certificate"
    UNKNOWN_PEER = "unknown_peer"
    REVOKED_PEER = "revoked_peer"
    BAD_CERTIFICATE_PIN = "bad_certificate_pin"
    PROJECT_SCOPE_MISMATCH = "project_scope_mismatch"
    UNKNOWN_SIGNING_KEY = "unknown_signing_key"


@dataclass(frozen=True)
class MTLSTrustedPeer:
    """One operator-managed trusted peer entry.

    Parameters
    ----------
    peer_id : str
        Stable peer identifier.
    certificate_pins : frozenset[str]
        Accepted certificate SHA-256 pins in ``sha256:<hex>`` form.
    signing_key_ids : frozenset[str]
        Event-signing key ids this peer may use.
    projects : frozenset[str]
        Local project namespaces this peer may address.
    revoked : bool, optional
        When ``True``, the peer fails verification with ``revoked_peer``.
    """

    peer_id: str
    certificate_pins: frozenset[str]
    signing_key_ids: frozenset[str]
    projects: frozenset[str]
    revoked: bool = False


@dataclass(frozen=True)
class MTLSPeerTrustBundle:
    """Operator-managed mutual-TLS peer trust bundle.

    Parameters
    ----------
    peers : Mapping[str, MTLSTrustedPeer]
        Trusted peer entries keyed by peer id.
    """

    peers: dict[str, MTLSTrustedPeer]

    def verify_peer_certificate(
        self,
        peer_id: str,
        *,
        certfile: str | Path,
        project: str,
        signing_key_id: str,
    ) -> MTLSVerificationResult:
        """Verify a peer certificate pin, project scope, and signing key.

        Parameters
        ----------
        peer_id : str
            Peer id expected in the trust bundle.
        certfile : str or pathlib.Path
            PEM certificate file presented by the peer.
        project : str
            Local project namespace for the connection or event.
        signing_key_id : str
            Event-signing key id associated with this peer.

        Returns
        -------
        MTLSVerificationResult
            Stable result describing success or the refusal reason.
        """
        peer = self.peers.get(peer_id)
        if peer is None:
            return MTLSVerificationResult.UNKNOWN_PEER
        if peer.revoked:
            return MTLSVerificationResult.REVOKED_PEER
        if project not in peer.projects:
            return MTLSVerificationResult.PROJECT_SCOPE_MISMATCH
        if signing_key_id not in peer.signing_key_ids:
            return MTLSVerificationResult.UNKNOWN_SIGNING_KEY
        try:
            pin = certificate_sha256_pin(certfile)
        except HubTLSConfigError:
            return MTLSVerificationResult.MISSING_CERTIFICATE
        if pin not in peer.certificate_pins:
            return MTLSVerificationResult.BAD_CERTIFICATE_PIN
        return MTLSVerificationResult.VALID


def _load_certificate_der(certfile: str | Path) -> bytes:
    """Load a PEM or DER certificate file and return DER bytes."""
    path = Path(certfile)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HubTLSConfigError(f"could not load peer certificate: {exc}") from exc
    try:
        certificate = x509.load_pem_x509_certificate(data)
    except ValueError:
        try:
            certificate = x509.load_der_x509_certificate(data)
        except ValueError as exc:
            raise HubTLSConfigError("could not parse peer certificate") from exc
    return certificate.public_bytes(serialization.Encoding.DER)


def certificate_sha256_pin(certfile: str | Path) -> str:
    """Return the SHA-256 certificate pin for a PEM or DER certificate.

    Parameters
    ----------
    certfile : str or pathlib.Path
        Certificate file to hash after DER canonicalisation.

    Returns
    -------
    str
        Pin formatted as ``sha256:<hex>``.
    """
    return "sha256:" + sha256(_load_certificate_der(certfile)).hexdigest()


def build_server_ssl_context(
    *, certfile: str | Path | None, keyfile: str | Path | None
) -> ssl.SSLContext | None:
    """Build a server-side SSL context for native WSS.

    Parameters
    ----------
    certfile : str or pathlib.Path or None
        PEM certificate chain file passed to ``SSLContext.load_cert_chain``.
    keyfile : str or pathlib.Path or None
        PEM private-key file paired with ``certfile``.

    Returns
    -------
    ssl.SSLContext or None
        A TLS server context when both paths are supplied, otherwise ``None``
        when TLS is disabled.

    Raises
    ------
    HubTLSConfigError
        If only one path is supplied or the certificate chain cannot be loaded.
    """
    if certfile is None and keyfile is None:
        return None
    if certfile is None or keyfile is None:
        raise HubTLSConfigError("native WSS requires both --tls-certfile and --tls-keyfile")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    except (OSError, ssl.SSLError) as exc:
        raise HubTLSConfigError(f"could not load hub TLS certificate chain: {exc}") from exc
    return context


def build_mutual_tls_server_ssl_context(
    *,
    certfile: str | Path | None,
    keyfile: str | Path | None,
    client_ca_file: str | Path | None,
) -> ssl.SSLContext:
    """Build a server-side SSL context requiring client certificates.

    Parameters
    ----------
    certfile : str or pathlib.Path or None
        PEM certificate chain file for the hub.
    keyfile : str or pathlib.Path or None
        PEM private-key file paired with ``certfile``.
    client_ca_file : str or pathlib.Path or None
        CA bundle used to verify client certificates.

    Returns
    -------
    ssl.SSLContext
        TLS server context with ``CERT_REQUIRED`` client verification.

    Raises
    ------
    HubTLSConfigError
        If certificate material or the client CA cannot be loaded.
    """
    if client_ca_file is None:
        raise HubTLSConfigError("mutual TLS requires --mtls-client-ca-file")
    context = build_server_ssl_context(certfile=certfile, keyfile=keyfile)
    if context is None:
        raise HubTLSConfigError("mutual TLS requires --tls-certfile and --tls-keyfile")
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = False
    try:
        context.load_verify_locations(cafile=str(client_ca_file))
    except (OSError, ssl.SSLError) as exc:
        raise HubTLSConfigError(f"could not load mTLS client CA bundle: {exc}") from exc
    return context
