# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — read-only snapshot handlers (state/who/history/resume/board/manifest)
"""Read-only snapshot handlers.

Each function answers a request by sending one private snapshot back to the
asking socket and mutating nothing: the lease/resource state, the online roster,
recent or cursor-bounded chat history, the shared plan board, or the capability
manifest. They share the routing signature so the dispatch table treats them
uniformly; the read handlers that need no request body simply ignore ``data``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import RECALL
from synapse_channel.core.numeric_coercion import safe_int
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.release_confirmation import read_release_confirmation
from synapse_channel.core.verb_access import fixed_access, history_access
from synapse_channel.core.verb_registry import VerbSpec
from synapse_channel.core.wake_capability import WAKE_UNKNOWN

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.capability import CapabilityRegistry
    from synapse_channel.core.claim_holder_presence import ClaimHolderPresence
    from synapse_channel.core.dead_letters import DeadLetterLedger
    from synapse_channel.core.handlers.inbox import InboxContext
    from synapse_channel.core.ledger import Blackboard
    from synapse_channel.core.mailbox_pending import MailboxPendingTracker
    from synapse_channel.core.message_forward_origin import ForwardOriginContext
    from synapse_channel.core.operator_relay_approval import RelayApprovalLedger

    class SnapshotsContext(ForwardOriginContext, InboxContext, Protocol):
        """Capabilities consumed by snapshots handlers and their callees."""

        @property
        def agent_roles(self) -> dict[str, tuple[str, ...]]:
            """Return the agent roles used by this handler family."""
            ...

        @property
        def blackboard(self) -> Blackboard:
            """Return the blackboard used by this handler family."""
            ...

        @property
        def board_task_cap(self) -> int | None:
            """Return the board task cap used by this handler family."""
            ...

        @property
        def capabilities(self) -> CapabilityRegistry:
            """Return the capabilities used by this handler family."""
            ...

        @property
        def claim_holders(self) -> ClaimHolderPresence:
            """Return the claim holders used by this handler family."""
            ...

        @property
        def config_epoch(self) -> str:
            """Return the config epoch used by this handler family."""
            ...

        @property
        def connected_clients(self) -> set[Any]:
            """Return the connected clients used by this handler family."""
            ...

        @property
        def dead_letters(self) -> DeadLetterLedger:
            """Return the dead letters used by this handler family."""
            ...

        @property
        def mailbox_pending(self) -> MailboxPendingTracker:
            """Return the mailbox pending used by this handler family."""
            ...

        def online_agents(self) -> list[str]:
            """Return the sorted names of currently registered agents."""
            ...

        @property
        def relay_approvals(self) -> RelayApprovalLedger:
            """Return the relay approvals used by this handler family."""
            ...

        def roster_liveness(self) -> dict[str, dict[str, Any]]:
            """Per-agent liveness annotation for the ``/who`` roster (handler surface).

            Thin wrapper over
            :meth:`~synapse_channel.core.hub_liveness.HubLivenessView.roster_liveness`, kept
            because the who-snapshot handler and tests call ``hub.roster_liveness``.
            """
            ...

        def wake_capability_of(self, name: str) -> str:
            """Return the declared receiver wake capability for ``name``."""
            ...


async def handle_state_request(
    hub: SnapshotsContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent a full state snapshot.

    Each active claim carries ``holder_online`` and ``holder_offline_seconds`` (``None``
    while connected), so a claim whose holder disconnected is visible as such before the
    lease window releases it.
    """
    request_id = data.get("request_id")
    request_fields = (
        {"request_id": request_id}
        if isinstance(request_id, str) and 0 < len(request_id) <= 128
        else {}
    )
    if "release_confirmation" in data:
        await hub.send_json(
            websocket,
            hub.system(
                "Release confirmation",
                msg_type=MessageType.STATE_SNAPSHOT,
                target=sender,
                **request_fields,
                release_confirmation=read_release_confirmation(
                    hub.journal, sender, data["release_confirmation"]
                ),
            ),
        )
        return
    snapshot = hub.state.snapshot()
    now = hub.claim_holders.clock()
    online = hub.clients.agent_sockets
    for claim in snapshot["active_claims"]:
        owner = str(claim.get("owner") or "")
        away = hub.claim_holders.offline_seconds(owner, online=owner in online, now=now)
        claim["holder_online"] = away is None
        claim["holder_offline_seconds"] = None if away is None else round(away, 3)
    await hub.send_json(
        websocket,
        hub.system(
            "State snapshot",
            msg_type=MessageType.STATE_SNAPSHOT,
            target=sender,
            **request_fields,
            snapshot={
                **snapshot,
                "dead_letters": hub.dead_letters.snapshot(),
                "pending_relay_approvals": hub.relay_approvals.pending(),
            },
        ),
    )


async def handle_who_request(
    hub: SnapshotsContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent the online-agent roster and the hub's pinning tag.

    A request carrying ``hub`` asks a configured message peer for its roster instead
    (:func:`~synapse_channel.core.message_forward_origin.forward_who`); the answer names
    remote seats as ``seat@HUB_ID`` and lists only namespaces the peer lets this hub see.
    """
    remote_hub = data.get("hub")
    if isinstance(remote_hub, str) and remote_hub.strip():
        from synapse_channel.core.message_forward_origin import forward_who

        await hub.send_json(websocket, await forward_who(hub, sender, remote_hub.strip()))
        return
    # Lazy: the package __init__ pulls in the handler modules, so a top-level
    # import of __version__ would be circular; by call time it is initialised.
    from synapse_channel import __version__

    # A per-agent liveness annotation is added only when the hub tracks reactions
    # (--warn-stale-recipients); otherwise the field is omitted so the who snapshot
    # is byte-for-byte unchanged on an open hub.
    extra: dict[str, Any] = {}
    liveness = hub.roster_liveness()
    if liveness:
        extra["agent_liveness"] = liveness
    wake_capabilities = {
        name: capability
        for name in hub.online_agents()
        if (capability := hub.wake_capability_of(name)) != WAKE_UNKNOWN
    }
    if wake_capabilities:
        extra["wake_capabilities"] = wake_capabilities
    if hub.clients.protocol_version_of(sender) >= 3:
        delivery_sessions = {
            name: {
                "incarnation": session.incarnation,
                "capabilities": session.capabilities,
                "hub_id": hub.hub_id,
            }
            for name in hub.online_agents()
            if (session := hub.clients.delivery_session(name)) is not None
        }
        extra["delivery_sessions"] = delivery_sessions

    await hub.send_json(
        websocket,
        hub.system(
            "Who snapshot",
            msg_type=MessageType.WHO_SNAPSHOT,
            target=sender,
            online_agents=hub.online_agents(),
            agent_roles={name: list(roles) for name, roles in hub.agent_roles.items()},
            connected_clients=len(hub.connected_clients),
            hub_version=__version__,
            config_epoch=hub.config_epoch,
            mailbox_pending=hub.mailbox_pending.snapshot(hub.online_agents(), hub.roles_of),
            **extra,
        ),
    )


async def handle_history_request(
    hub: SnapshotsContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send recent chat history, optionally selecting an exact message first.

    ``history_client_msg_id`` and ``history_target`` are optional exact string
    selectors applied before ``limit``. Invalid selectors yield no matches,
    never an unfiltered response. Existing recall ACL admission still applies.
    A versioned ``inbox_query`` selects the durable exact-identity reader,
    admitted through the mailbox ACL rather than global recall.
    """
    if "inbox_query" in data:
        from synapse_channel.core.handlers.inbox import handle_inbox_query

        await handle_inbox_query(hub, sender, data, websocket)
        return
    history = list(hub.chat_history)
    for selector, field in (
        ("history_client_msg_id", "client_msg_id"),
        ("history_target", "target"),
    ):
        if selector in data:
            value = data[selector]
            history = (
                [item for item in history if item.get(field) == value]
                if isinstance(value, str) and value
                else []
            )
    # An absent, non-numeric, or overflowing limit all read as "all" (None).
    limit = safe_int(data.get("limit"))
    if limit is None:
        requested_limit: int | str = "all"
    else:
        n = max(1, limit)
        history = history[-n:]
        requested_limit = n
    await hub.send_json(
        websocket,
        hub.system(
            "History snapshot",
            msg_type=MessageType.HISTORY_SNAPSHOT,
            target=sender,
            history=history,
            requested_limit=requested_limit,
        ),
    )


async def handle_resume_request(
    hub: SnapshotsContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent every chat message after a cursor.

    Lets a reconnected agent catch up on exactly the messages it missed,
    identified by the ``since`` chat ``msg_id`` it last saw, rather than
    pulling a fixed-size history window.

    Parameters
    ----------
    hub : SnapshotsContext
        The hub whose chat history and transport the handler uses.
    sender : str
        The requesting agent.
    data : dict[str, Any]
        The request; ``since`` is the last ``msg_id`` the agent has seen.
    websocket : Any
        The requesting socket.
    """
    # An absent, non-numeric, or overflowing cursor resumes from the start (0).
    since = safe_int(data.get("since"), default=0)
    tail = [m for m in hub.chat_history if int(m.get("msg_id", 0)) > since]
    await hub.send_json(
        websocket,
        hub.system(
            "Resume snapshot",
            msg_type=MessageType.RESUME_SNAPSHOT,
            target=sender,
            since=since,
            messages=tail,
        ),
    )


async def handle_board_request(
    hub: SnapshotsContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent a snapshot of the shared blackboard."""
    await hub.send_json(
        websocket,
        hub.system(
            "Board snapshot",
            msg_type=MessageType.BOARD_SNAPSHOT,
            target=sender,
            board=hub.blackboard.snapshot(task_cap=hub.board_task_cap),
        ),
    )


async def handle_manifest_request(
    hub: SnapshotsContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Send the requesting agent the capability manifest."""
    await hub.send_json(
        websocket,
        hub.system(
            "Manifest snapshot",
            msg_type=MessageType.MANIFEST_SNAPSHOT,
            target=sender,
            manifest=hub.capabilities.manifest(),
        ),
    )


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.STATE_REQUEST,),
        handler=handle_state_request,
        reply_types=(MessageType.STATE_SNAPSHOT,),
        mutates=False,
        replay_protected=False,
        mutation_guarded=False,
        accesses=None,
        event_kinds=(),
        minimum_wire_version=1,
        commands=("state",),
    ),
    VerbSpec(
        request_types=(MessageType.WHO_REQUEST,),
        handler=handle_who_request,
        reply_types=(MessageType.WHO_SNAPSHOT,),
        mutates=False,
        replay_protected=False,
        mutation_guarded=False,
        accesses=None,
        event_kinds=(),
        minimum_wire_version=1,
        commands=("who",),
    ),
    VerbSpec(
        request_types=(MessageType.HISTORY_REQUEST,),
        handler=handle_history_request,
        reply_types=(MessageType.HISTORY_SNAPSHOT,),
        mutates=False,
        replay_protected=False,
        mutation_guarded=False,
        accesses=history_access,
        event_kinds=(),
        minimum_wire_version=1,
        commands=(),
    ),
    VerbSpec(
        request_types=(MessageType.RESUME_REQUEST,),
        handler=handle_resume_request,
        reply_types=(MessageType.RESUME_SNAPSHOT,),
        mutates=False,
        replay_protected=False,
        mutation_guarded=False,
        accesses=fixed_access(RECALL, "history", "global"),
        event_kinds=(),
        minimum_wire_version=1,
        commands=(),
    ),
    VerbSpec(
        request_types=(MessageType.BOARD_REQUEST,),
        handler=handle_board_request,
        reply_types=(MessageType.BOARD_SNAPSHOT,),
        mutates=False,
        replay_protected=False,
        mutation_guarded=False,
        accesses=None,
        event_kinds=(),
        minimum_wire_version=1,
        commands=("board",),
    ),
    VerbSpec(
        request_types=(MessageType.MANIFEST_REQUEST,),
        handler=handle_manifest_request,
        reply_types=(MessageType.MANIFEST_SNAPSHOT,),
        mutates=False,
        replay_protected=False,
        mutation_guarded=False,
        accesses=None,
        event_kinds=(),
        minimum_wire_version=1,
        commands=("manifest",),
    ),
)
