# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — classify inbound frames as local or federated cross-domain
"""Federation frame gate for the routing hub.

:class:`HubFederationGate` owns the cross-domain decision for one inbound frame:
resolving the frame's Ed25519 ``signature.key_id`` together with the **live** peer
certificate pin to a single peered domain, composing the deny-closed cross-domain
authorisation (peering policy, mutual-TLS pin, verified event signature, bounded
scope), and warning the operator when a signed, pinned frame resolves to no peering
because of a misconfigured key or certificate. The gate captures the hub's
federation configuration at construction — the bundle, the certificate source, and
the per-message-authentication posture are hub constructor arguments that are never
reassigned — and takes the hub's system-message factory and single-socket sender as
injected callbacks, so it carries no back-reference to the hub, the same
callback-injection :class:`~synapse_channel.core.hub_broadcast.HubBroadcaster` uses.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from synapse_channel.core.acl_enforcement import project_of, required_accesses
from synapse_channel.core.federation import (
    DomainResolutionDiagnosis,
    FederationBundle,
    compose_cross_domain,
    diagnose_unresolved_domain,
    resolve_domain,
    scope_authorises,
)
from synapse_channel.core.message_auth import DEFAULT_SIGNED_MESSAGE_TYPES
from synapse_channel.core.multihub_serving import PeerCertificateSource
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import certificate_sha256_pin_from_der

logger = logging.getLogger("synapse.hub")

__all__ = [
    "FrameDisposition",
    "HubFederationGate",
]


class FrameDisposition(Enum):
    """How the federation gate classifies an inbound frame.

    ``LOCAL`` — no federation bundle, or the frame resolves to no peered domain, so it
    takes the ordinary local authorisation path unchanged. ``ALLOW_CROSS_DOMAIN`` — the
    frame is a peered remote domain's, authorised by the federation policy and its mapped
    scope, and is routed without the local ACL (a remote subject has no local identity).
    ``DENY`` — the frame is cross-domain and some layer refused it; it is not routed.
    """

    LOCAL = "local"
    ALLOW_CROSS_DOMAIN = "allow_cross_domain"
    DENY = "deny"


class HubFederationGate:
    """Classify inbound frames as local or cross-domain and authorise the latter.

    Parameters
    ----------
    bundle : FederationBundle or None
        The hub's federation policy. ``None`` disables the gate: every frame is
        :attr:`FrameDisposition.LOCAL` and nothing else is evaluated.
    cert_source : PeerCertificateSource
        Reads the live peer certificate (DER) off a websocket, or ``None`` when the
        connection presented no client certificate.
    require_per_message_auth : bool
        Whether the hub demands per-message authentication; a cross-domain frame can
        bind authority only when its event signature was *required* and verified.
    signed_event_trust : bool
        Whether the hub holds an Ed25519 event-signature trust bundle; without one no
        signature can have been verified, so no cross-domain frame is authorised.
    system : Callable[..., dict]
        The hub's system-message factory (``hub._system``), used to stamp the denial
        error returned to a refused sender.
    send_json : Callable[[Any, dict], Awaitable[None]]
        Sends one message to a single socket (``hub._send_json``); used for the
        denial error.
    """

    def __init__(
        self,
        bundle: FederationBundle | None,
        *,
        cert_source: PeerCertificateSource,
        require_per_message_auth: bool,
        signed_event_trust: bool,
        system: Callable[..., dict[str, Any]],
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._bundle = bundle
        self._cert_source = cert_source
        self._require_per_message_auth = require_per_message_auth
        self._signed_event_trust = signed_event_trust
        self._system = system
        self._send_json = send_json

    async def authorise(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> FrameDisposition:
        """Classify a frame as local or cross-domain and authorise the cross-domain case.

        With no :class:`~synapse_channel.core.federation.FederationBundle` configured the
        gate is a no-op and every frame is :attr:`FrameDisposition.LOCAL`. Otherwise a frame
        is cross-domain only when its Ed25519 ``signature.key_id`` and the **live** peer
        certificate pin read off the socket resolve, together, to a single peered domain
        (:func:`~synapse_channel.core.federation.resolve_domain`); the key and pin must
        belong to the same peer, and neither is ever taken from a self-declared field. A
        frame that resolves to no peered domain — unsigned, HMAC-authenticated, presented on
        a plaintext socket, or signed with a local key — is :attr:`FrameDisposition.LOCAL`
        and takes the ordinary path unchanged.

        A cross-domain frame is authorised deny-closed by composing the peering policy
        (peered, active, namespace granted, key and pin accepted), the live mutual-TLS pin,
        the event signature (which must have been *required* and verified — a cross-domain
        frame on a hub without per-message authentication cannot bind authority and is
        refused), and the peering's bounded scope evaluated against the frame's required
        accesses. When every layer allows it the frame is
        :attr:`FrameDisposition.ALLOW_CROSS_DOMAIN` and is routed without the local ACL — a
        remote subject has no local ACL identity. Any layer refusing yields
        :attr:`FrameDisposition.DENY`, an error naming the reason is returned, and the frame
        is not routed.
        """
        if self._bundle is None:
            return FrameDisposition.LOCAL
        signature = data.get("signature")
        key_id = str(signature.get("key_id") or "").strip() if isinstance(signature, dict) else ""
        if not key_id:
            return FrameDisposition.LOCAL
        try:
            der = self._cert_source(websocket)
        except Exception:
            # A certificate read can raise on a socket that has closed or never
            # finished its TLS handshake, and an injected source is arbitrary code.
            # A cross-domain frame whose peer we cannot pin degrades to the local
            # path — exactly as an absent certificate does — rather than crashing
            # the frame handler for that connection.
            logger.warning(
                "Federation certificate read failed for %s (%s); handling frame as local",
                sender,
                msg_type,
            )
            return FrameDisposition.LOCAL
        if der is None:
            return FrameDisposition.LOCAL
        pin = certificate_sha256_pin_from_der(der)
        domain_id = resolve_domain(self._bundle, key_id=key_id, certificate_pin=pin)
        if domain_id is None:
            self.warn_unresolved(sender, msg_type, key_id, pin)
            return FrameDisposition.LOCAL
        namespace = project_of(sender)
        decision = self._bundle.authorise(
            domain_id,
            namespace=namespace,
            signing_key_id=key_id,
            certificate_pin=pin,
            now=time.time(),
        )
        signature_ok = (
            self._require_per_message_auth
            and msg_type in DEFAULT_SIGNED_MESSAGE_TYPES
            and "auth" not in data
            and self._signed_event_trust
        )
        acl_ok = scope_authorises(
            required_accesses(msg_type, data), scope=decision.scope, namespace=namespace
        )
        if compose_cross_domain(decision, mtls_ok=True, signature_ok=signature_ok, acl_ok=acl_ok):
            logger.info("Federation allowed %s from %s as domain %s", msg_type, sender, domain_id)
            return FrameDisposition.ALLOW_CROSS_DOMAIN
        if not decision.allowed:
            reason = decision.reason
        elif not signature_ok:
            reason = "signature_not_verified"
        else:
            reason = "out_of_scope"
        logger.warning(
            "Federation denied %s for %s (domain %s): %s", msg_type, sender, domain_id, reason
        )
        await self._send_json(
            websocket,
            self._system(
                f"federation denied: {reason}",
                msg_type=MessageType.ERROR,
                target=sender,
                federation_domain=domain_id,
                federation_reason=reason,
            ),
        )
        return FrameDisposition.DENY

    def warn_unresolved(self, sender: str, msg_type: str, key_id: str, pin: str) -> None:
        """Log a misconfiguration signal when a signed, pinned frame resolves to no domain.

        A cross-domain frame arrives signed and over a pinned connection, yet its key and
        certificate resolve to no single peering. Most such frames are ordinary — a
        locally signed frame is not cross-domain and rightly takes the local path — so the
        common case stays silent. But a peering whose signing key or certificate pin is
        missing, stale, or split across peerings otherwise leaves the operator no signal at
        all: the frame is simply handled locally and, lacking a local identity, usually
        denied downstream with no hint that a federation peering is misconfigured. When the
        diagnosis is one of those misconfigurations this logs a warning naming the reason;
        the frame's disposition is unchanged (still local).
        """
        if self._bundle is None:
            return
        diagnosis = diagnose_unresolved_domain(self._bundle, key_id=key_id, certificate_pin=pin)
        if diagnosis in (DomainResolutionDiagnosis.UNRELATED, DomainResolutionDiagnosis.RESOLVED):
            return
        logger.warning(
            "Federation frame from %s (%s) is signed with key %s over a pinned connection "
            "but resolves to no peered domain (%s); handling it locally. Check the "
            "peering's signing key id and certificate pin.",
            sender,
            msg_type,
            key_id,
            diagnosis,
        )
