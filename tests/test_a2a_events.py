# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for A2A bridge event fanout

from __future__ import annotations

import threading

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_events import A2ATaskEvents


def test_task_events_deliver_initial_and_terminal_snapshots() -> None:
    events = A2ATaskEvents()
    task: JsonMap = {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}
    received: list[JsonMap] = []

    worker = threading.Thread(
        target=lambda: received.extend(
            events.subscribe("task-a", task, wait_seconds=1.0, default_wait_seconds=0.0)
        )
    )
    worker.start()
    events.publish("task-a", {"id": "task-a", "status": {"state": "TASK_STATE_COMPLETED"}})
    worker.join(timeout=2.0)

    assert [event["task"]["status"]["state"] for event in received] == [
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    ]


def test_task_events_snapshot_mutable_payloads() -> None:
    events = A2ATaskEvents()
    task: JsonMap = {"id": "task-a", "status": {"state": "TASK_STATE_COMPLETED"}}

    received = events.subscribe("task-a", task, wait_seconds=0.0, default_wait_seconds=0.0)
    task["status"]["state"] = "TASK_STATE_CANCELED"

    assert received[0]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_task_events_replay_previous_updates_for_late_subscribers() -> None:
    events = A2ATaskEvents()
    task: JsonMap = {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}

    events.publish("task-a", {"id": "task-a", "status": {"state": "TASK_STATE_SUBMITTED"}})
    events.publish("task-a", task)

    received = events.subscribe("task-a", task, wait_seconds=0.0, default_wait_seconds=0.0)

    assert [event["task"]["status"]["state"] for event in received] == [
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_WORKING",
    ]
