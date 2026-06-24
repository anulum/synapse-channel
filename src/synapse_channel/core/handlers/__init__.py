# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — message-type dispatch registry for the hub
"""Declarative dispatch table mapping each message type to its handler.

The hub's routing core looks a parsed, sender-resolved message up in
:data:`DISPATCH` and awaits the matched handler, falling back to an
unknown-type error when there is no entry. Each handler is a free coroutine that
takes the hub as its first argument and reaches the shared state, journal, and
transport through it — so adding a verb is one table entry plus one function, and
the routing core stays a lookup rather than a growing ``if`` ladder. Every
resource alias maps to the single resource handler.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from synapse_channel.core.handlers.leasing import (
    handle_checkpoint,
    handle_claim,
    handle_handoff,
    handle_release,
    handle_task_update,
    handle_wait_request,
)
from synapse_channel.core.handlers.memory import handle_recall_log
from synapse_channel.core.handlers.messaging import handle_chat, handle_heartbeat
from synapse_channel.core.handlers.offerings import handle_advertise, handle_resource
from synapse_channel.core.handlers.planning import (
    handle_ledger_progress,
    handle_ledger_task,
    handle_ledger_task_update,
)
from synapse_channel.core.handlers.snapshots import (
    handle_board_request,
    handle_history_request,
    handle_manifest_request,
    handle_resume_request,
    handle_state_request,
    handle_who_request,
)
from synapse_channel.core.protocol import RESOURCE_TYPE_ALIASES, MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub

Handler = Callable[["SynapseHub", str, dict[str, Any], Any], Awaitable[None]]
"""A message handler: ``(hub, sender, data, websocket) -> awaitable[None]``."""

DISPATCH: dict[str, Handler] = {
    MessageType.CHAT: handle_chat,
    MessageType.HEARTBEAT: handle_heartbeat,
    MessageType.CLAIM: handle_claim,
    MessageType.RELEASE: handle_release,
    MessageType.STATE_REQUEST: handle_state_request,
    MessageType.WHO_REQUEST: handle_who_request,
    MessageType.HISTORY_REQUEST: handle_history_request,
    MessageType.RESUME_REQUEST: handle_resume_request,
    MessageType.WAIT_REQUEST: handle_wait_request,
    MessageType.TASK_UPDATE: handle_task_update,
    MessageType.HANDOFF: handle_handoff,
    MessageType.CHECKPOINT: handle_checkpoint,
    MessageType.LEDGER_TASK: handle_ledger_task,
    MessageType.LEDGER_TASK_UPDATE: handle_ledger_task_update,
    MessageType.LEDGER_PROGRESS: handle_ledger_progress,
    MessageType.BOARD_REQUEST: handle_board_request,
    MessageType.ADVERTISE: handle_advertise,
    MessageType.MANIFEST_REQUEST: handle_manifest_request,
    MessageType.RECALL_LOG: handle_recall_log,
    **{alias: handle_resource for alias in RESOURCE_TYPE_ALIASES},
}

__all__ = ["DISPATCH", "Handler"]
