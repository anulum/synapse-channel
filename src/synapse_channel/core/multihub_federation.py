# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deny-by-default authorisation for a cross-host multi-hub pull
"""Deny-by-default authorisation for a cross-host multi-hub event-log pull.

The transport (:mod:`synapse_channel.core.multihub_transport`) can follow a peer hub over a
real connection, but a follower must only pull from a peer an operator has explicitly granted.
This module composes the existing trust primitives into one decision for that case, joining the
federation policy (:class:`~synapse_channel.core.federation.FederationBundle`) with mutual-TLS
peer verification (:meth:`~synapse_channel.core.tls.MTLSPeerTrustBundle.verify_peer_certificate`)
through the composition law :func:`~synapse_channel.core.federation.compose_cross_domain` — a
pull is permitted only when *every* layer permits it, and federation never widens a check.

The result is deny-by-default and fail-closed: an unknown, revoked, or expired peering, a
namespace the peering does not grant, a certificate whose pin is not accepted, or a certificate
file that cannot even be loaded all refuse the pull. :func:`peer_authoriser` binds a credential
into the zero-argument gate the transport calls before each fetch, sampling the clock per call
so a peering's expiry and revocation are re-evaluated on every poll rather than once at
start-up. The module is pure of the network: it verifies an operator-pinned peer certificate
file and policy, leaving the live connection to the transport.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from synapse_channel.core.federation import (
    AUTHORISED,
    FederationBundle,
    ScopeGrant,
    compose_cross_domain,
)
from synapse_channel.core.tls import (
    HubTLSConfigError,
    MTLSPeerTrustBundle,
    MTLSVerificationResult,
    certificate_sha256_pin,
)

SIGNATURE_UNVERIFIED = "signature_unverified"
"""Refusal reason when an event-signature check is required but not satisfied."""

ACL_DENIED = "acl_denied"
"""Refusal reason when the local ACL denies the mapped scope."""


@dataclass(frozen=True)
class MultiHubPeerCredential:
    """The operator-pinned identity a peer hub is followed under.

    Attributes
    ----------
    domain_id : str
        The peer's trust-domain id, looked up in both the federation and mTLS bundles.
    namespace : str
        The local namespace whose log is being pulled; the peering must grant it.
    signing_key_id : str
        The peer's event-signing key id, which both bundles must accept.
    certfile : str or pathlib.Path
        The peer hub's pinned PEM/DER certificate file, hashed to the pin the bundles check.
    """

    domain_id: str
    namespace: str
    signing_key_id: str
    certfile: str | Path


@dataclass(frozen=True)
class MultiHubAuthorisation:
    """The composed outcome of authorising a multi-hub pull from a peer.

    Attributes
    ----------
    allowed : bool
        Whether every layer permits the pull.
    reason : str
        :data:`~synapse_channel.core.federation.AUTHORISED` on success, otherwise the first
        refusing layer's reason (a federation deny reason, an mTLS result, or a local one).
    scope : tuple[ScopeGrant, ...]
        The bounded local scope the peering maps for the namespace when allowed; empty on a deny.
    """

    allowed: bool
    reason: str
    scope: tuple[ScopeGrant, ...] = field(default_factory=tuple)


def authorise_multihub_pull(
    *,
    federation: FederationBundle,
    mtls: MTLSPeerTrustBundle,
    credential: MultiHubPeerCredential,
    now: float,
    signature_ok: bool = True,
    acl_ok: bool = True,
) -> MultiHubAuthorisation:
    """Authorise a multi-hub pull from a peer by composing every trust layer.

    The certificate file is hashed to a pin once; an unloadable file fails closed before any
    policy is consulted. The federation policy and mutual-TLS verification then run, and
    :func:`~synapse_channel.core.federation.compose_cross_domain` permits the pull only when
    both — and the optional event-signature and ACL checks — allow it.

    Parameters
    ----------
    federation : FederationBundle
        The operator's peered-domain policy.
    mtls : MTLSPeerTrustBundle
        The operator's mutual-TLS peer trust bundle.
    credential : MultiHubPeerCredential
        The pinned peer identity being verified.
    now : float
        Current UNIX epoch time used to evaluate peering expiry.
    signature_ok : bool, optional
        Whether an event-signature check is satisfied. Defaults to ``True``; the
        connection-establishment gate does not require per-event signing, but a caller may
        tighten it.
    acl_ok : bool, optional
        Whether the local ACL permits the mapped scope. Defaults to ``True``.

    Returns
    -------
    MultiHubAuthorisation
        The composed decision; ``allowed`` is ``True`` only when every layer permits the pull.
    """
    try:
        pin = certificate_sha256_pin(credential.certfile)
    except HubTLSConfigError:
        return MultiHubAuthorisation(
            allowed=False, reason=MTLSVerificationResult.MISSING_CERTIFICATE.value
        )

    return authorise_multihub_peer(
        federation=federation,
        mtls=mtls,
        certificate_pin=pin,
        domain_id=credential.domain_id,
        namespace=credential.namespace,
        signing_key_id=credential.signing_key_id,
        now=now,
        signature_ok=signature_ok,
        acl_ok=acl_ok,
    )


def authorise_multihub_peer(
    *,
    federation: FederationBundle,
    mtls: MTLSPeerTrustBundle,
    certificate_pin: str,
    domain_id: str,
    namespace: str,
    signing_key_id: str,
    now: float,
    signature_ok: bool = True,
    acl_ok: bool = True,
) -> MultiHubAuthorisation:
    """Compose every trust layer for a multi-hub peer whose certificate pin is already known.

    The pin-based core of :func:`authorise_multihub_pull`. The following side computes the pin
    from an operator-pinned certificate file; the serving side computes it from the certificate
    the peer presents on the live mutual-TLS socket. Both then share this composition: the
    federation policy and mutual-TLS pin verification run, and
    :func:`~synapse_channel.core.federation.compose_cross_domain` permits the pull only when
    both — and the optional event-signature and ACL checks — allow it. The reason on a deny is
    the first refusing layer's, in federation-then-mTLS-then-signature-then-ACL order.

    Parameters
    ----------
    federation : FederationBundle
        The operator's peered-domain policy.
    mtls : MTLSPeerTrustBundle
        The operator's mutual-TLS peer trust bundle.
    certificate_pin : str
        The peer certificate's SHA-256 pin in ``sha256:<hex>`` form.
    domain_id : str
        The peer's trust-domain id, looked up in both bundles.
    namespace : str
        The local namespace the pull concerns; the peering must grant it.
    signing_key_id : str
        The peer's event-signing key id, which both bundles must accept.
    now : float
        Current UNIX epoch time used to evaluate peering expiry.
    signature_ok : bool, optional
        Whether an event-signature check is satisfied. Defaults to ``True``.
    acl_ok : bool, optional
        Whether the local ACL permits the mapped scope. Defaults to ``True``.

    Returns
    -------
    MultiHubAuthorisation
        The composed decision; ``allowed`` is ``True`` only when every layer permits the pull.
    """
    decision = federation.authorise(
        domain_id,
        namespace=namespace,
        signing_key_id=signing_key_id,
        certificate_pin=certificate_pin,
        now=now,
    )
    mtls_result = mtls.verify_peer_pin(
        domain_id,
        pin=certificate_pin,
        project=namespace,
        signing_key_id=signing_key_id,
    )
    mtls_ok = mtls_result == MTLSVerificationResult.VALID
    allowed = compose_cross_domain(
        decision, mtls_ok=mtls_ok, signature_ok=signature_ok, acl_ok=acl_ok
    )
    if allowed:
        return MultiHubAuthorisation(allowed=True, reason=AUTHORISED, scope=decision.scope)
    if not decision.allowed:
        reason = decision.reason
    elif not mtls_ok:
        reason = mtls_result.value
    elif not signature_ok:
        reason = SIGNATURE_UNVERIFIED
    else:
        reason = ACL_DENIED
    return MultiHubAuthorisation(allowed=False, reason=reason)


MultiHubAuthoriser = Callable[[], MultiHubAuthorisation]
"""A zero-argument gate the transport consults before a fetch."""


def peer_authoriser(
    *,
    federation: FederationBundle,
    mtls: MTLSPeerTrustBundle,
    credential: MultiHubPeerCredential,
    clock: Callable[[], float],
    signature_ok: bool = True,
    acl_ok: bool = True,
) -> MultiHubAuthoriser:
    """Bind a credential into the gate the transport calls before each fetch.

    The returned callable samples ``clock`` on every call, so a peering's expiry and revocation
    are re-evaluated on each poll rather than fixed when the follower started.

    Parameters
    ----------
    federation : FederationBundle
        The operator's peered-domain policy.
    mtls : MTLSPeerTrustBundle
        The operator's mutual-TLS peer trust bundle.
    credential : MultiHubPeerCredential
        The pinned peer identity authorised on each call.
    clock : Callable[[], float]
        Returns the current UNIX epoch time; sampled per call.
    signature_ok : bool, optional
        Forwarded to :func:`authorise_multihub_pull`. Defaults to ``True``.
    acl_ok : bool, optional
        Forwarded to :func:`authorise_multihub_pull`. Defaults to ``True``.

    Returns
    -------
    MultiHubAuthoriser
        A zero-argument callable returning the current :class:`MultiHubAuthorisation`.
    """

    def authorise() -> MultiHubAuthorisation:
        return authorise_multihub_pull(
            federation=federation,
            mtls=mtls,
            credential=credential,
            now=clock(),
            signature_ok=signature_ok,
            acl_ok=acl_ok,
        )

    return authorise
