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
configured also refuses every peer: serving is fail-closed until an operator supplies exact
sender/domain/namespace/signing-key grants and live-certificate trust.

A grant may opt in to an identity key instead of the certificate: the hub then accepts a
registration it verified against its identity trust bundle under that key, which survives a
TLS-terminating proxy.

The module is pure of the wire protocol and of the hub: it reads the live socket only through a
small, injectable :data:`PeerCertificateSource`, so a test can drive the full decision without a
real mutual-TLS handshake while production uses :func:`live_peer_certificate_der`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.federation import FederationBundle
from synapse_channel.core.message_auth import EventSignatureTrustBundle
from synapse_channel.core.multihub_federation import (
    MultiHubAuthorisation,
    authorise_multihub_identity_peer,
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

PeerIdentitySource = Callable[[Any], tuple[str, str] | None]
"""Returns ``(sender, key_id)`` of a connection's operator-verified registration, or ``None``."""


def no_peer_identity(_websocket: Any) -> tuple[str, str] | None:
    """Report no verified registration; the default until a hub injects its own source."""
    return None


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
    identity_key_id : str or None
        Opt-in alternative to the client certificate. When set, a connection whose
        registration the hub verified against its identity trust bundle under exactly this
        key id, for this grant's sender, is proven without a certificate. A
        TLS-terminating proxy removes the certificate but carries the signature.
    """

    domain_id: str
    namespace: str
    signing_key_id: str
    identity_key_id: str | None = None


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
    identity_source : PeerIdentitySource
        Reads the connection's operator-verified registration. Defaults to
        :func:`no_peer_identity`; the hub injects its own record, so a policy used outside
        a hub never treats a connection as identity-proven.
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
    identity_source: PeerIdentitySource = field(default=no_peer_identity)
    signature_ok: bool = True
    acl_ok: bool = True

    def authorise(self, *, sender: str, websocket: Any) -> MultiHubAuthorisation:
        """Decide whether ``sender`` may pull this hub's log over ``websocket``.

        The sender must have an operator-configured grant, and the connection must prove it:
        either the live client certificate passes the shared :func:`authorise_multihub_peer`
        composition, or, for a grant naming an ``identity_key_id``, the hub verified this
        sender's registration under that key and :func:`authorise_multihub_identity_peer`
        passes. The first failure refuses, fail-closed.

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
        return self._authorise_grant(sender, grant, websocket, namespace=grant.namespace)

    def authorise_namespace(
        self, *, sender: str, websocket: Any, namespace: str
    ) -> MultiHubAuthorisation:
        """Decide whether ``sender`` may address ``namespace`` on this hub over ``websocket``.

        The same composition as :meth:`authorise`, evaluated for one target namespace
        instead of the grant's own: the sender needs an operator grant and a live, pinned
        client certificate, and its federation peering must list ``namespace`` among the
        local namespaces it may address. Cross-hub message forwarding calls this once per
        target, so a peer reaches only the projects its peering names.

        Parameters
        ----------
        sender : str
            The requesting peer's registered id.
        websocket : Any
            The serving-side connection the request arrived on.
        namespace : str
            The local project namespace the request addresses.

        Returns
        -------
        MultiHubAuthorisation
            ``allowed`` is ``True`` only when every layer permits ``namespace``.
        """
        grant = self.grants.get(sender)
        if grant is None:
            return MultiHubAuthorisation(
                allowed=False, reason=MTLSVerificationResult.UNKNOWN_PEER.value
            )
        return self._authorise_grant(sender, grant, websocket, namespace=namespace)

    def _authorise_grant(
        self, sender: str, grant: MultiHubServingGrant, websocket: Any, *, namespace: str
    ) -> MultiHubAuthorisation:
        """Check the connection's proof and every trust layer for one granted namespace.

        A grant naming an identity key is satisfied by a registration the hub verified under
        that key for this sender; otherwise the live certificate is required, as before.
        """
        if grant.identity_key_id is not None and self.identity_source(websocket) == (
            sender,
            grant.identity_key_id,
        ):
            return authorise_multihub_identity_peer(
                federation=self.federation,
                domain_id=grant.domain_id,
                namespace=namespace,
                signing_key_id=grant.signing_key_id,
                now=self.clock(),
                signature_ok=self.signature_ok,
                acl_ok=self.acl_ok,
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
            namespace=namespace,
            signing_key_id=grant.signing_key_id,
            now=self.clock(),
            signature_ok=self.signature_ok,
            acl_ok=self.acl_ok,
        )


def check_identity_grants(
    policy: MultiHubServingPolicy,
    *,
    identity_trust_bundle: EventSignatureTrustBundle | None,
    require_identity_binding: bool,
) -> None:
    """Refuse a policy whose identity-key grants the hub could never satisfy.

    A grant naming an ``identity_key_id`` is proven only by a registration the hub
    verified against its identity trust bundle, so the hub must require identity binding,
    and that bundle must hold the key, unrevoked, bound to the grant's sender. Otherwise
    the peer would be admitted but refused every request, silently.

    Raises
    ------
    ValueError
        Naming the first grant that cannot be satisfied.
    """
    for sender, grant in sorted(policy.grants.items()):
        if grant.identity_key_id is None:
            continue
        if not require_identity_binding or identity_trust_bundle is None:
            raise ValueError(
                f"serving grant {sender!r} names an identity_key_id, but the hub does not "
                "verify registrations against an identity trust bundle: pass --identity-trust "
                "with --require-identity-binding"
            )
        key = identity_trust_bundle.keys.get(grant.identity_key_id)
        if key is None or key.revoked or sender not in key.senders:
            raise ValueError(
                f"serving grant {sender!r} identity key {grant.identity_key_id!r} is not "
                "enrolled, unrevoked, for that sender in the identity trust bundle"
            )
