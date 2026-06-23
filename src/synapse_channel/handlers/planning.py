# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared-plan handlers (declare/update task, post progress)
"""Shared-plan handlers writing to the blackboard.

These apply the collaborative plan: declaring or re-declaring a task with its
dependency edges, changing a task's status or suggested owner, and appending a
structured progress note. Each accepted write is journalled (when a durable log
is attached) and broadcast to the channel; a rejected one is privately reported
to the sender.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from synapse_channel.journal import record_ledger_progress, record_ledger_task
from synapse_channel.ledger import ProgressNote
from synapse_channel.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.hub import SynapseHub


async def handle_ledger_task(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Declare or re-declare a plan task and broadcast it, or reject it."""
    task_id = str(data.get("task_id") or "").strip()
    raw_deps = data.get("depends_on")
    depends_on = [str(d) for d in raw_deps] if isinstance(raw_deps, list) else []
    ok, message = hub.blackboard.post_task(
        task_id=task_id,
        title=str(data.get("title") or ""),
        author=sender,
        description=str(data.get("description") or ""),
        depends_on=depends_on,
        suggested_owner=str(data.get("suggested_owner") or ""),
    )
    if ok:
        task = hub.blackboard.tasks[task_id]
        if hub.journal is not None:
            record_ledger_task(hub.journal, task)
        await hub._broadcast(
            hub._system(
                message,
                msg_type=MessageType.LEDGER_TASK_POSTED,
                task=task.as_dict(),
            )
        )
        return
    await hub._send_json(websocket, hub._system(message, msg_type=MessageType.ERROR, target=sender))


async def handle_ledger_task_update(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Apply a plan-status/suggested-owner change and broadcast it, or reject."""
    task_id = str(data.get("task_id") or "").strip()
    status = data.get("status")
    suggested_owner = data.get("suggested_owner")
    ok, message = hub.blackboard.update_task(
        task_id,
        status=str(status) if status is not None else None,
        suggested_owner=str(suggested_owner) if suggested_owner is not None else None,
    )
    if ok:
        task = hub.blackboard.tasks[task_id]
        if hub.journal is not None:
            record_ledger_task(hub.journal, task)
        await hub._broadcast(
            hub._system(
                message,
                msg_type=MessageType.LEDGER_TASK_UPDATED,
                task=task.as_dict(),
            )
        )
        return
    await hub._send_json(websocket, hub._system(message, msg_type=MessageType.ERROR, target=sender))


async def handle_ledger_progress(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Append a structured progress note and broadcast it, or reject the kind."""
    ok, result = hub.blackboard.post_progress(
        task_id=str(data.get("task_id") or ""),
        author=sender,
        text=str(data.get("text") or data.get("payload") or ""),
        kind=str(data.get("kind") or "note"),
    )
    if not ok or not isinstance(result, ProgressNote):
        await hub._send_json(
            websocket, hub._system(str(result), msg_type=MessageType.ERROR, target=sender)
        )
        return
    if hub.journal is not None:
        record_ledger_progress(hub.journal, result)
    await hub._broadcast(
        hub._system(
            f"Progress from {sender}",
            msg_type=MessageType.LEDGER_PROGRESS_POSTED,
            note=result.as_dict(),
        )
    )
