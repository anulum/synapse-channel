# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox recipient resolution and reconnect backlog replay
"""Mailbox recipient resolution and reconnect directed-backlog replay helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import (
    MAILBOX,
    WOULD_ALLOW,
    Target,
    evaluate_access,
)
from synapse_channel.core.agent_liveness import waiter_sidecar_names
from synapse_channel.core.dead_letters import is_directed_target
from synapse_channel.core.identity_namespace import project_of
from synapse_channel.core.journal import EventKind
from synapse_channel.core.mailbox_pending import replayed_frames
from synapse_channel.core.protocol import is_recipient, system_message

if TYPE_CHECKING:
    from synapse_channel.core.handlers.messaging import MessagingContext

MAILBOX_REPLAY_READ_CAP = 1000
"""Upper bound on journal chat events scanned for one reconnect backlog replay.

A briefly-offline recipient's missed directed messages sit within a bounded window
of recent chat, so scanning at most this many keeps a reconnect cheap and bounds a
mailbox heartbeat's cost. A gap so long that a recipient's messages fall beyond the
window degrades to the durable feed (``syn-inbox``), which has no such bound — the
backlog replay adds promptness, it does not replace the feed as the unbounded record.
"""


async def replay_directed_backlog(
    hub: MessagingContext, name: str, recipient: str, since_seq: int, websocket: Any
) -> None:
    """Push the directed chats ``recipient`` missed while offline, from the durable journal.

    On a mailbox-capable reconnect the hub resumes from the client's ``since_seq``
    cursor: it reads journalled chat events after it and keeps those directed at
    ``recipient`` (by name, project or glob) that ``recipient`` did not send itself.
    Mail addressed to a role is replayed from the role's own mailbox instead: for each
    role the connection holds, the chats still pending after the role's watermark,
    however far back they lie, because the role's mail may predate this holder. The
    two sets are merged by ``seq`` without duplicates and re-sent to the reconnecting
    socket marked ``replayed`` with their durable journal ``seq`` — so the client wakes
    on and dedups the backlog by ``seq`` exactly as it would live traffic. Dedup keys on
    ``seq``, not ``msg_id``, because the per-hub ``msg_id`` counter resets on restart
    while ``seq`` never repeats.

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
    personal = [
        event
        for event in events
        if str(event.payload.get("sender") or "") != recipient
        and is_directed_target(str(event.payload.get("target") or "all"))
        and is_recipient(str(event.payload.get("target") or "all"), recipient)
    ]
    role_mail = hub.mailbox_pending.role_backlog(roles, limit=MAILBOX_REPLAY_READ_CAP)
    await send_frames(hub, websocket, replayed_frames(personal, role_mail))


async def send_frames(hub: MessagingContext, websocket: Any, frames: list[dict[str, Any]]) -> None:
    """Write replay frames in order, holding the socket until the batch is out.

    Parameters
    ----------
    hub : MessagingContext
        Hub that owns the socket's broadcaster.
    websocket : Any
        The reconnecting socket.
    frames : list of dict
        Frames already marked for replay. An empty list writes nothing.
    """
    if not frames:
        return
    # One backlog write holds live frames for this socket until the older
    # sequences are written, so a live frame cannot land between them.
    broadcaster = getattr(hub, "_broadcaster", None)
    send_many = getattr(broadcaster, "send_json_many", None)
    if send_many is not None:
        await send_many(websocket, frames)
        return
    for frame in frames:
        await hub.send_json(websocket, frame)


async def replay_absent_cursor(
    hub: MessagingContext, name: str, recipient: str, websocket: Any
) -> None:
    """Anchor a missing waiter cursor without replaying personal history.

    The anchor is the journal tip at this heartbeat. Personal mail already in
    the journal stays there. The anchor frame carries ``mailbox_pending_before``,
    the pending count for this identity read before the watermark moves, so the
    waiter can say how many messages addressed to that name are still unread.
    ``--since 0`` reaches them. Mail for a role this connection holds is still
    pushed, because a role's backlog is not one seat's old transcript. The
    socket is told the anchor even when nothing was replayed.

    Parameters
    ----------
    hub : MessagingContext
        Hub whose journal and mailbox tracker supply the replay.
    name : str
        Connection name the roles were bound to.
    recipient : str
        Identity whose exact-name mailbox is wanted.
    websocket : Any
        Socket to push the replay and the anchor notice to.
    """
    journal = hub.journal
    anchor = journal.max_seq() if journal is not None else 0
    roles = hub.roles_of(name)
    pending_before = hub.mailbox_pending.pending_count(recipient)
    role_mail = hub.mailbox_pending.role_backlog(roles, limit=MAILBOX_REPLAY_READ_CAP)
    await send_frames(hub, websocket, replayed_frames(role_mail))
    await hub.send_json(
        websocket,
        system_message(
            "mailbox cursor anchored",
            hub_id=str(getattr(hub, "hub_id", "") or ""),
            mailbox_anchor=anchor,
            mailbox_pending_before=pending_before,
        ),
    )
    hub.mailbox_pending.advance(recipient, anchor, roles=roles, source="cursor")


def mailbox_acl_allows(hub: MessagingContext, connection: str, requested: str) -> bool:
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


def mailbox_recipient(connection: str, declared: Any, hub: MessagingContext | None = None) -> str:
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
    if connection in waiter_sidecar_names(requested):
        return requested
    if hub is not None and mailbox_acl_allows(hub, connection, requested):
        return requested
    return connection
