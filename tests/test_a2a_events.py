# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for A2A bridge event fanout

from __future__ import annotations

import threading
import time

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


def test_task_events_deliver_to_registered_subscriber_and_cleanup() -> None:
    events = A2ATaskEvents()
    task: JsonMap = {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}
    received: list[JsonMap] = []

    worker = threading.Thread(
        target=lambda: received.extend(
            events.subscribe("task-a", task, wait_seconds=1.0, default_wait_seconds=0.0)
        )
    )
    worker.start()
    deadline = time.monotonic() + 1.0
    while "task-a" not in events._subscribers and time.monotonic() < deadline:
        time.sleep(0.01)
    events.publish("task-a", {"id": "task-a", "status": {"state": "TASK_STATE_COMPLETED"}})
    worker.join(timeout=2.0)

    assert [event["task"]["status"]["state"] for event in received] == [
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    ]
    assert "task-a" not in events._subscribers


def test_task_events_cleanup_keeps_other_active_subscribers_until_they_exit() -> None:
    events = A2ATaskEvents()
    task: JsonMap = {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}
    first: list[JsonMap] = []
    second: list[JsonMap] = []

    first_worker = threading.Thread(
        target=lambda: first.extend(
            events.subscribe("task-a", task, wait_seconds=1.0, default_wait_seconds=0.0)
        )
    )
    second_worker = threading.Thread(
        target=lambda: second.extend(
            events.subscribe("task-a", task, wait_seconds=1.0, default_wait_seconds=0.0)
        )
    )
    first_worker.start()
    second_worker.start()
    deadline = time.monotonic() + 1.0
    while len(events._subscribers.get("task-a", [])) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    events._subscribers["task-a"][0].put(
        {"task": {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}}
    )
    first_worker.join(timeout=2.0)

    assert len(events._subscribers.get("task-a", [])) == 1

    events.publish("task-a", {"id": "task-a", "status": {"state": "TASK_STATE_COMPLETED"}})
    second_worker.join(timeout=2.0)

    assert first[-1]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    assert second[-1]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "task-a" not in events._subscribers


def test_task_events_cleanup_tolerates_already_removed_subscriber() -> None:
    events = A2ATaskEvents()
    task: JsonMap = {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}
    received: list[JsonMap] = []

    worker = threading.Thread(
        target=lambda: received.extend(
            events.subscribe("task-a", task, wait_seconds=0.05, default_wait_seconds=0.0)
        )
    )
    worker.start()
    deadline = time.monotonic() + 1.0
    while "task-a" not in events._subscribers and time.monotonic() < deadline:
        time.sleep(0.01)
    events._subscribers.clear()
    worker.join(timeout=2.0)

    assert received == [{"task": task}]
    assert events._subscribers == {}


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


def test_task_events_bound_replay_history() -> None:
    events = A2ATaskEvents(max_history_events=2)
    current: JsonMap = {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}

    events.publish("task-a", {"id": "task-a", "status": {"state": "TASK_STATE_SUBMITTED"}})
    events.publish("task-a", {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}})
    events.publish("task-a", current)

    received = events.subscribe("task-a", current, wait_seconds=0.0, default_wait_seconds=0.0)

    assert [event["task"]["status"]["state"] for event in received] == [
        "TASK_STATE_WORKING",
        "TASK_STATE_WORKING",
    ]


def test_task_events_terminal_history_returns_current_then_replayed_terminal() -> None:
    events = A2ATaskEvents()
    current: JsonMap = {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}

    events.publish("task-a", {"id": "task-a", "status": {"state": "TASK_STATE_COMPLETED"}})

    received = events.subscribe("task-a", current, wait_seconds=0.0, default_wait_seconds=0.0)

    assert [event["task"]["status"]["state"] for event in received] == [
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    ]


def test_task_events_default_wait_and_negative_wait_do_not_block() -> None:
    events = A2ATaskEvents(max_history_events=0)
    task: JsonMap = {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}

    default_wait = events.subscribe("task-a", task, wait_seconds=None, default_wait_seconds=0.0)
    negative_wait = events.subscribe("task-b", task, wait_seconds=-5.0, default_wait_seconds=1.0)

    assert default_wait[0]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    assert negative_wait[0]["task"]["status"]["state"] == "TASK_STATE_WORKING"


def test_task_events_positive_wait_times_out_without_update() -> None:
    events = A2ATaskEvents()
    task: JsonMap = {"id": "task-a", "status": {"state": "TASK_STATE_WORKING"}}

    received = events.subscribe("task-a", task, wait_seconds=0.01, default_wait_seconds=1.0)

    assert received == [{"task": task}]
    assert "task-a" not in events._subscribers


def test_task_events_invalid_status_shapes_are_non_terminal() -> None:
    events = A2ATaskEvents()

    no_task = events.subscribe("task-a", {}, wait_seconds=0.0, default_wait_seconds=0.0)
    no_status = events.subscribe(
        "task-b", {"id": "task-b", "status": "bad"}, wait_seconds=0.0, default_wait_seconds=0.0
    )

    assert no_task == [{"task": {}}]
    assert no_status == [{"task": {"id": "task-b", "status": "bad"}}]
    assert events._last_state([]) == ""
    assert events._last_state([{"task": "bad"}]) == ""
