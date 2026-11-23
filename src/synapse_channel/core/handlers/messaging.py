# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — base channel messaging handlers (chat relay and acknowledgement)
"""Base messaging handlers: the chat relay and acknowledgement.

Chat is the channel's broadcast primitive: the hub stamps the message with a
sequence id and hub id, retains it in bounded history, journals it when a durable
log is attached, and fans it out to every socket. Acknowledgements settle
deferred delivery receipts for reconnected directed recipients. This module also
composes the messaging verb family by unpacking :data:`heartbeat.VERB_SPECS` into
:data:`VERB_SPECS`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from synapse_channel.core.chat_dedupe import chat_digest
from synapse_channel.core.directed_delivery_liveness import (
    DeliveryLiveness,
    classify_completed_delivery,
    classify_delivery_liveness,
)
from synapse_channel.core.handlers.chat_delivery import (
    ChatDeliveryRequest,
    deliver_local_chat,
)
from synapse_channel.core.handlers.dead_letter_escalation import escalate_dead_letter
from synapse_channel.core.handlers.delivery_feedback import (
    send_delivery_receipt,
    settle_delivery_receipt,
)
from synapse_channel.core.handlers.heartbeat import VERB_SPECS as HEARTBEAT_VERB_SPECS
from synapse_channel.core.handlers.mailbox_replay import mailbox_recipient
from synapse_channel.core.hub_address import HUB_ADDRESS_SEPARATOR
from synapse_channel.core.journal import EventKind, record_chat
from synapse_channel.core.message_forward_origin import forward_chat
from synapse_channel.core.message_response import validate_semantic_response
from synapse_channel.core.numeric_coercion import safe_float
from synapse_channel.core.protocol import MessageType, is_recipient
from synapse_channel.core.verb_access import message_access
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.acl import AclPolicy
    from synapse_channel.core.channels import ChannelRegistry
    from synapse_channel.core.chat_dedupe import ChatDedupe
    from synapse_channel.core.dead_letter_forwarding import DeadLetterForwarder
    from synapse_channel.core.dead_letters import DeadLetterLedger
    from synapse_channel.core.durable_ingress import DurableIngressQuota
    from synapse_channel.core.handlers.delivery_feedback import DeliveryFeedbackContext
    from synapse_channel.core.mailbox_pending import MailboxPendingTracker
    from synapse_channel.core.message_forward_origin import ForwardOriginContext
    from synapse_channel.core.namespace_ownership import NamespaceOwnership
    from synapse_channel.core.operator_relay_transport import OperatorRelayPeer

    class MessagingContext(DeliveryFeedbackContext, ForwardOriginContext, Protocol):
        """Capabilities consumed by messaging handlers and their callees."""

        @property
        def acl_policy(self) -> AclPolicy | None:
            """Return the acl policy used by this handler family."""
            ...

        async def broadcast_directed(
            self, data: dict[str, Any], *, names: Iterable[str], sender_socket: Any
        ) -> frozenset[str]:
            """Return successful writes to recipients and granted observers only."""
            ...

        @property
        def channels(self) -> ChannelRegistry:
            """Return the channels used by this handler family."""
            ...

        @property
        def chat_dedupe(self) -> ChatDedupe:
            """Return the chat dedupe used by this handler family."""
            ...

        @property
        def dead_letter_escalation_threshold(self) -> int:
            """Return the dead letter escalation threshold used by this handler family."""
            ...

        @property
        def dead_letter_forwarder(self) -> DeadLetterForwarder | None:
            """Return the dead letter forwarder used by this handler family."""
            ...

        @property
        def dead_letters(self) -> DeadLetterLedger:
            """Return the dead letters used by this handler family."""
            ...

        @property
        def durable_ingress_quota(self) -> DurableIngressQuota | None:
            """Return the durable ingress quota used by this handler family."""
            ...

        @property
        def mailbox_pending(self) -> MailboxPendingTracker:
            """Return the mailbox pending used by this handler family."""
            ...

        async def mirror_to_relay(self, data: dict[str, Any]) -> None:
            """Mirror one broadcast to the lite relay log via :class:`RelayMirror`.

            Handler surface: the chat handler mirrors a channel-scoped message it
            fans out itself; the append, lite encoding, and bounded trimming live in
            :class:`~synapse_channel.core.hub_relay.RelayMirror`.
            """
            ...

        @property
        def namespace_ownership(self) -> NamespaceOwnership | None:
            """Return the namespace ownership used by this handler family."""
            ...

        def next_msg_id(self) -> int:
            """Return a strictly increasing per-hub message sequence number."""
            ...

        @property
        def observed_asserting_hubs(self) -> Callable[[str], Iterable[str]] | None:
            """Return the observed asserting hubs used by this handler family."""
            ...

        def observing_identities(self, target: str) -> tuple[str, ...]:
            """Return connected identities the ACL policy grants ``observe`` on ``target``.

            Under directed-message routing an observer (a live monitor or auditor) still
            receives a directed message it is not a party to only when it holds an
            ``observe`` grant. With no ACL policy configured there are no observers, so
            directed routing narrows to the recipients alone; the grant is scoped to the
            observer's own namespace, so an operator designates observers without opening
            the traffic to everyone.
            """
            ...

        def online_agents(self) -> list[str]:
            """Return the sorted names of currently registered agents."""
            ...

        def permitted_role_claims(self, name: str, roles: tuple[str, ...]) -> tuple[str, ...]:
            """Return the subset of declared ``roles`` ``name`` is permitted to bind.

            With role-claim enforcement off — the default open/loopback posture — every
            declared role is permitted, so a single-user dev hub binds roles exactly as
            before. With ``--require-role-claim`` on, a role is kept when either:

            - the role-grant store (``synapse role`` / ``--role-grants``) authorises
            ``name`` for it, or
            - the loaded ACL policy grants ``role-claim`` on target kind ``role`` for
            that role value (namespace-scoped like every other ACL rule).

            An unauthorised role is dropped and logged as a squatting attempt rather
            than dropping the socket. Enforcement with no store and no matching ACL
            rule denies the claim (fail closed). The gate keys off the self-reported
            ``name``, so pair it with a connect token and identity binding to be a real
            boundary.
            """
            ...

        def recipients_without_live_waiter(self, recipients: Iterable[str]) -> tuple[str, ...]:
            """Present recipients with no proof of liveness — the ones to warn about.

            Thin wrapper over
            :meth:`~synapse_channel.core.hub_liveness.HubLivenessView.recipients_without_live_waiter`,
            kept because the chat handler and tests call ``hub.recipients_without_live_waiter``.
            """
            ...

        @property
        def relay_peers(self) -> dict[str, OperatorRelayPeer] | None:
            """Return the relay peers used by this handler family."""
            ...

        def roles_of(self, name: str) -> tuple[str, ...]:
            """Return the roles ``name`` currently answers to (empty tuple if none)."""
            ...

        def set_agent_roles(self, name: str, roles: tuple[str, ...]) -> None:
            """Bind the roles an agent answers to, as declared on its registration heartbeat."""
            ...

        def set_wake_capability(self, name: str, capability: str) -> None:
            """Bind the receiver wake capability declared on an identity's registration."""
            ...

        @property
        def warn_stale_recipients(self) -> bool:
            """Return the warn stale recipients used by this handler family."""
            ...


MAX_CLIENT_MSG_ID_BYTES = 256
"""Maximum UTF-8 size of the optional receiver-side chat dedupe identity."""


def _normalize_client_msg_id(data: dict[str, Any]) -> str:
    """Retain one bounded printable sender-chosen chat identity, else omit it.

    A valid identity makes a retry safe: once a copy reached a live recipient, the hub
    answers a repeated ``(sender, client_msg_id)`` with a duplicate notice instead of
    routing it again (:mod:`synapse_channel.core.chat_dedupe`), and every live, retained,
    journalled, replayed, and receipted copy still echoes the identity so receivers
    can deduplicate what arrives by other paths. Invalid or oversized identifiers
    are removed rather than turning a chat into a connection error; such a chat
    follows ordinary at-least-once semantics.
    """
    raw = data.get("client_msg_id")
    if not isinstance(raw, str):
        data.pop("client_msg_id", None)
        return ""
    value = raw.strip()
    if (
        not value
        or len(value.encode("utf-8")) > MAX_CLIENT_MSG_ID_BYTES
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        data.pop("client_msg_id", None)
        return ""
    data["client_msg_id"] = value
    return value


def _client_timestamp(raw: Any) -> float | None:
    """Return a finite client send time when usable, else ``None``.

    A chat frame may carry an optional client-stamped instant. Only a finite
    numeric value is retained, and only as *advisory* metadata
    (``client_timestamp``). Missing, falsy, boolean, non-numeric, non-finite, or
    double-overflowing values are discarded rather than raised on (a bare
    ``float`` would otherwise drop the sender's connection out of the frame
    handler). The hub never uses this value to order history or dead letters.
    """
    if not raw or isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    # ``safe_float`` maps non-finite and overflowing inputs to ``default``.
    candidate = safe_float(raw, default=float("nan"), allow_bool=False)
    if candidate != candidate:  # NaN sentinel from safe_float failure / non-finite
        return None
    return candidate


def _stamp_chat_times(data: dict[str, Any], *, now: float | None = None) -> float:
    """Stamp authoritative hub time; retain finite client time only as advisory.

    Parameters
    ----------
    data : dict[str, Any]
        Inbound chat frame (mutated in place).
    now : float or None, optional
        Hub wall-clock override for tests; ``None`` uses :func:`time.time`.

    Returns
    -------
    float
        The hub-authoritative ``timestamp`` written onto the frame (also used
        for dead-letter ordering and history retention).
    """
    hub_now = time.time() if now is None else float(now)
    # Client-supplied advisory field is never trusted as-is; re-derive only from
    # a finite value presented on the envelope ``timestamp`` before overwrite.
    data.pop("client_timestamp", None)
    advisory = _client_timestamp(data.get("timestamp"))
    data["timestamp"] = hub_now
    if advisory is not None:
        data["client_timestamp"] = advisory
    return hub_now


@dataclass(frozen=True)
class ChatRouting:
    """What routing one chat through the local hub decided.

    Attributes
    ----------
    refusal : str
        Human-readable reason the chat was refused before it was accepted; empty when it
        was accepted.
    delivery : DeliveryLiveness or None
        The live-recipient verdict for an accepted chat; ``None`` for a refusal, a channel
        chat, or a chat forwarded to a peer hub.
    directed : bool
        Whether the accepted chat named one recipient rather than an audience.
    """

    refusal: str = ""
    delivery: DeliveryLiveness | None = None
    directed: bool = False

    @property
    def verdict(self) -> DeliveryLiveness:
        """Return the live-recipient verdict of a chat routed to local seats.

        Returns
        -------
        DeliveryLiveness
            The verdict.

        Raises
        ------
        ValueError
            For a refusal, a channel chat or a forwarded chat, which have no local verdict.
        """
        if self.delivery is None:
            raise ValueError(self.refusal or "chat was not routed to local seats")
        return self.delivery


async def handle_chat(
    hub: MessagingContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Route one agent chat; see :func:`route_chat` for the full contract."""
    await route_chat(hub, sender, data, websocket)


async def _refuse_chat(
    hub: MessagingContext, sender: str, websocket: Any, refusal: str, *, report: bool
) -> ChatRouting:
    """Refuse a chat, telling the sender on ``websocket`` when ``report`` is set."""
    if report:
        await hub.send_json(
            websocket, hub.system(refusal, msg_type=MessageType.ERROR, target=sender)
        )
    return ChatRouting(refusal=refusal)


async def _retry_chat(
    hub: MessagingContext,
    sender: str,
    websocket: Any,
    *,
    client_msg_id: str,
    digest: str,
    accepted_at: float,
    report_refusal: bool,
) -> ChatRouting | None:
    """Answer a conflicting or already delivered retry before charging ingress."""
    retry = hub.chat_dedupe.check(sender, client_msg_id, digest, now=accepted_at)
    if retry.outcome == "conflict":
        hub.counters.chat_client_id_conflicts += 1
        return await _refuse_chat(
            hub,
            sender,
            websocket,
            f"client_msg_id {client_msg_id!r} was already used for a different message.",
            report=report_refusal,
        )
    if retry.original is not None:
        # Duplicate chat: the first copy was accepted; tell the sender instead of routing it
        # again. A system notice, since clients drop chat frames bearing their name.
        hub.counters.chat_duplicates_suppressed += 1
        await hub.send_json(
            websocket,
            hub.system(
                f"Chat {client_msg_id!r} was already accepted as message "
                f"{retry.original.get('msg_id')}; it was not routed again.",
                target=sender,
                duplicate=True,
                client_msg_id=client_msg_id,
                **retry.original,
            ),
        )
        return ChatRouting()

    return None


async def _charge_chat(
    hub: MessagingContext,
    sender: str,
    data: dict[str, Any],
    websocket: Any,
    *,
    report_refusal: bool,
) -> ChatRouting | None:
    """Charge the server-derived durable principal before retaining or forwarding."""
    from synapse_channel.core.durable_ingress import chat_frame_bytes

    if hub.durable_ingress_quota is not None:
        principal = hub.clients.quota_principal(websocket, fallback_agent=sender)
        reason = hub.durable_ingress_quota.allow(
            principal,
            nbytes=chat_frame_bytes(data),
        )
        if reason:
            hub.counters.durable_ingress_refused += 1
            return await _refuse_chat(
                hub,
                sender,
                websocket,
                f"Durable ingress quota exceeded ({reason}).",
                report=report_refusal,
            )

    return None


async def route_chat(
    hub: MessagingContext,
    sender: str,
    data: dict[str, Any],
    websocket: Any,
    *,
    report_refusal: bool = True,
    dedupe_retries: bool = True,
) -> ChatRouting:
    """Stamp, retain, journal, and broadcast a chat message to every socket.

    A message carrying a ``channel`` is audience-scoped instead: it is delivered
    only to that channel's online members and is never broadcast or kept in the
    public chat history. It is still mirrored to the relay log and journalled,
    tagged with its channel so relay and event-query can filter it — see
    :func:`_route_channel_chat`.

    ``timestamp`` is always the hub's wall clock (authoritative for history and
    dead-letter ordering). A finite client-supplied instant is preserved only as
    ``client_timestamp`` advisory metadata and cannot poison those order keys.

    When the hub has a :class:`~synapse_channel.core.durable_ingress.DurableIngressQuota`,
    the chat is charged to the connection's server-derived quota principal and
    refused before history or journal growth when the sliding window is full.

    A target of the form ``PROJECT/seat@HUB_ID`` is handed to
    :func:`~synapse_channel.core.message_forward_origin.forward_chat` after the quota
    check, so it is forwarded to that peer hub instead of routed locally.

    Parameters
    ----------
    hub : MessagingContext
        The hub routing the chat.
    sender : str
        The authenticated sender, or ``seat@origin_hub`` for a chat forwarded by a peer.
    data : dict[str, Any]
        The chat frame; stamped in place.
    websocket : Any
        The connection the chat arrived on; refusals and receipts are sent there.
    report_refusal : bool, optional
        Send a refusal to ``websocket`` as an ``error`` frame (the agent path). A peer
        forward passes ``False`` and relays :attr:`ChatRouting.refusal` in its own result.
    dedupe_retries : bool, optional
        Answer a retried chat (same sender and ``client_msg_id``) from
        :class:`~synapse_channel.core.chat_dedupe.ChatDedupe` instead of routing it again
        (the agent path). A peer forward passes ``False``: its ``forward_id`` ledger
        already answers a repeated forward.

    Returns
    -------
    ChatRouting
        The refusal, or the delivery verdict of the accepted chat.
    """
    response_error = validate_semantic_response(
        hub.journal,
        data,
        sender,
        hub.roles_of(sender),
    )
    if response_error is not None:
        return await _refuse_chat(hub, sender, websocket, response_error, report=report_refusal)

    _stamp_chat_times(data)
    client_msg_id = _normalize_client_msg_id(data)
    accepted_at = float(data["timestamp"])
    remember = bool(client_msg_id) and dedupe_retries
    digest = chat_digest(data) if remember else ""
    if remember:
        retry = await _retry_chat(
            hub,
            sender,
            websocket,
            client_msg_id=client_msg_id,
            digest=digest,
            accepted_at=accepted_at,
            report_refusal=report_refusal,
        )
        if retry is not None:
            return retry
    refusal = await _charge_chat(hub, sender, data, websocket, report_refusal=report_refusal)
    if refusal is not None:
        return refusal
    data["type"] = MessageType.CHAT
    data["hub_id"] = hub.hub_id
    data["msg_id"] = hub.next_msg_id()
    channel = str(data.get("channel") or "").strip()
    if channel:
        reached = await _route_channel_chat(hub, sender, data, websocket, channel)
        if reached and remember:
            hub.chat_dedupe.remember(sender, client_msg_id, digest, data, accepted_at=accepted_at)
        return ChatRouting()
    if HUB_ADDRESS_SEPARATOR in str(data.get("target") or ""):
        accepted = await forward_chat(hub, sender, data, websocket)
        if accepted and remember:
            hub.chat_dedupe.remember(sender, client_msg_id, digest, data, accepted_at=accepted_at)
        return ChatRouting()
    delivery, directed = await deliver_local_chat(
        ChatDeliveryRequest(
            hub=hub,
            sender=sender,
            data=data,
            websocket=websocket,
            client_msg_id=client_msg_id,
            digest=digest,
            remember=remember,
            accepted_at=accepted_at,
            escalate=partial(escalate_dead_letter, hub),
        )
    )
    return ChatRouting(delivery=delivery, directed=directed)


async def _route_channel_chat(
    hub: MessagingContext, sender: str, data: dict[str, Any], websocket: Any, channel: str
) -> bool:
    """Deliver a channel-scoped chat to online members only, never broadcast.

    Non-members are refused privately. The body is not retained in the public
    chat history, but it is retained in the channel's bounded live history and
    mirrored/journalled with explicit channel metadata so relay and event-query
    can filter it. Writes run concurrently with finite transport deadlines. Returns
    whether a write to at least one online member completed; receipts and retry
    deduplication use this same verdict.
    """
    if not hub.channels.is_member(channel, sender):
        await hub.send_json(
            websocket,
            hub.system(
                f"not a member of channel '{channel}'",
                msg_type=MessageType.ERROR,
                target=sender,
                channel=channel,
            ),
        )
        return False
    online = set(hub.online_agents())
    recipients = sorted(
        member for member in hub.channels.members(channel) if member != sender and member in online
    )
    hub.channels.retain_message(channel, data, max_messages=hub.max_history)
    if hub.journal is not None:
        record_chat(hub.journal, data)
    await hub.mirror_to_relay(data)
    completed = await asyncio.gather(*(hub.send_to_agent(member, data) for member in recipients))
    successful = [member for member, sent in zip(recipients, completed, strict=True) if sent]
    delivery = classify_completed_delivery(
        classify_delivery_liveness(recipients, ()), successful, include_waiters=False
    )
    if bool(data.get("receipt_requested")):
        await send_delivery_receipt(
            hub,
            websocket,
            sender=sender,
            target=channel,
            msg_id=int(data["msg_id"]),
            decision=delivery,
            client_msg_id=str(data.get("client_msg_id") or ""),
        )
    return delivery.delivered


async def handle_ack(
    hub: MessagingContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Settle a pending directed message with a deferred delivery receipt to its sender.

    A recipient that drained a receipt-requested directed message from its reconnect
    backlog acks it by its durable journal ``seq``. If that ``seq`` is still awaiting a
    receipt and ``sender`` is a genuine recipient of the target it was addressed to, the
    hub finally tells the original sender ``delivered: true, deferred: true`` — the
    revision of the immediate ``delivered: false`` the sender saw when nobody was live —
    and forgets the entry.

    The target is re-checked with :func:`is_recipient` before the entry is claimed, so a
    spoofed ack from a client the message was never addressed to neither fabricates a
    receipt nor destroys the pending one a genuine recipient will settle. A malformed or
    non-integer ``seq``, an unknown ``seq`` (never pending, or already settled), and an ack
    from a non-recipient are all silent no-ops — an ack is a best-effort confirmation, never
    a frame whose rejection should drop the acking socket.
    """
    raw_seq = data.get("seq")
    if isinstance(raw_seq, bool) or not isinstance(raw_seq, int):
        return
    recipient = mailbox_recipient(sender, data.get("mailbox_for"), hub=hub)
    roles = hub.roles_of(sender)
    hub.mailbox_pending.acknowledge(recipient, raw_seq, roles=roles)
    entry = hub.pending_receipts.peek(raw_seq)
    if entry is None:
        return
    if not is_recipient(entry.target, recipient, roles=roles):
        return
    await settle_delivery_receipt(
        hub,
        message_seq=raw_seq,
        entry=entry,
        recipient=recipient,
        acknowledged_by=sender,
    )


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.CHAT,),
        handler=handle_chat,
        reply_types=(
            MessageType.CHAT,
            MessageType.DELIVERY_RECEIPT,
            MessageType.DEAD_LETTER_ESCALATION,
            MessageType.DEAD_LETTER_FORWARDING,
        ),
        mutates=True,
        replay_protected=False,
        mutation_guarded=True,
        accesses=message_access,
        event_kinds=(
            EventKind.CHAT,
            EventKind.DEAD_LETTER_ESCALATION,
            EventKind.DEAD_LETTER_FORWARDING,
            EventKind.DELIVERY_RECEIPT_REQUESTED,
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            EventKind.DELIVERY_RECEIPT_DEFERRED,
            EventKind.DELIVERY_RECEIPT_EXPIRED,
        ),
        minimum_wire_version=1,
        commands=("send",),
    ),
    VerbSpec(
        request_types=(MessageType.ACK,),
        handler=handle_ack,
        reply_types=(MessageType.DELIVERY_RECEIPT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=False,
        accesses=None,
        event_kinds=(
            EventKind.MAILBOX_WATERMARK,
            EventKind.DELIVERY_RECEIPT_DEFERRED,
        ),
        minimum_wire_version=2,
        commands=(),
    ),
    *HEARTBEAT_VERB_SPECS,
)
