# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serving-side deny-by-default gate for a cross-host multi-hub pull
"""Serving-side deny-by-default gate for a cross-host multi-hub event-log pull.

The following side already refuses to pull from a peer an operator has not granted
(:func:`~synapse_channel.core.multihub_federation.peer_authoriser`). This module is the mirror
on the *serving* side: a hub configured with a :class:`MultiHubServingPolicy` refuses to serve
its event log to a peer it does not trust, deciding from the certificate the peer presents on
the *live* mutual-TLS connection rather than an operator-pinned file.

The two sides compose the *same* trust law. The following side hashes an operator-pinned
certificate file to a pin; this side hashes the certificate read off the live socket
(:func:`~synapse_channel.core.tls.certificate_sha256_pin_from_der`); both then run the shared
:func:`~synapse_channel.core.multihub_federation.authorise_multihub_peer` composition of the
federation policy and mutual-TLS pin verification. The gate is deny-by-default and fail-closed:
a peer with no operator-configured grant, a connection presenting no client certificate, or a
certificate whose pin the policy does not accept all refuse the serve. A hub with no policy
configured serves as before, so the gate is strictly opt-in and changes no default deployment.

The module is pure of the wire protocol and of the hub: it reads the live socket only through a
small, injectable :data:`PeerCertificateSource`, so a test can drive the full decision without a
real mutual-TLS handshake while production uses :func:`live_peer_certificate_der`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.federation import FederationBundle
from synapse_channel.core.multihub_federation import (
    MultiHubAuthorisation,
    authorise_multihub_peer,
)
from synapse_channel.core.tls import (
    HubTLSConfigError,
    MTLSPeerTrustBundle,
    MTLSVerificationResult,
    certificate_sha256_pin_from_der,
)

PeerCertificateSource = Callable[[Any], bytes | None]
"""Reads the peer's DER certificate off a live connection, or ``None`` when there is none."""


def live_peer_certificate_der(websocket: Any) -> bytes | None:
    """Return the peer's DER certificate from a live (mutual-)TLS socket, or ``None``.

    Reaches through the connection's asyncio transport to the negotiated
    :class:`ssl.SSLObject` and returns ``getpeercert(binary_form=True)``. Every step is guarded:
    a plaintext connection, a transport without the extra info, or a peer that presented no
    certificate all return ``None`` rather than raising, so the gate fails closed on the caller's
    side.

    Parameters
    ----------
    websocket : Any
        The serving-side connection the request arrived on.

    Returns
    -------
    bytes or None
        The peer's DER certificate bytes, or ``None`` when none is available.
    """
    transport = getattr(websocket, "transport", None)
    get_extra_info = getattr(transport, "get_extra_info", None)
    if get_extra_info is None:
        return None
    ssl_object = get_extra_info("ssl_object")
    if ssl_object is None:
        return None
    der = ssl_object.getpeercert(binary_form=True)
    return der or None


@dataclass(frozen=True)
class MultiHubServingGrant:
    """The trust-domain identity an operator grants one requesting peer to pull under.

    Attributes
    ----------
    domain_id : str
        The peer's trust-domain id, looked up in both the federation and mutual-TLS bundles.
    namespace : str
        The local namespace whose log the peer may pull; the peering must grant it.
    signing_key_id : str
        The peer's event-signing key id, which both bundles must accept.
    """

    domain_id: str
    namespace: str
    signing_key_id: str


@dataclass(frozen=True)
class MultiHubServingPolicy:
    """An operator's deny-by-default policy for serving the event log to peer hubs.

    Attributes
    ----------
    federation : FederationBundle
        The peered-domain policy shared with the following side.
    mtls : MTLSPeerTrustBundle
        The mutual-TLS peer trust bundle the live certificate pin is checked against.
    grants : Mapping[str, MultiHubServingGrant]
        The identity each requesting peer is authorised under, keyed by the sender id the peer
        registers as. A request from a sender with no grant is refused.
    clock : Callable[[], float]
        Returns the current POSIX wall-clock time, equivalent to ``time.time()``;
        sampled per request so epoch-based peering expiry and revocation are
        re-evaluated on every pull.
    cert_source : PeerCertificateSource
        Reads the peer's live certificate. Defaults to :func:`live_peer_certificate_der`;
        injected in tests to exercise the decision without a real handshake.
    signature_ok : bool
        Forwarded to :func:`authorise_multihub_peer`. Defaults to ``True``; the
        connection-establishment gate does not require per-event signing.
    acl_ok : bool
        Forwarded to :func:`authorise_multihub_peer`. Defaults to ``True``.
    """

    federation: FederationBundle
    mtls: MTLSPeerTrustBundle
    grants: Mapping[str, MultiHubServingGrant]
    clock: Callable[[], float]
    cert_source: PeerCertificateSource = field(default=live_peer_certificate_der)
    signature_ok: bool = True
    acl_ok: bool = True

    def authorise(self, *, sender: str, websocket: Any) -> MultiHubAuthorisation:
        """Decide whether ``sender`` may pull this hub's log over ``websocket``.

        The sender must have an operator-configured grant, the live connection must present a
        client certificate, and that certificate must pass the shared
        :func:`authorise_multihub_peer` composition for the granted identity. The first failure
        refuses, fail-closed.

        Parameters
        ----------
        sender : str
            The requesting peer's registered id.
        websocket : Any
            The serving-side connection the request arrived on, read through
            :attr:`cert_source`.

        Returns
        -------
        MultiHubAuthorisation
            ``allowed`` is ``True`` only when the grant, the live certificate, and every trust
            layer permit the pull.
        """
        grant = self.grants.get(sender)
        if grant is None:
            return MultiHubAuthorisation(
                allowed=False, reason=MTLSVerificationResult.UNKNOWN_PEER.value
            )
        der = self.cert_source(websocket)
        if der is None:
            return MultiHubAuthorisation(
                allowed=False, reason=MTLSVerificationResult.MISSING_CERTIFICATE.value
            )
        try:
            pin = certificate_sha256_pin_from_der(der)
        except HubTLSConfigError:
            return MultiHubAuthorisation(
                allowed=False, reason=MTLSVerificationResult.MISSING_CERTIFICATE.value
            )
        return authorise_multihub_peer(
            federation=self.federation,
            mtls=self.mtls,
            certificate_pin=pin,
            domain_id=grant.domain_id,
            namespace=grant.namespace,
            signing_key_id=grant.signing_key_id,
            now=self.clock(),
            signature_ok=self.signature_ok,
            acl_ok=self.acl_ok,
        )
