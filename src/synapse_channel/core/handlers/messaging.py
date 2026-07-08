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

from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.dead_letter_escalation import (
    crosses_escalation_threshold,
    escalation_notice,
)
from synapse_channel.core.dead_letter_forwarding import DeadLetterForwardError, forwarding_notice
from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.journal import (
    DEAD_LETTER_DIRECTION_OUT,
    EventKind,
    record_chat,
    record_dead_letter_escalation,
    record_dead_letter_forwarding,
)
from synapse_channel.core.numeric_coercion import safe_float, safe_int
from synapse_channel.core.operator_relay_routing import RelayRouteKind, route_operator_relay
from synapse_channel.core.protocol import MessageType, is_recipient
from synapse_channel.core.wake_capability import (
    WAKE_PASSIVE,
    WAKE_UNKNOWN,
    normalize_wake_capability,
    wake_capability_label,
)

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
    only to that channel's online members and never broadcast, retained in the
    public history, or mirrored to the relay log.
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
    if is_directed_target(target):
        hub.counters.chat_directed += 1
    else:
        hub.counters.chat_broadcast += 1
    escalation: tuple[str, int, str] | None = None
    if not recipients and is_directed_target(target):
        # durable but waking nobody - remember the blackhole so the state
        # snapshot can show it instead of a human discovering it by relaying
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
    if hub.private_directed_messages and is_directed_target(target):
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
    if hub.warn_stale_recipients and is_directed_target(target) and recipients:
        # The message was delivered to present recipients, but present is not the
        # same as reachable-in-practice: warn the sender about any recipient that is
        # online yet has no proof it is wake-capable, so a reply that never comes is
        # not silently waited on. Off by default, so the open hub never sends this.
        await _warn_stale_recipients(
            hub,
            websocket,
            sender=sender,
            target=target,
            msg_id=int(data["msg_id"]),
            recipients=recipients,
        )
    if bool(data.get("receipt_requested")):
        await _send_delivery_receipt(
            hub,
            websocket,
            sender=sender,
            target=target,
            msg_id=int(data["msg_id"]),
            recipients=recipients,
        )
        if not recipients and is_directed_target(target) and "seq" in data:
            # Nobody live matched, so the immediate receipt said "not delivered" — but the
            # journal kept the body, so remember it under its durable seq: when the recipient
            # reconnects, drains the backlog, and acks that seq, we can revise the verdict to
            # a deferred "delivered". Only a journalled directed message can be acked, because
            # only it carries the seq the ack echoes and the durable copy the recipient reads.
            hub.pending_receipts.remember(int(data["seq"]), sender=sender, target=target)


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
        await _send_delivery_receipt(
            hub,
            websocket,
            sender=sender,
            target=channel,
            msg_id=int(data["msg_id"]),
            recipients=recipients,
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


def _recipient_wake_capability(hub: SynapseHub, recipient: str) -> str:
    """Return the best declared wake capability for a logical recipient."""
    direct = hub.wake_capability_of(recipient)
    if direct != WAKE_UNKNOWN:
        return direct
    return hub.wake_capability_of(f"{recipient}{_WAITER_SUFFIX}")


def _recipient_wake_capabilities(hub: SynapseHub, recipients: Iterable[str]) -> dict[str, str]:
    """Return normalized wake capabilities keyed by logical recipient name."""
    return {recipient: _recipient_wake_capability(hub, recipient) for recipient in recipients}


def _render_recipient_with_capability(recipient: str, capability: str) -> str:
    """Render one receipt recipient with its declared wake-capability label."""
    if capability == WAKE_UNKNOWN:
        return recipient
    return f"{recipient} ({wake_capability_label(capability)})"


async def _warn_stale_recipients(
    hub: SynapseHub,
    websocket: Any,
    *,
    sender: str,
    target: str,
    msg_id: int,
    recipients: list[str],
) -> None:
    """Privately warn ``sender`` about directed recipients that are present but deaf.

    A recipient is flagged when it is online yet has no independent proof of
    liveness — no armed ``-rx`` waiter sidecar and no genuine reaction within the
    liveness window (see
    :meth:`~synapse_channel.core.hub.SynapseHub.recipients_without_live_waiter`). The
    warning names those recipients and is delivered only to the sender's own socket,
    the way a delivery receipt is; it is advisory (the message was still delivered
    and journalled) so it is not itself journalled. When every recipient has a proof
    of liveness the sender is told nothing, so the signal stays rare enough to mean
    something.
    """
    capabilities = _recipient_wake_capabilities(hub, recipients)
    stale = hub.recipients_without_live_waiter(recipients)
    passive = tuple(
        recipient for recipient, capability in capabilities.items() if capability == WAKE_PASSIVE
    )
    if not stale and not passive:
        return
    clauses: list[str] = []
    if stale:
        clauses.append(
            f"{', '.join(stale)} present but not proven live — no armed waiter and no "
            "recent reaction"
        )
    if passive:
        clauses.append(
            f"{', '.join(passive)} reached only a passive receiver — socket delivery does "
            "not prove an agent pane was woken"
        )
    await hub._send_json(
        websocket,
        hub._system(
            "; ".join(clauses) + "; a directed message may sit unread",
            msg_type=MessageType.RECIPIENT_LIVENESS_WARNING,
            target=sender,
            message_target=target,
            message_id=msg_id,
            stale_recipients=list(stale),
            passive_recipients=list(passive),
            recipient_wake_capabilities=capabilities,
        ),
    )


async def _send_delivery_receipt(
    hub: SynapseHub,
    websocket: Any,
    *,
    sender: str,
    target: str,
    msg_id: int,
    recipients: list[str],
) -> None:
    """Send a private delivery receipt for a receipt-requested chat."""
    delivered = bool(recipients)
    capabilities = _recipient_wake_capabilities(hub, recipients)
    if delivered:
        rendered = (
            _render_recipient_with_capability(recipient, capabilities[recipient])
            for recipient in recipients
        )
        payload = f"delivered to {', '.join(rendered)}"
    else:
        payload = f"delivery failed: no online recipient matched {target}"
    await hub._send_json(
        websocket,
        hub._system(
            payload,
            msg_type=MessageType.DELIVERY_RECEIPT,
            target=sender,
            message_target=target,
            message_id=msg_id,
            delivered=delivered,
            recipients=recipients,
            recipient_wake_capabilities=capabilities,
        ),
    )


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
    entry = hub.pending_receipts.peek(raw_seq)
    if entry is None:
        return
    if not is_recipient(entry.target, sender, roles=hub.roles_of(sender)):
        return
    hub.pending_receipts.claim(raw_seq)
    await hub._send_to_agent(
        entry.sender,
        hub._system(
            f"delivered to {sender} on reconnect",
            msg_type=MessageType.DELIVERY_RECEIPT,
            target=entry.sender,
            message_target=entry.target,
            message_seq=raw_seq,
            delivered=True,
            deferred=True,
            recipients=[sender],
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


def _mailbox_recipient(connection: str, declared: Any) -> str:
    """Resolve whose directed backlog a mailbox heartbeat may replay onto ``connection``.

    A mailbox client may name, in ``declared`` (the heartbeat's ``mailbox_for``), an
    identity other than its connection name — a wake-listener connects under a
    receive-only ``<identity>-rx`` name while waiting on the bare ``<identity>``. But
    replaying an *arbitrary* named identity's directed backlog on an unauthenticated
    assertion would let any socket pull another identity's missed directed messages
    from the journal. So a declared identity is honoured only when it is the connection
    itself or the identity this connection is the ``-rx`` sidecar of; any other value
    (including a blank or non-string one) falls back to the connection's own backlog
    rather than dropping the socket.

    This enforces the documented ``-rx`` sidecar contract structurally; it is not by
    itself proof the socket is genuinely that sidecar — absent hub authentication a
    hostile socket can still connect under a ``<victim>-rx`` name. Binding the
    connection identity (per-message auth / an ACL grant) is the deeper control, tracked
    with the wider identity-authenticity work, not resolved by a naming convention.
    """
    if not isinstance(declared, str):
        return connection
    requested = declared.strip()
    if not requested or requested == connection:
        return connection
    if connection == f"{requested}{_WAITER_SUFFIX}":
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
    and the replay is filtered by it. It is honoured only when it names ``sender`` or
    the identity ``sender`` is the ``-rx`` sidecar of (see :func:`_mailbox_recipient`);
    a missing, blank, or unrelated value falls back to ``sender``, so an agent
    connecting under its own identity needs no such field and no socket can pull an
    arbitrary identity's directed backlog by naming it.
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
        recipient = _mailbox_recipient(sender, data.get("mailbox_for"))
        await _replay_directed_backlog(hub, sender, recipient, since_seq, websocket)
