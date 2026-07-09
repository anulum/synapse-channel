# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from a2a_server_helpers import RecordingAgent
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def test_timeout_marks_open_task_failed() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        task_timeout_seconds=1.0,
    )
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    task["metadata"]["updatedAt"] = 10.0
    bridge.store.put(task)

    failed = bridge.expire_timed_out_tasks(now=12.0)

    assert len(failed) == 1
    assert failed[0]["status"]["state"] == "TASK_STATE_FAILED"


def test_cancel_task_keeps_terminal_task_state_unchanged() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": "done",
            "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
        }
    )
    completed = bridge.store.get(task["id"])
    assert completed is not None

    canceled = bridge.cancel_task(task["id"])

    assert canceled == completed
    stored = bridge.store.get(task["id"])
    assert stored is not None
    assert stored["status"]["state"] == "TASK_STATE_COMPLETED"


def test_late_reply_after_timeout_does_not_reopen_failed_task() -> None:
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        task_timeout_seconds=1.0,
    )
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    task["metadata"]["updatedAt"] = 10.0
    bridge.store.put(task)
    bridge.expire_timed_out_tasks(now=12.0)

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": "late",
            "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
        }
    )

    stored = bridge.store.get(task["id"])
    assert stored is not None
    assert stored["status"]["state"] == "TASK_STATE_FAILED"
    assert stored.get("artifacts") == []


def test_state_file_recovery_fails_stale_working_tasks(tmp_path: Path) -> None:
    state_file = tmp_path / "a2a-state.json"
    store = A2ATaskStore(storage_path=state_file)
    store.put(
        {
            "id": "task-a",
            "contextId": "ctx",
            "status": {"state": "TASK_STATE_WORKING"},
            "history": [],
            "artifacts": [],
            "metadata": {"synapseTarget": "WORKER", "updatedAt": 1.0},
        }
    )
    loaded = A2ATaskStore(storage_path=state_file)

    A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=loaded,
        task_timeout_seconds=1.0,
    )

    recovered = loaded.get("task-a")
    assert recovered is not None
    assert recovered["status"]["state"] == "TASK_STATE_FAILED"


def test_subscription_queue_receives_terminal_update() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    events: list[dict[str, Any]] = []

    def collect_events() -> None:
        subscribed = bridge.subscribe_task_events(task["id"], wait_seconds=1.0) or []
        events.extend(subscribed)

    worker = threading.Thread(
        target=collect_events,
    )
    worker.start()
    # completing the task before the subscription registers would replay only
    # the terminal state; wait for the live queue before publishing
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not bridge._events.has_subscribers(task["id"]):
        time.sleep(0.005)
    assert bridge._events.has_subscribers(task["id"])
    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": "done",
            "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
        }
    )
    worker.join(timeout=2.0)

    assert [event["task"]["status"]["state"] for event in events] == [
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    ]
