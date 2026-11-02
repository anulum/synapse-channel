# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an unkeyed ledger-task update journals its causal parent
"""Without an idempotency key, a task update still records the causal parent it was given."""

from __future__ import annotations

from pathlib import Path

from synapse_channel.core.handlers import planning
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.task_causality import TASK_CAUSAL_PARENT_FIELD, TaskCausalParent
from test_atomic_operation_handlers import _RecordingHub


async def test_an_unkeyed_update_journals_its_causal_parent(tmp_path: Path) -> None:
    """The update event carries the parent; the broadcast task never exposes it."""
    store = EventStore(tmp_path / "hub.db")
    try:
        hub = _RecordingHub(store)
        socket = object()
        created = {"sender": "A", "type": MessageType.LEDGER_TASK, "task_id": "T", "title": "t"}
        await planning.handle_ledger_task(hub, "A", created, socket)
        parent = TaskCausalParent("peer-hub", 7, "c" * 64)
        update = {
            "sender": "A",
            "type": MessageType.LEDGER_TASK_UPDATE,
            "task_id": "T",
            "status": "in_progress",
            "causal_parent": parent.to_dict(),
        }
        await planning.handle_ledger_task_update(hub, "A", update, socket)
        events = store.read_all()
        assert [event.kind for event in events] == [EventKind.LEDGER_TASK, EventKind.LEDGER_TASK]
        assert TASK_CAUSAL_PARENT_FIELD not in events[0].payload
        assert events[1].payload[TASK_CAUSAL_PARENT_FIELD] == parent.to_dict()
        assert events[1].payload["status"] == "in_progress"
        assert TASK_CAUSAL_PARENT_FIELD not in hub.broadcasts[-1]["task"]
        assert store.read_operations() == ()
    finally:
        store.close()
