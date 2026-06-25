# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — concurrency tests for the A2A bridge

from __future__ import annotations

import threading
from typing import Any

from a2a_server_helpers import RecordingAgent
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _bridge() -> A2ABridge:
    return A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())


def _message(task_id: str, text: str) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "messageId": f"message-{task_id}",
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }


def test_concurrent_fallback_replies_complete_same_target_tasks_once() -> None:
    bridge = _bridge()
    first = bridge.create_working_task(_message("task-a", "first"), target="WORKER")
    second = bridge.create_working_task(_message("task-b", "second"), target="WORKER")

    threads = [
        threading.Thread(
            target=bridge.handle_synapse_frame,
            args=({"type": "chat", "sender": "WORKER", "payload": payload},),
        )
        for payload in ("first reply", "second reply")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)

    first_updated = bridge.store.get(str(first["id"]))
    second_updated = bridge.store.get(str(second["id"]))
    assert first_updated is not None
    assert second_updated is not None
    assert first_updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert second_updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(first_updated.get("artifacts", [])) == 1
    assert len(second_updated.get("artifacts", [])) == 1


def test_wrong_sender_pressure_does_not_consume_fifo_fallback_slot() -> None:
    bridge = _bridge()
    task = bridge.create_working_task(_message("task-a", "work"), target="WORKER")

    threads = [
        threading.Thread(
            target=bridge.handle_synapse_frame,
            args=({"type": "chat", "sender": "OTHER", "payload": f"noise-{index}"},),
        )
        for index in range(16)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)

    bridge.handle_synapse_frame({"type": "chat", "sender": "WORKER", "payload": "real reply"})

    updated = bridge.store.get(str(task["id"]))
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert updated["status"]["message"]["parts"][0]["text"] == "real reply"
    assert len(updated.get("artifacts", [])) == 1


def test_concurrent_subscribers_receive_terminal_fanout() -> None:
    bridge = _bridge()
    task = bridge.create_working_task(_message("task-a", "fanout"), target="WORKER")
    received: list[list[dict[str, Any]]] = []
    received_lock = threading.Lock()
    ready = threading.Barrier(9)

    def subscribe() -> None:
        ready.wait(timeout=2.0)
        events = bridge.subscribe_task_events(str(task["id"]), wait_seconds=1.0) or []
        with received_lock:
            received.append(events)

    threads = [threading.Thread(target=subscribe) for _ in range(8)]
    for thread in threads:
        thread.start()
    ready.wait(timeout=2.0)
    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"done\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )
    for thread in threads:
        thread.join(timeout=2.0)

    assert len(received) == 8
    assert all(
        events[-1]["task"]["status"]["state"] == "TASK_STATE_COMPLETED" for events in received
    )
