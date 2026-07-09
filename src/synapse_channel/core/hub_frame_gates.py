# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authorise a frame through per-message-auth, ACL, and ownership gates
"""Frame-authorisation gates for the routing hub.

:class:`HubFrameGates` owns the checks a parsed, sender-resolved frame must pass
before the hub routes it: verifying required per-message authentication (an HMAC
frame signature or an Ed25519 signed-event signature), authorising a mutating frame
against the ACL, and routing a claim by namespace ownership — granting locally,
forwarding to the owning peer hub and relaying its verdict, or refusing fail-closed
with the owner named. Each gate returns whether the frame may proceed and, on a
refusal, sends the denial itself.

Routing is deliberately *not* here: dispatch stays on the hub because a handler is
called with the hub as its first argument, so keeping it on the hub avoids handing
this collaborator a back-reference. The gates instead take the hub's per-socket send
and system-message factory as injected callbacks and capture their policy inputs
(the auth keys and trust bundle, the ACL policy, the ownership map, the claim-peer
routes, and this hub's id) at construction, since the hub never mutates them after
``__init__`` — the same callback-injection
:class:`~synapse_channel.core.hub_broadcast.HubBroadcaster` uses. Denials are logged
through a logger named ``synapse.hub`` so their records stay under the hub's log
namespace.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from synapse_channel.core.acl import AclPolicy
from synapse_channel.core.acl_enforcement import authorise_frame, project_of
from synapse_channel.core.hub_counters import HubCounters
from synapse_channel.core.message_auth import (
    DEFAULT_SIGNED_MESSAGE_TYPES,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
    SignedEventVerificationResult,
    VerificationResult,
    verify_event_signature,
    verify_frame,
)
from synapse_channel.core.multihub_claim_transport import (
    ClaimForwarder,
    ClaimForwardError,
    ClaimForwardPeer,
    ClaimForwardTimeoutError,
)
from synapse_channel.core.multihub_claim_wire import ClaimForwardRequest
from synapse_channel.core.namespace_ownership import NamespaceOwnership, OwnershipOutcome
from synapse_channel.core.protocol import MessageType

logger = logging.getLogger("synapse.hub")


class HubFrameGates:
    """Authorise a frame through the per-message-auth, ACL, and ownership gates.

    Parameters
    ----------
    require_per_message_auth : bool
        When ``True``, a selected mutating frame must carry valid per-message
        authentication before it may mutate state.
    per_message_auth_keys : dict[str, MessageAuthKey]
        HMAC keys accepted for opt-in per-message authentication, keyed by key id.
    message_replay : MessageReplayCache
        The shared replay cache that rejects a re-sent signed frame; the same
        instance the hub holds, so freshness and nonce state survive across gates.
    signed_event_trust_bundle : EventSignatureTrustBundle or None
        Ed25519 trust bundle accepted as the signed-event verification path; ``None``
        leaves HMAC frame authentication as the only enforcing path.
    require_acl : bool
        Whether the ACL is enforced. With enforcement off (or no policy) every frame
        is allowed through the ACL gate.
    acl_policy : AclPolicy or None
        The policy a mutating frame is authorised against when enforcement is on.
    namespace_ownership : NamespaceOwnership or None
        The single-authoritative-hub map that routes claims by namespace ownership;
        ``None`` lets this hub grant claims in every namespace (single-hub behaviour).
    observed_asserting_hubs : Callable[[str], Iterable[str]] or None
        Runtime feed of hub ids observed asserting authority over a namespace, so a
        partition refuses every grant until it is re-established; ``None`` supplies no
        assertions and ownership resolves from the static map alone.
    claim_peers : dict[str, ClaimForwardPeer] or None
        How to reach each owning hub to forward a claim it owns, keyed by owning hub
        id; ``None`` forwards nothing and a remote-owned claim is refused with the
        owner named.
    claim_forwarder : ClaimForwarder
        The seam that forwards a claim to an owning hub over the network.
    counters : HubCounters
        Shared live hub counters, incremented for forwarded-claim attempts and outcomes.
    hub_id : str
        This hub's stable id, stamped as the forwarding hub's local id on a forwarded
        claim.
    send_json : Callable[[Any, dict], Awaitable[None]]
        The hub's per-socket send (``hub._send_json``), used to deliver each denial.
    system : Callable[..., dict]
        The hub's system-message factory (``hub._system``), used to stamp each denial
        with the hub id.
    """

    def __init__(
        self,
        *,
        require_per_message_auth: bool,
        per_message_auth_keys: dict[str, MessageAuthKey],
        message_replay: MessageReplayCache,
        signed_event_trust_bundle: EventSignatureTrustBundle | None,
        require_acl: bool,
        acl_policy: AclPolicy | None,
        namespace_ownership: NamespaceOwnership | None,
        observed_asserting_hubs: Callable[[str], Iterable[str]] | None,
        claim_peers: dict[str, ClaimForwardPeer] | None,
        claim_forwarder: ClaimForwarder,
        counters: HubCounters,
        hub_id: str,
        send_json: Callable[[Any, dict[str, Any]], Awaitable[None]],
        system: Callable[..., dict[str, Any]],
    ) -> None:
        self._require_per_message_auth = require_per_message_auth
        self._per_message_auth_keys = per_message_auth_keys
        self._message_replay = message_replay
        self._signed_event_trust_bundle = signed_event_trust_bundle
        self._require_acl = require_acl
        self._acl_policy = acl_policy
        self._namespace_ownership = namespace_ownership
        self._observed_asserting_hubs_feed = observed_asserting_hubs
        self._claim_peers = claim_peers
        self._claim_forwarder = claim_forwarder
        self._counters = counters
        self._hub_id = hub_id
        self._send_json = send_json
        self._system = system

    async def verify_per_message_auth(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Verify required per-message authentication before mutating state."""
        if not self._require_per_message_auth or msg_type not in DEFAULT_SIGNED_MESSAGE_TYPES:
            return True
        now = time.time()
        if "auth" in data:
            result: VerificationResult | SignedEventVerificationResult = verify_frame(
                data,
                keys=self._per_message_auth_keys,
                replay_cache=self._message_replay,
                now=now,
                required_sender=sender,
            )
            if result is VerificationResult.OK:
                return True
        elif "signature" in data and self._signed_event_trust_bundle is not None:
            result = verify_event_signature(
                data,
                trust_bundle=self._signed_event_trust_bundle,
                now=now,
                required_sender=sender,
                required_project=str(data.get("project") or ""),
            )
            if result is SignedEventVerificationResult.VALID:
                return True
        else:
            result = VerificationResult.MISSING
        await self._send_json(
            websocket,
            self._system(
                f"per-message authentication failed: {result.value}",
                msg_type=MessageType.ERROR,
                target=sender,
                verification_result=result.value,
            ),
        )
        return False

    async def authorise_acl(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Authorise a mutating frame against the ACL when enforcement is on.

        Returns ``True`` when enforcement is off, no policy is configured, or the
        frame is allowed (including ungated verbs). A denied frame is refused with
        an error naming the rule reason and is not routed.
        """
        if not self._require_acl or self._acl_policy is None:
            return True
        denial = authorise_frame(
            sender=sender, msg_type=msg_type, data=data, policy=self._acl_policy
        )
        if denial is None:
            return True
        logger.warning(
            "ACL denied %s for %s on %s:%s (%s)",
            msg_type,
            sender,
            denial.target.kind,
            denial.target.value,
            denial.reason,
        )
        await self._send_json(
            websocket,
            self._system(
                f"access denied: {denial.permission} on {denial.target.kind}:{denial.target.value}",
                msg_type=MessageType.ERROR,
                target=sender,
                acl_decision=denial.decision,
                acl_reason=denial.reason,
            ),
        )
        return False

    async def authorise_claim_ownership(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> bool:
        """Route a claim by namespace ownership: grant locally, forward to the owner, or refuse.

        Claims are mutual exclusion and are routed by namespace ownership, never merged: a hub
        grants claims only for the namespaces it owns, so two hubs never grant the same scope.
        When a :class:`~synapse_channel.core.namespace_ownership.NamespaceOwnership` map is
        configured, a claim whose namespace — derived from the sender identity exactly as the
        ACL derives it — this hub owns runs the local grant path. A namespace a named peer owns is
        forwarded to that peer when a ``ClaimForwardPeer`` route is configured, and the peer's
        verdict is relayed to the claimant; without a route, or when the owner is unreachable,
        ungoverned, or contested, the claim is refused fail-closed with the owning hub named so
        the caller can route it itself. With no map
        configured the hub owns every namespace it is asked about, preserving single-hub behaviour.

        Returns
        -------
        bool
            ``True`` when the claim may be routed to the local grant path; ``False`` when it was
            handled here — forwarded and its verdict relayed, or refused (a
            :data:`~synapse_channel.core.protocol.MessageType.CLAIM_DENIED` was sent).
        """
        if self._namespace_ownership is None or msg_type != MessageType.CLAIM:
            return True
        namespace = project_of(sender)
        decision = self._namespace_ownership.resolve(
            namespace, asserting_hubs=self.observed_asserting_hubs(namespace)
        )
        if decision.grants_locally:
            return True
        task_id = str(data.get("task_id") or data.get("payload") or "").strip()
        if decision.outcome is OwnershipOutcome.REMOTE and await self.forward_remote_claim(
            sender, namespace, task_id, data, decision.owner_hub_id or "", websocket
        ):
            return False
        logger.warning(
            "Claim refused for %s: namespace %r is %s (owner %s)",
            sender,
            namespace,
            decision.outcome.value,
            decision.owner_hub_id,
        )
        await self._send_json(
            websocket,
            self._system(
                f"claim refused: this hub does not own namespace {namespace!r} "
                f"({decision.outcome.value})",
                msg_type=MessageType.CLAIM_DENIED,
                target=sender,
                task_id=task_id,
                namespace=namespace,
                ownership=decision.outcome.value,
                owner_hub_id=decision.owner_hub_id,
            ),
        )
        return False

    def observed_asserting_hubs(self, namespace: str) -> tuple[str, ...]:
        """Return the hub ids observed asserting authority over ``namespace``, or empty.

        Reads the optional runtime feed configured on the hub; with none configured the
        ownership resolution sees no assertions and decides from its static map alone.
        """
        if self._observed_asserting_hubs_feed is None:
            return ()
        return tuple(self._observed_asserting_hubs_feed(namespace))

    async def forward_remote_claim(
        self,
        sender: str,
        namespace: str,
        task_id: str,
        data: dict[str, Any],
        owner_hub_id: str,
        websocket: Any,
    ) -> bool:
        """Forward a remote-owned claim to its owning hub and relay the verdict to the claimant.

        The owning hub applies the claim authoritatively and answers with a grant or a denial,
        which is relayed privately to the claimant — a grant carries the authentic lease fields,
        so the client sees the same ``CLAIM_GRANTED`` it would for a local claim.

        Returns
        -------
        bool
            ``True`` when the claim was forwarded and a verdict relayed, so the local grant path
            must not also run. ``False`` when no route is configured for the owner, the task
            carries no id to forward, or the forward failed — leaving the caller to refuse the
            claim and name the owner, fail-closed.
        """
        peer = self._claim_peers.get(owner_hub_id) if self._claim_peers else None
        if peer is None or not task_id:
            return False
        request = ClaimForwardRequest(
            namespace=namespace, claimant=sender, task_id=task_id, claim=data
        )
        self._counters.forwarded_claims += 1
        try:
            result = await self._claim_forwarder(
                request, uri=peer.uri, local_id=self._hub_id, token=peer.token
            )
        except ClaimForwardTimeoutError:
            self._counters.forwarded_claim_timeouts += 1
            logger.warning(
                "Forwarding claim %r for %s to owner %s timed out",
                task_id,
                sender,
                owner_hub_id,
            )
            await self._send_json(
                websocket,
                self._system(
                    f"claim refused: owning hub {owner_hub_id!r} did not answer "
                    f"forwarded claim {task_id!r} before timeout",
                    msg_type=MessageType.CLAIM_DENIED,
                    target=sender,
                    task_id=task_id,
                    namespace=namespace,
                    owner_hub_id=owner_hub_id,
                    ownership="remote",
                    forward_error="timeout",
                ),
            )
            return True
        except ClaimForwardError:
            logger.warning(
                "Forwarding claim %r for %s to owner %s failed", task_id, sender, owner_hub_id
            )
            return False
        if result.granted and result.grant is not None:
            self._counters.forwarded_claims_granted += 1
            await self._send_json(
                websocket,
                self._system(
                    result.detail or f"claim granted by {owner_hub_id}",
                    msg_type=MessageType.CLAIM_GRANTED,
                    target=sender,
                    **result.grant,
                ),
            )
        else:
            self._counters.forwarded_claims_denied += 1
            await self._send_json(
                websocket,
                self._system(
                    result.detail or "claim refused by the owning hub",
                    msg_type=MessageType.CLAIM_DENIED,
                    target=sender,
                    task_id=task_id,
                    namespace=namespace,
                    owner_hub_id=owner_hub_id,
                ),
            )
        return True
