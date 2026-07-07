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
import math
import time
from collections.abc import Callable
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
from synapse_channel.core.operator_relay_routing import RelayRouteKind, route_operator_relay
from synapse_channel.core.protocol import MessageType, is_recipient

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
    try:
        value = float(raw)
    except OverflowError:  # a JSON integer too large for a double
        return now
    return value if math.isfinite(value) else now


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
    await hub._broadcast(data)
    if escalation is not None:
        # After the chat is delivered: escalate the blackhole it added to, as a follow-up signal.
        await _escalate_dead_letter(
            hub, target=escalation[0], count=escalation[1], sender=escalation[2]
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
    """
    return sorted(
        name
        for name in online_agents
        if name != sender and is_recipient(target, name, roles=roles_of(name))
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
    if delivered:
        payload = f"delivered to {', '.join(recipients)}"
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
    hub: SynapseHub, name: str, since_seq: int, websocket: Any
) -> None:
    """Push the directed chats ``name`` missed while offline, from the durable journal.

    On a mailbox-capable reconnect the hub resumes from the client's ``since_seq``
    cursor: it reads journalled chat events after it, keeps only those directed at
    ``name`` (by name, project, glob, or a role it holds) that ``name`` did not send
    itself, and re-sends each to the reconnecting socket marked ``replayed`` with its
    durable journal ``seq`` — so the client wakes on and dedups the backlog by ``seq``
    exactly as it would live traffic. Dedup keys on ``seq``, not ``msg_id``, because
    the per-hub ``msg_id`` counter resets on restart while ``seq`` never repeats. A
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
        if str(payload.get("sender") or "") == name or not is_directed_target(target):
            continue
        if not is_recipient(target, name, roles):
            continue
        frame = dict(payload)
        frame.pop("receipt_requested", None)
        frame["replayed"] = True
        frame["seq"] = event.seq
        await hub._send_json(websocket, frame)


async def handle_heartbeat(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Register any declared roles and replay a missed directed backlog on request.

    The registration heartbeat may carry a ``roles`` list of ``<project>/<role>``
    names this identity answers to. Binding them here lets a directed message to a
    role reach its holder and show in ``/who`` instead of being counted a dead letter.
    Only a list is honoured, and non-string or blank entries are dropped rather than
    rejected, so a malformed field degrades to no roles instead of dropping the socket.
    A keepalive with no ``roles`` field leaves an earlier binding untouched.

    A mailbox-capable client also sets ``mailbox: true`` and ``since_seq`` (the last
    durable chat ``seq`` it processed) on its *registration* heartbeat, and the hub
    replays the directed messages it missed while offline from the journal — turning
    the pull-based ``syn-inbox`` catch-up into an automatic push on reconnect. Only a
    literal ``True`` triggers a replay, and a missing or malformed ``since_seq``
    degrades to ``0`` (replay the whole retained window) rather than dropping the
    socket. A keepalive omits ``mailbox``, so it never re-storms the backlog.
    """
    raw_roles = data.get("roles")
    if isinstance(raw_roles, list):
        cleaned = (item.strip() for item in raw_roles if isinstance(item, str) and item.strip())
        hub.set_agent_roles(sender, tuple(dict.fromkeys(cleaned)))
    if data.get("mailbox") is True:
        raw_since = data.get("since_seq")
        since_seq = (
            raw_since
            if isinstance(raw_since, int) and not isinstance(raw_since, bool) and raw_since >= 0
            else 0
        )
        await _replay_directed_backlog(hub, sender, since_seq, websocket)
