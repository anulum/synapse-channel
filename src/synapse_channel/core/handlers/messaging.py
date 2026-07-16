# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — base channel messaging handlers (chat relay + heartbeat)
"""Base messaging handlers: the chat relay and the heartbeat keepalive.

Chat is the channel's broadcast primitive: the hub stamps the message with a
sequence id and hub id, retains it in bounded history, journals it when a durable
log is attached, and fans it out to every socket. The heartbeat carries no
payload — the liveness side effect has already been applied by the routing core
before dispatch — so its handler is a deliberate no-op kept in the registry for a
uniform dispatch table.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import (
    MAILBOX,
    WOULD_ALLOW,
    Target,
    evaluate_access,
)
from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.dead_letter_escalation import (
    crosses_escalation_threshold,
    escalation_notice,
)
from synapse_channel.core.dead_letter_forwarding import DeadLetterForwardError, forwarding_notice
from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.delivery_receipts import deferred_receipt_payload
from synapse_channel.core.directed_delivery_liveness import classify_delivery_liveness
from synapse_channel.core.handlers.delivery_feedback import (
    send_and_track_delivery_receipt,
    send_delivery_receipt,
    warn_stale_recipients,
)
from synapse_channel.core.journal import (
    DEAD_LETTER_DIRECTION_OUT,
    EventKind,
    record_chat,
    record_dead_letter_escalation,
    record_dead_letter_forwarding,
    record_delivery_receipt_deferred,
)
from synapse_channel.core.numeric_coercion import safe_float, safe_int
from synapse_channel.core.operator_relay_routing import RelayRouteKind, route_operator_relay
from synapse_channel.core.protocol import MessageType, is_recipient
from synapse_channel.core.wake_capability import normalize_wake_capability

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

logger = logging.getLogger("synapse.messaging")


def _client_timestamp(raw: Any, now: float) -> float:
    """Return the client's send time if it is a usable instant, else the hub clock.

    A chat's ``timestamp`` is advisory metadata the client may stamp. A missing,
    falsy, non-numeric, non-finite, or double-overflowing value falls back to the
    hub's authoritative ``now`` — a bare ``float`` would otherwise raise on a
    string, list, or huge integer (dropping the sender's connection out of the
    frame handler), or admit ``inf``/``nan`` into the retained history, the
    broadcast, and the dead-letter ledger's ordering key.
    """
    if not raw or isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return now
    return safe_float(raw, default=now, allow_bool=False)


async def handle_chat(hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any) -> None:
    """Stamp, retain, journal, and broadcast a chat message to every socket.

    A message carrying a ``channel`` is audience-scoped instead: it is delivered
    only to that channel's online members and is never broadcast or kept in the
    public chat history. It is still mirrored to the relay log and journalled,
    tagged with its channel so relay and event-query can filter it — see
    :func:`_route_channel_chat`.
    """
    data["timestamp"] = _client_timestamp(data.get("timestamp"), time.time())
    data["type"] = MessageType.CHAT
    data["hub_id"] = hub.hub_id
    data["msg_id"] = hub._next_msg_id()
    channel = str(data.get("channel") or "").strip()
    if channel:
        await _route_channel_chat(hub, sender, data, websocket, channel)
        return
    target = str(data.get("target") or "all")
    recipients = _matching_online_recipients(target, sender, hub.online_agents(), hub.roles_of)
    directed = is_directed_target(target)
    stale_recipients = hub.recipients_without_live_waiter(recipients) if directed else ()
    delivery = classify_delivery_liveness(recipients, stale_recipients)
    if directed:
        hub.counters.chat_directed += 1
    else:
        hub.counters.chat_broadcast += 1
    escalation: tuple[str, int, str] | None = None
    if not delivery.delivered and directed:
        # Durable but reaching nobody proven consume-live: remember the blackhole
        # even when a stale socket stayed open, so transport presence cannot hide it.
        count = hub.dead_letters.record(target, sender=sender, ts=float(data["timestamp"]))
        if crosses_escalation_threshold(count, hub.dead_letter_escalation_threshold):
            escalation = (target, count, sender)
    hub.chat_history.append(data.copy())
    if len(hub.chat_history) > hub.max_history:
        del hub.chat_history[0]
    if hub.journal is not None:
        # Stamp the durable journal seq on the outgoing frame so a client can track
        # it as the cursor it resumes a missed directed backlog from on reconnect.
        data["seq"] = record_chat(hub.journal, data)
        hub.mailbox_pending.observe_chat(int(data["seq"]), data)
    receipt_requested = bool(data.get("receipt_requested"))
    message_seq = int(data["seq"]) if "seq" in data else None
    receipt_before_fanout = (
        receipt_requested and directed and not delivery.delivered and bool(recipients)
    )
    if receipt_before_fanout:
        await send_and_track_delivery_receipt(
            hub,
            websocket,
            sender=sender,
            target=target,
            msg_id=int(data["msg_id"]),
            message_seq=message_seq,
            decision=delivery,
            directed=directed,
        )
    if hub.private_directed_messages and directed:
        # Recipient routing: a directed message reaches only its recipients (and their
        # -rx waiter sidecars) plus any granted observers — never every socket. It is
        # still mirrored to the relay and journalled above, so the durable feed keeps
        # full visibility for dashboards and the federation follower.
        audience = _directed_audience(recipients, hub.observing_identities(target))
        await hub._broadcast_directed(data, names=audience, sender_socket=websocket)
    else:
        await hub._broadcast(data)
    if escalation is not None:
        # After the chat is delivered: escalate the blackhole it added to, as a follow-up signal.
        await _escalate_dead_letter(
            hub, target=escalation[0], count=escalation[1], sender=escalation[2]
        )
    if hub.warn_stale_recipients and directed and recipients:
        await warn_stale_recipients(
            hub,
            websocket,
            sender=sender,
            target=target,
            msg_id=int(data["msg_id"]),
            decision=delivery,
        )
    if receipt_requested and not receipt_before_fanout:
        await send_and_track_delivery_receipt(
            hub,
            websocket,
            sender=sender,
            target=target,
            msg_id=int(data["msg_id"]),
            message_seq=message_seq,
            decision=delivery,
            directed=directed,
        )


async def _escalate_dead_letter(hub: SynapseHub, *, target: str, count: int, sender: str) -> None:
    """Escalate a dead-letter blackhole that has crossed its threshold.

    The escalation is an active signal, never a re-delivery (the ledger holds no message bodies):
    the hub broadcasts a one-line notice to every connected socket — so an operator sees a growing
    blackhole live — and journals an audit-only event when a durable log is attached, so the
    escalation is also reviewable after the fact. The named target is almost certainly not
    connected (that is why its messages are dead-lettering), so the notice reaches the operators and
    peers who can act, not the missing reader.
    """
    # Persist before notifying, so a reader that sees the broadcast can trust the audit is written.
    if hub.journal is not None:
        record_dead_letter_escalation(
            hub.journal,
            {
                "target": target,
                "count": count,
                "last_sender": sender,
                "threshold": hub.dead_letter_escalation_threshold,
            },
        )
    notice = escalation_notice(target, count, sender)
    await hub._broadcast(
        hub._system(
            notice,
            msg_type=MessageType.DEAD_LETTER_ESCALATION,
            escalation_target=target,
            escalation_count=count,
            last_sender=sender,
        )
    )
    await _forward_dead_letter_to_peer(hub, target=target, count=count)


async def _forward_dead_letter_to_peer(hub: SynapseHub, *, target: str, count: int) -> None:
    """Forward a blackhole signal to the peer hub whose domain owns the target, if any.

    The target's namespace is resolved through the same namespace-ownership and relay-route roster
    the operator relay uses. When it resolves to a peer this hub does not own, the origin records a
    durable, audit-only forwarding event (a pointer — the target, its undelivered count, and the
    origin and owner hub ids; never a message body) and, when a forwarder is configured, transmits
    that pointer to the owning hub best-effort. A local, unrouted, ungoverned, or partitioned
    namespace forwards nothing: the local escalation already covers a target this hub owns, and a
    signal is never sent to a hub the operator did not route to.
    """
    if hub.namespace_ownership is None or not hub.relay_peers:
        return
    namespace = project_of(target)
    if not namespace:
        return
    asserting = hub.observed_asserting_hubs(namespace) if hub.observed_asserting_hubs else ()
    decision = hub.namespace_ownership.resolve(namespace, asserting_hubs=asserting)
    route = route_operator_relay(decision, relay_peers=hub.relay_peers)
    if route.kind is not RelayRouteKind.FORWARD or route.peer is None:
        return
    notice = forwarding_notice(
        target, count, origin_hub_id=hub.hub_id, owner_hub_id=decision.owner_hub_id or ""
    )
    if hub.journal is not None:
        # The wire pointer stays the bare four-field notice; the audit adds the ``out`` direction
        # so this origin-side record reconciles with the owning hub's inbound record of the same
        # forward.
        record_dead_letter_forwarding(
            hub.journal, {**notice, "direction": DEAD_LETTER_DIRECTION_OUT}
        )
    if hub.dead_letter_forwarder is None:
        return
    try:
        await hub.dead_letter_forwarder(
            notice, uri=route.peer.uri, local_id=hub.hub_id, token=route.peer.token
        )
    except DeadLetterForwardError as exc:
        # Best-effort over the already-durable audit: a peer we could not reach degrades to
        # "recorded but not delivered", never a lost signal or a crashed escalation.
        logger.warning("Dead-letter forward to %s failed: %s", route.peer.uri, exc)


async def _route_channel_chat(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any, channel: str
) -> None:
    """Deliver a channel-scoped chat to online members only, never broadcast.

    Non-members are refused privately. The body is not retained in the public
    chat history, but it is retained in the channel's bounded live history and
    mirrored/journalled with explicit channel metadata so relay and event-query
    can filter it.
    """
    if not hub.channels.is_member(channel, sender):
        await hub._send_json(
            websocket,
            hub._system(
                f"not a member of channel '{channel}'",
                msg_type=MessageType.ERROR,
                target=sender,
                channel=channel,
            ),
        )
        return
    online = set(hub.online_agents())
    recipients = sorted(
        member for member in hub.channels.members(channel) if member != sender and member in online
    )
    hub.channels.retain_message(channel, data, max_messages=hub.max_history)
    if hub.journal is not None:
        record_chat(hub.journal, data)
    hub._mirror_to_relay(data)
    for member in recipients:
        await hub._send_to_agent(member, data)
    if bool(data.get("receipt_requested")):
        await send_delivery_receipt(
            hub,
            websocket,
            sender=sender,
            target=channel,
            msg_id=int(data["msg_id"]),
            decision=classify_delivery_liveness(recipients, ()),
        )


def _directed_audience(recipients: list[str], observers: Iterable[str]) -> list[str]:
    """Return the live delivery names for a directed message under recipient routing.

    Each recipient is reached on its own socket and on its ``-rx`` waiter sidecar (so a
    reconnecting waiter is still woken), and any granted observers are appended. The
    broadcaster deduplicates by socket, so an observer that is also a recipient, or a
    recipient with no live sidecar, is handled without special-casing here.
    """
    names: list[str] = []
    for name in recipients:
        names.append(name)
        names.append(f"{name}{_WAITER_SUFFIX}")
    names.extend(observers)
    return names


def _matching_online_recipients(
    target: str,
    sender: str,
    online_agents: list[str],
    roles_of: Callable[[str], tuple[str, ...]],
) -> list[str]:
    """Return online recipients reached by ``target``, excluding the sender socket.

    A recipient is reached when ``target`` addresses its name, its bare project, or
    one of the roles it holds (looked up through ``roles_of``) — so a directed message
    to a ``<project>/<role>`` resolves to whichever agents currently answer to it.
    A receive-only waiter socket (``<identity>-rx``) also proves the logical
    ``<identity>`` is reachable for wake delivery. The logical name is what senders
    address and what waiters filter on; the socket name is only transport plumbing.
    """
    recipients: set[str] = set()
    for name in online_agents:
        if name == sender:
            continue
        roles = roles_of(name)
        if name.endswith(_WAITER_SUFFIX):
            logical = name[: -len(_WAITER_SUFFIX)]
            logical_roles = tuple(dict.fromkeys((*roles, *roles_of(logical))))
            if logical != sender and is_recipient(target, logical, roles=logical_roles):
                recipients.add(logical)
                continue
        if is_recipient(target, name, roles=roles):
            recipients.add(name)
    return sorted(recipients)


async def handle_ack(hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any) -> None:
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
    recipient = _mailbox_recipient(sender, data.get("mailbox_for"), hub=hub)
    roles = hub.roles_of(sender)
    hub.mailbox_pending.acknowledge(recipient, raw_seq, roles=roles)
    entry = hub.pending_receipts.peek(raw_seq)
    if entry is None:
        return
    if not is_recipient(entry.target, recipient, roles=roles):
        return
    hub.pending_receipts.claim(raw_seq)
    if hub.journal is not None:
        record_delivery_receipt_deferred(
            hub.journal,
            deferred_receipt_payload(entry=entry, message_seq=raw_seq, recipient=recipient),
        )
    await hub._send_to_agent(
        entry.sender,
        hub._system(
            f"delivered to {recipient} on reconnect",
            msg_type=MessageType.DELIVERY_RECEIPT,
            target=entry.sender,
            message_target=entry.target,
            message_id=entry.message_id,
            message_seq=raw_seq,
            delivered=True,
            deferred=True,
            recipients=[recipient],
        ),
    )


MAILBOX_REPLAY_READ_CAP = 1000
"""Upper bound on journal chat events scanned for one reconnect backlog replay.

A briefly-offline recipient's missed directed messages sit within a bounded window
of recent chat, so scanning at most this many keeps a reconnect cheap and bounds a
mailbox heartbeat's cost. A gap so long that a recipient's messages fall beyond the
window degrades to the durable feed (``syn-inbox``), which has no such bound — the
backlog replay adds promptness, it does not replace the feed as the unbounded record.
"""


async def _replay_directed_backlog(
    hub: SynapseHub, name: str, recipient: str, since_seq: int, websocket: Any
) -> None:
    """Push the directed chats ``recipient`` missed while offline, from the durable journal.

    On a mailbox-capable reconnect the hub resumes from the client's ``since_seq``
    cursor: it reads journalled chat events after it, keeps only those directed at
    ``recipient`` (by name, project, glob, or a role it holds) that ``recipient`` did
    not send itself, and re-sends each to the reconnecting socket marked ``replayed``
    with its durable journal ``seq`` — so the client wakes on and dedups the backlog by
    ``seq`` exactly as it would live traffic. Dedup keys on ``seq``, not ``msg_id``,
    because the per-hub ``msg_id`` counter resets on restart while ``seq`` never repeats.

    ``recipient`` is the identity whose backlog is wanted; it is the connecting ``name``
    for an agent that connects under its own identity, but a wake-listener connects under
    a receive-only ``-rx`` name while waiting on its bare identity, so it declares that
    identity separately and the replay is filtered by it, not by the socket's name. Roles
    are still read from the connection (``name``), the socket the roles were bound to. A
    journal-less hub cannot replay and returns silently; a broadcast is never replayed
    (the feed already carries it and re-pushing it would re-storm every reconnect).
    """
    if hub.journal is None:
        return
    roles = hub.roles_of(name)
    events = hub.journal.read_since(
        since_seq, kinds=(EventKind.CHAT,), limit=MAILBOX_REPLAY_READ_CAP
    )
    for event in events:
        payload = event.payload
        target = str(payload.get("target") or "all")
        if str(payload.get("sender") or "") == recipient or not is_directed_target(target):
            continue
        if not is_recipient(target, recipient, roles):
            continue
        frame = dict(payload)
        frame.pop("receipt_requested", None)
        frame["replayed"] = True
        frame["seq"] = event.seq
        await hub._send_json(websocket, frame)


# The kernel cannot import the top-level ``waiter_identity`` module (the package boundary
# keeps ``core`` from reaching up into the feature layers), so the ``-rx`` wake-listener
# suffix is matched here directly. It mirrors ``waiter_identity.WAITER_SUFFIX``, the single
# definition the non-core layers share; the convention is a stable naming contract.
_WAITER_SUFFIX = "-rx"


def _mailbox_acl_allows(hub: SynapseHub, connection: str, requested: str) -> bool:
    """Return whether the ACL policy grants ``connection`` mailbox access to ``requested``.

    Consulted only when a policy is loaded. The grant is the policy-file finish of the
    mailbox conditions: self and ``-rx`` sidecars do not need it; a trusted monitor that
    is neither still may, via a ``mailbox`` rule on target kind ``agent``.
    """
    policy = hub.acl_policy
    if policy is None:
        return False
    decision = evaluate_access(
        subject=connection,
        project=project_of(connection),
        permission=MAILBOX,
        target=Target("agent", requested),
        policy=policy,
    )
    return decision.decision == WOULD_ALLOW


def _mailbox_recipient(connection: str, declared: Any, hub: SynapseHub | None = None) -> str:
    """Resolve whose directed backlog a mailbox heartbeat may replay onto ``connection``.

    A mailbox client may name, in ``declared`` (the heartbeat's ``mailbox_for``), an
    identity other than its connection name — a wake-listener connects under a
    receive-only ``<identity>-rx`` name while waiting on the bare ``<identity>``. But
    replaying an *arbitrary* named identity's directed backlog on an unauthenticated
    assertion would let any socket pull another identity's missed directed messages
    from the journal. A declared identity is honoured when:

    1. it is the connection itself, or
    2. the connection is the ``-rx`` sidecar of that identity, or
    3. the loaded ACL policy grants the connection the ``mailbox`` permission on
       that identity (target kind ``agent``).

    Any other value (including blank or non-string) falls back to the connection's own
    backlog rather than dropping the socket. Structural self/``-rx`` is not by itself
    proof the socket is genuine — pair with connect token and identity binding; the
    ACL grant is the policy-file path for a trusted non-sidecar monitor.
    """
    if not isinstance(declared, str):
        return connection
    requested = declared.strip()
    if not requested or requested == connection:
        return connection
    if connection == f"{requested}{_WAITER_SUFFIX}":
        return requested
    if hub is not None and _mailbox_acl_allows(hub, connection, requested):
        return requested
    return connection


async def handle_heartbeat(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Register any declared roles and replay a missed directed backlog on request.

    The registration heartbeat may carry a ``roles`` list of ``<project>/<role>``
    names this identity answers to. Binding them here lets a directed message to a
    role reach its holder and show in ``/who`` instead of being counted a dead letter.
    Only a list is honoured, and non-string or blank entries are dropped rather than
    rejected, so a malformed field degrades to no roles instead of dropping the socket.
    When role-claim enforcement is on (``--require-role-claim``), a declared role the
    role-grant store does not authorise for this identity is dropped the same forgiving
    way (see :meth:`~synapse_channel.core.hub.SynapseHub.permitted_role_claims`), so a
    socket cannot squat a role no operator granted it. A keepalive with no ``roles``
    field leaves an earlier binding untouched.

    A mailbox-capable client also sets ``mailbox: true`` and ``since_seq`` (the last
    durable chat ``seq`` it processed) on its *registration* heartbeat, and the hub
    replays the directed messages it missed while offline from the journal — turning
    the pull-based ``syn-inbox`` catch-up into an automatic push on reconnect. Only a
    literal ``True`` triggers a replay, and a missing or malformed ``since_seq``
    degrades to ``0`` (replay the whole retained window) rather than dropping the
    socket. A keepalive omits ``mailbox``, so it never re-storms the backlog.

    An optional ``mailbox_for`` string names the identity whose backlog is wanted when
    it differs from the connection ``sender`` — a wake-listener connects under a
    receive-only ``-rx`` name but waits on its bare identity, so it declares that here
    and the replay is filtered by it. It is honoured when it names ``sender``, the
    identity ``sender`` is the ``-rx`` sidecar of, or an ACL ``mailbox`` grant on that
    identity (see :func:`_mailbox_recipient`); a missing, blank, or unauthorised value
    falls back to ``sender``, so an agent connecting under its own identity needs no
    such field and no socket can pull an arbitrary identity's directed backlog by
    naming it without a grant.
    """
    raw_roles = data.get("roles")
    if isinstance(raw_roles, list):
        cleaned = (item.strip() for item in raw_roles if isinstance(item, str) and item.strip())
        declared = tuple(dict.fromkeys(cleaned))
        hub.set_agent_roles(sender, hub.permitted_role_claims(sender, declared))
    if "wake_capability" in data:
        hub.set_wake_capability(sender, normalize_wake_capability(data.get("wake_capability")))
    if data.get("mailbox") is True:
        since_seq = safe_int(data.get("since_seq"), default=0, min_value=0, allow_bool=False)
        recipient = _mailbox_recipient(sender, data.get("mailbox_for"), hub=hub)
        hub.mailbox_pending.advance(
            recipient,
            since_seq,
            roles=hub.roles_of(sender),
            source="cursor",
        )
        await _replay_directed_backlog(hub, sender, recipient, since_seq, websocket)
