# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — plan assignment commit and metadata projection tests
"""Exercise task event preparation through real admitted plan writers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, replay
from synapse_channel.core.notification_events import NOTIFICATION_FIELD
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.task_causality import TASK_CAUSAL_PARENT_FIELD, TaskCausalParent
from test_hub_activity_network import fence, query
from test_pending_work_network import pending_query


@pytest.mark.parametrize("keyed", [False, True])
async def test_assignment_transition_and_causal_record_are_committed_once(
    tmp_path: Path, keyed: bool
) -> None:
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, hub_id="plan-hub", anti_rollback_checkpoint=False)
    try:
        async with running_hub(hub) as (_, uri):
            operator = await connect_agent("ALPHA/operator", uri)
            worker = await connect_agent("ALPHA/worker", uri)
            try:
                parent = TaskCausalParent("foreign-observation", 1, "a" * 64)
                await operator.agent.post_task(
                    "ALPHA/task",
                    "assigned later",
                    causal_parent=parent,
                    idem_key="declare" if keyed else None,
                )
                await fence(operator)
                assert (await query(worker))["items"] == []
                await operator.agent.update_ledger_task(
                    "ALPHA/task",
                    suggested_owner="ALPHA/worker",
                    expected_version=999,
                    idem_key="rejected-assignment" if keyed else None,
                )
                await operator.recorder.wait_for(lambda item: item.get("type") == MessageType.ERROR)
                assert (await query(worker))["items"] == []
                assert hub.blackboard.tasks["ALPHA/task"].suggested_owner == ""
                await operator.agent.update_ledger_task(
                    "ALPHA/task",
                    suggested_owner="ALPHA/worker",
                    causal_parent=parent,
                    idem_key="assignment" if keyed else None,
                )
                await fence(operator)
                page = await query(worker)
                assert [item["kind"] for item in page["items"]] == ["tasks"]
                records = store.read_since(0, kinds=(EventKind.LEDGER_TASK,))
                assert len(records) == 2
                assert NOTIFICATION_FIELD not in records[0].payload
                assert records[1].payload[NOTIFICATION_FIELD]["recipient"] == "ALPHA/worker"
                assert records[1].payload[TASK_CAUSAL_PARENT_FIELD] == parent.to_dict()
                await operator.agent.update_ledger_task(
                    "ALPHA/task",
                    status="done",
                    suggested_owner="ALPHA/worker",
                    idem_key="finish" if keyed else None,
                )
                await fence(operator)
                assert len((await query(worker))["items"]) == 1
                await operator.agent.post_task(
                    "ALPHA/task", "repeat declaration", suggested_owner="ALPHA/worker"
                )
                await fence(operator)
                assert len((await query(worker))["items"]) == 1
                snapshot = hub.blackboard.tasks["ALPHA/task"].as_dict()
                assert (
                    NOTIFICATION_FIELD not in snapshot and TASK_CAUSAL_PARENT_FIELD not in snapshot
                )
                await operator.agent.update_ledger_task("ALPHA/missing", status="open")
                await operator.recorder.wait_for(lambda item: item.get("type") == MessageType.ERROR)
            finally:
                await close_agents(operator, worker)
        restored = replay(store)
        assert restored.blackboard.tasks["ALPHA/task"].suggested_owner == "ALPHA/worker"
    finally:
        store.close()


async def _updated(handle: AgentHandle, task_id: str, status: str) -> dict[str, Any]:
    """Wait for the live plan frame of one status write."""
    return await handle.recorder.wait_for(
        lambda item: (
            item.get("type") == MessageType.LEDGER_TASK_UPDATED
            and item.get("task", {}).get("task_id") == task_id
            and item.get("task", {}).get("status") == status
        )
    )


async def test_work_returned_from_finished_reaches_its_owner(tmp_path: Path) -> None:
    """A finished task set back by someone else notifies its owner like a new assignment.

    The owner setting their own task back, and a change that stays unfinished,
    add neither an activity item nor a frame pointer. Historical pending work
    stays empty, as it does for a new assignment.
    """
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(journal=store, hub_id="return-hub", anti_rollback_checkpoint=False)
    try:
        async with running_hub(hub) as (_, uri):
            planner = await connect_agent("ALPHA/planner", uri)
            worker = await connect_agent("ALPHA/worker", uri)
            try:
                await planner.agent.post_task(
                    "ALPHA/job",
                    "private title",
                    suggested_owner="ALPHA/worker",
                    project="ALPHA",
                )
                await fence(planner)
                assigned = await query(worker)
                assert [item["kind"] for item in assigned["items"]] == ["tasks"]
                assert (await pending_query(worker))["items"] == []

                worker.recorder.messages.clear()
                await planner.agent.update_ledger_task("ALPHA/job", status="in_progress")
                moving = await _updated(worker, "ALPHA/job", "in_progress")
                assert NOTIFICATION_FIELD not in moving
                await fence(planner)
                assert [item["kind"] for item in (await query(worker))["items"]] == ["tasks"]

                await worker.agent.update_ledger_task("ALPHA/job", status="done")
                await fence(worker)
                assert [item["kind"] for item in (await query(worker))["items"]] == ["tasks"]

                worker.recorder.messages.clear()
                await worker.agent.update_ledger_task("ALPHA/job", status="open")
                own = await _updated(worker, "ALPHA/job", "open")
                assert NOTIFICATION_FIELD not in own
                await fence(worker)
                assert [item["kind"] for item in (await query(worker))["items"]] == ["tasks"]
                assert (await pending_query(worker))["items"] == []

                worker.recorder.messages.clear()
                await planner.agent.update_ledger_task("ALPHA/job", status="done")
                await fence(planner)
                await planner.agent.update_ledger_task("ALPHA/job", status="open")
                returned = await _updated(worker, "ALPHA/job", "open")
                assert returned[NOTIFICATION_FIELD] == {
                    "version": 1,
                    "kind": "tasks",
                    "actor": "ALPHA/planner",
                    "recipient": "ALPHA/worker",
                }
                await fence(planner)
                page = await query(worker)
                assert [item["kind"] for item in page["items"]] == ["tasks", "tasks"]
                assert (await pending_query(worker))["items"] == []
            finally:
                await close_agents(planner, worker)
        restored = replay(store)
        assert restored.blackboard.tasks["ALPHA/job"].suggested_owner == "ALPHA/worker"
        assert not restored.blackboard.pending_assignments.entries
    finally:
        store.close()
