# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real shared-plan storage refusal and recovery tests
"""Exercise shared-plan journal failures over actual WebSocket connections."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

_Operation = Literal["declare", "redeclare", "update", "progress"]
_PRIVATE_DIAGNOSTIC = "PRIVATE_STORAGE_CANARY /private/storage/events.db SQL journal failure"


async def _write_plan(agent: AgentHandle, operation: _Operation, key: str | None) -> None:
    """Send one shared-plan mutation through the supported Python client API."""
    if operation in {"declare", "redeclare"}:
        await agent.agent.post_task("T1", "New title", idem_key=key)
    elif operation == "update":
        await agent.agent.update_ledger_task("T1", status="done", idem_key=key)
    else:
        await agent.agent.post_progress("T1", "Recovered progress", idem_key=key)


@pytest.mark.parametrize("operation", ["declare", "redeclare", "update", "progress"])
@pytest.mark.parametrize("keyed", [False, True])
async def test_plan_storage_refusal_is_private_atomic_and_recoverable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, operation: _Operation, keyed: bool
) -> None:
    """A real SQLite abort exposes no diagnostic and permits exactly one later commit.

    Cover each writer with and without durable idempotency. A second connection
    observes the same ordered stream, proving no failed candidate is broadcast.
    Closing and reopening the event store proves successful recovery survives
    restart rather than merely changing the current hub's in-memory board.
    """
    path = tmp_path / "events.db"
    store = EventStore(path)
    hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
    accepted_type = {
        "declare": "ledger_task_posted",
        "redeclare": "ledger_task_posted",
        "update": "ledger_task_updated",
        "progress": "ledger_progress_posted",
    }[operation]
    key = "recover-plan-write" if keyed else None
    try:
        async with running_hub(hub) as (_, uri):
            poster = await connect_agent("P", uri)
            observer = await connect_agent("WATCH", uri)
            try:
                if operation != "declare":
                    await poster.agent.post_task("T1", "Original title")
                    await observer.recorder.wait_for(
                        lambda message: message.get("type") == "ledger_task_posted"
                    )
                before_board = hub.blackboard.snapshot()
                before_events = store.read_all()
                before_operations = store.read_operations()
                observer_start = len(observer.recorder.messages)
                with sqlite3.connect(path) as connection:
                    connection.execute(
                        "CREATE TRIGGER refuse_plan BEFORE INSERT ON events "
                        "WHEN NEW.kind IN ('ledger_task', 'ledger_progress') "
                        f"BEGIN SELECT RAISE(ABORT, '{_PRIVATE_DIAGNOSTIC}'); END"
                    )
                await _write_plan(poster, operation, key)
                error = await poster.recorder.wait_for(
                    lambda message: message.get("type") == "error"
                )
                subject = "Progress note" if operation == "progress" else "Task 'T1'"
                assert error["payload"] == f"{subject} was not journalled; mutation rolled back."
                assert error["target"] == "P"
                assert _PRIVATE_DIAGNOSTIC not in json.dumps(poster.recorder.messages)
                assert hub.blackboard.snapshot() == before_board
                assert store.read_all() == before_events
                assert store.read_operations() == before_operations
                assert _PRIVATE_DIAGNOSTIC in caplog.text

                # This ordered public chat is a barrier after the refused write.
                await poster.agent.chat("still-connected", target="WATCH")
                await observer.recorder.wait_for(
                    lambda message: message.get("payload") == "still-connected"
                )
                failed_stream = observer.recorder.messages[observer_start:]
                assert not any(message.get("type") == accepted_type for message in failed_stream)
                assert not any(message.get("type") == "error" for message in failed_stream)
                assert _PRIVATE_DIAGNOSTIC not in json.dumps(failed_stream)

                with sqlite3.connect(path) as connection:
                    connection.execute("DROP TRIGGER refuse_plan")
                observer.recorder.messages.clear()
                poster.recorder.messages.clear()
                await _write_plan(poster, operation, key)
                await observer.recorder.wait_for(
                    lambda message: message.get("type") == accepted_type
                )
                recovered_board = hub.blackboard.snapshot()
                recovered_events = [
                    event
                    for event in store.read_all()
                    if event.kind in {EventKind.LEDGER_TASK, EventKind.LEDGER_PROGRESS}
                ]
                before_plan_events = [
                    event
                    for event in before_events
                    if event.kind in {EventKind.LEDGER_TASK, EventKind.LEDGER_PROGRESS}
                ]
                assert len(recovered_events) == len(before_plan_events) + 1
                assert len(store.read_operations()) == len(before_operations) + int(keyed)
                if keyed:
                    await _write_plan(poster, operation, key)
                    await poster.recorder.wait_for(
                        lambda message: message.get("type") == accepted_type
                    )
                    await poster.agent.chat("retry-complete", target="WATCH")
                    await observer.recorder.wait_for(
                        lambda message: message.get("payload") == "retry-complete"
                    )
                    assert hub.blackboard.snapshot() == recovered_board
                    assert len(
                        [
                            event
                            for event in store.read_all()
                            if event.kind in {EventKind.LEDGER_TASK, EventKind.LEDGER_PROGRESS}
                        ]
                    ) == len(recovered_events)
                    assert (
                        sum(
                            message.get("type") == accepted_type
                            for message in observer.recorder.messages
                        )
                        == 1
                    )
            finally:
                await close_agents(poster, observer)
    finally:
        store.close()

    reopened = EventStore(path)
    try:
        restarted = SynapseHub(journal=reopened, anti_rollback_checkpoint=False)
        async with running_hub(restarted) as (_, uri):
            reader = await connect_agent("RESTART-READER", uri)
            try:
                assert restarted.blackboard.snapshot() == recovered_board
                await reader.agent.send_message("board_request")
                snapshot = await reader.recorder.wait_for(
                    lambda message: message.get("type") == "board_snapshot"
                )
                assert snapshot["board"] == recovered_board
                assert _PRIVATE_DIAGNOSTIC not in json.dumps(reader.recorder.messages)
            finally:
                await close_agents(reader)
    finally:
        reopened.close()
