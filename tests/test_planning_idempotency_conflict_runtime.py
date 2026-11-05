# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared-plan idempotency conflict over a real hub
"""A reused plan key with a different body must not write the board again."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

_CONFLICT = "Idempotency key was already used for a different request."


async def _conflict(
    poster: AgentHandle,
    observer: AgentHandle,
    *,
    send: Callable[[], Awaitable[None]],
    absent_type: str,
    barrier: str,
) -> None:
    """Send a changed body, require a private conflict, and keep the public stream still."""
    observer.recorder.messages.clear()
    poster.recorder.messages.clear()
    await send()
    error = await poster.recorder.wait_for(lambda message: message.get("type") == "error")
    assert error["error_code"] == "idempotency_conflict"
    assert error["payload"] == _CONFLICT
    assert error["target"] == "P"
    await poster.agent.chat(barrier, target="WATCH")
    await observer.recorder.wait_for(lambda message: message.get("payload") == barrier)
    assert not any(message.get("type") == absent_type for message in observer.recorder.messages)


def _board(hub: SynapseHub) -> dict[str, Any]:
    """Return the live plan snapshot."""
    return hub.blackboard.snapshot()


async def test_changed_plan_body_conflicts_and_keeps_one_record(tmp_path: Path) -> None:
    """Declare, update, and progress each keep the first record when the key changes body."""
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(journal=store, anti_rollback_checkpoint=False)
    try:
        async with running_hub(hub) as (_, uri):
            poster = await connect_agent("P", uri)
            observer = await connect_agent("WATCH", uri)
            try:
                await poster.agent.post_task("T1", "Original title", idem_key="declare-1")
                posted = await observer.recorder.wait_for(
                    lambda message: message.get("type") == "ledger_task_posted"
                )
                assert posted["task"]["title"] == "Original title"

                await _conflict(
                    poster,
                    observer,
                    send=lambda: poster.agent.post_task(
                        "T1", "Changed title", idem_key="declare-1"
                    ),
                    absent_type="ledger_task_posted",
                    barrier="declare-conflict-seen",
                )
                assert _board(hub)["tasks"][0]["title"] == "Original title"

                await poster.agent.update_ledger_task("T1", status="done", idem_key="update-1")
                updated = await observer.recorder.wait_for(
                    lambda message: message.get("type") == "ledger_task_updated"
                )
                assert updated["task"]["status"] == "done"

                await _conflict(
                    poster,
                    observer,
                    send=lambda: poster.agent.update_ledger_task(
                        "T1", status="blocked", idem_key="update-1"
                    ),
                    absent_type="ledger_task_updated",
                    barrier="update-conflict-seen",
                )
                assert _board(hub)["tasks"][0]["status"] == "done"

                await poster.agent.post_progress("T1", "Kept note", idem_key="progress-1")
                noted = await observer.recorder.wait_for(
                    lambda message: message.get("type") == "ledger_progress_posted"
                )
                assert noted["note"]["text"] == "Kept note"

                await _conflict(
                    poster,
                    observer,
                    send=lambda: poster.agent.post_progress(
                        "T1", "Replacement note", idem_key="progress-1"
                    ),
                    absent_type="ledger_progress_posted",
                    barrier="progress-conflict-seen",
                )
                assert [note["text"] for note in _board(hub)["progress"]] == ["Kept note"]
                assert [
                    event.kind
                    for event in store.read_all()
                    if event.kind in {EventKind.LEDGER_TASK, EventKind.LEDGER_PROGRESS}
                ] == [
                    EventKind.LEDGER_TASK,
                    EventKind.LEDGER_TASK,
                    EventKind.LEDGER_PROGRESS,
                ]
                assert len(store.read_operations()) == 3
            finally:
                await close_agents(poster, observer)
    finally:
        store.close()
