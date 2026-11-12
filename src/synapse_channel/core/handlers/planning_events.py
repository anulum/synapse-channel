# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — task snapshot and assignment event preparation
"""Prepare identical task routing metadata for keyed and ordinary board writes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from synapse_channel.core.atomic_operations import OperationDraft
from synapse_channel.core.journal import EventKind, record_ledger_task
from synapse_channel.core.ledger import Blackboard, LedgerTask
from synapse_channel.core.notification_events import NOTIFICATION_FIELD, task_notification
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.task_causality import TaskCausalParent, task_event_payload

if TYPE_CHECKING:
    from synapse_channel.core.handler_context import HandlerContext


class TaskEventRecorder:
    """Capture prior assignment inside the mutation and persist its routing event."""

    def __init__(self, actor: str, parent: TaskCausalParent | None) -> None:
        """Keep authenticated actor, validated causal parent and pre-write owner."""
        self.actor = actor
        self.parent = parent
        self.previous_owner = ""

    def capture_owner(self, board: Blackboard, task_id: str) -> None:
        """Read only the actor-owned candidate board before changing its task."""
        task = board.tasks.get(task_id)
        self.previous_owner = task.suggested_owner if task is not None else ""

    def record(self, store: EventStore, task: LedgerTask) -> None:
        """Preserve the historical journal call when no new metadata is present."""
        notification = task_notification(task.as_dict(), self.actor, self.previous_owner)
        if notification is not None:
            record_ledger_task(store, task, causal_parent=self.parent, notification=notification)
        elif self.parent is not None:
            record_ledger_task(store, task, causal_parent=self.parent)
        else:
            record_ledger_task(store, task)

    def draft(
        self,
        result: tuple[bool, str, LedgerTask | None],
        *,
        hub: HandlerContext,
        msg_type: str,
        family: str,
    ) -> OperationDraft | None:
        """Prepare one native effect and live frame with identical trusted metadata."""
        ok, message, task = result
        if not ok or task is None:
            return None
        snapshot = task.as_dict()
        notification = task_notification(snapshot, self.actor, self.previous_owner)
        response = hub.system(message, msg_type=msg_type, task=snapshot)
        if notification is not None:
            response[NOTIFICATION_FIELD] = notification
        payload = task_event_payload(
            snapshot, self.parent, notification=notification, origin=task.assignment_origin
        )
        return OperationDraft(
            response=response,
            events=((EventKind.LEDGER_TASK, payload),),
            intent={"family": family, "response_type": msg_type},
        )
