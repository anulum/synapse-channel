# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — registration heartbeat handler (roles, wake capability and mailbox replay)
"""Registration heartbeat handler: role binding, wake capability and mailbox replay."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from synapse_channel.core.handlers.mailbox_replay import (
    mailbox_recipient,
    replay_absent_cursor,
    replay_directed_backlog,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_registry import VerbSpec
from synapse_channel.core.wake_capability import normalize_wake_capability

if TYPE_CHECKING:
    from synapse_channel.core.handlers.messaging import MessagingContext


async def handle_heartbeat(
    hub: MessagingContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Register any declared roles and replay a missed directed backlog on request.

    The registration heartbeat may carry a ``roles`` list of ``<project>/<role>``
    names this identity answers to. Binding them here lets a directed message to a
    role reach its holder and show in ``/who`` instead of being counted a dead letter.
    Only a list is honoured, and non-string or blank entries are dropped rather than
    rejected, so a malformed field degrades to no roles instead of dropping the socket.
    When role-claim enforcement is on (``--require-role-claim``), a declared role the
    role-grant store does not authorise for this identity is dropped the same forgiving
    way (see :meth:`~synapse_channel.core.hub.MessagingContext.permitted_role_claims`), so a
    socket cannot squat a role no operator granted it. A keepalive with no ``roles``
    field leaves an earlier binding untouched. With a journal, a role set that differs
    from the connection's last journalled one is recorded as a ``role_binding`` event,
    so the role mailboxes and a quiet name's last roles survive a restart.

    A mailbox-capable client also sets ``mailbox: true`` and ``since_seq`` (the last
    durable chat ``seq`` it processed) on its *registration* heartbeat, and the hub
    replays the directed messages it missed while offline from the journal — turning
    the pull-based ``syn-inbox`` catch-up into an automatic push on reconnect. Only a
    literal ``True`` triggers a replay. ``mailbox_cursor`` of ``"absent"`` with
    no ``since_seq`` anchors at the journal tip. Personal history stays in the
    journal and role mail is still replayed. Any other missing or malformed
    ``since_seq`` degrades to ``0``
    (replay the whole retained window) rather than dropping the socket. A
    keepalive omits ``mailbox``, so it never re-storms the backlog.

    An optional ``mailbox_for`` string names the identity whose backlog is wanted when
    it differs from the connection ``sender`` — a wake-listener connects under a
    receive-only ``-rx`` name but waits on its bare identity, so it declares that here
    and the replay is filtered by it. It is honoured when it names ``sender``, the
    identity ``sender`` is the ``-rx`` sidecar of, or an ACL ``mailbox`` grant on that
    identity (see :func:`mailbox_recipient`); a missing, blank, or unauthorised value
    falls back to ``sender``, so an agent connecting under its own identity needs no
    such field and no socket can pull an arbitrary identity's directed backlog by
    naming it without a grant.
    """
    raw_roles = data.get("roles")
    if isinstance(raw_roles, list):
        cleaned = (item.strip() for item in raw_roles if isinstance(item, str) and item.strip())
        declared = tuple(dict.fromkeys(cleaned))
        hub.set_agent_roles(sender, hub.permitted_role_claims(sender, declared))
        hub.mailbox_pending.bind_roles(sender, hub.roles_of(sender))
    if "wake_capability" in data:
        hub.set_wake_capability(sender, normalize_wake_capability(data.get("wake_capability")))
    if data.get("mailbox") is True:
        recipient = mailbox_recipient(sender, data.get("mailbox_for"), hub=hub)
        if data.get("mailbox_cursor") == "absent" and "since_seq" not in data:
            await replay_absent_cursor(hub, sender, recipient, websocket)
        else:
            since_seq = safe_int(data.get("since_seq"), default=0, min_value=0, allow_bool=False)
            hub.mailbox_pending.advance(
                recipient,
                since_seq,
                roles=hub.roles_of(sender),
                source="cursor",
            )
            await replay_directed_backlog(hub, sender, recipient, since_seq, websocket)


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.HEARTBEAT,),
        handler=handle_heartbeat,
        reply_types=(MessageType.CHAT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=False,
        accesses=None,
        event_kinds=(EventKind.MAILBOX_WATERMARK, EventKind.ROLE_BINDING),
        minimum_wire_version=1,
        commands=(),
    ),
)
