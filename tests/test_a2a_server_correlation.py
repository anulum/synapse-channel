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
from typing import Any

from a2a_server_helpers import FakeAgent, SlowAgent
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def test_handle_synapse_frame_correlates_reply_and_completes_task() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "compute the answer"}],
        },
        target="WORKER",
    )
    assert task["status"]["state"] == "TASK_STATE_WORKING"
    reply_frame = {
        "type": "chat",
        "sender": "WORKER",
        "payload": (
            "the answer is 42\n[A2A-TASK:" + task["id"] + " contextId=" + task["contextId"] + "]"
        ),
    }
    bridge.handle_synapse_frame(reply_frame)
    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(updated.get("history", [])) >= 2
    assert any("42" in str(h) for h in updated.get("history", []))


def test_handle_synapse_frame_rejects_marker_from_wrong_sender() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "compute the answer"}],
        },
        target="WORKER",
    )

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "OTHER",
            "payload": f"wrong actor\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_WORKING"


def test_handle_synapse_frame_rejects_marker_with_wrong_context() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "compute the answer"}],
        },
        target="WORKER",
    )

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"wrong context\n[A2A-TASK:{task['id']} contextId=other-context]",
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_WORKING"
    assert updated.get("artifacts") == []


def test_handle_synapse_frame_rejects_marker_without_context() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "compute the answer"}],
        },
        target="WORKER",
    )

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"missing context\n[A2A-TASK:{task['id']}]",
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_WORKING"
    assert updated.get("artifacts") == []


def test_handle_synapse_frame_strips_correlation_marker_from_reply() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "compute the answer"}],
        },
        target="WORKER",
    )

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"answer body\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    status_message = updated["status"]["message"]
    assert status_message["parts"][0]["text"] == "answer body"


def test_fallback_correlation_preserves_fifo_tasks_for_same_sender() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    first = bridge.create_completed_task(
        {
            "taskId": "task-a",
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "first"}],
        },
        target="WORKER",
    )
    second = bridge.create_completed_task(
        {
            "taskId": "task-b",
            "messageId": "m2",
            "role": "ROLE_USER",
            "parts": [{"text": "second"}],
        },
        target="WORKER",
    )

    bridge.handle_synapse_frame({"type": "chat", "sender": "WORKER", "payload": "first reply"})

    first_updated = bridge.store.get(first["id"])
    second_updated = bridge.store.get(second["id"])
    assert first_updated is not None
    assert second_updated is not None
    assert first_updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert second_updated["status"]["state"] == "TASK_STATE_WORKING"


def test_concurrent_duplicate_task_id_creates_only_one_task() -> None:
    class SlowMissStore(A2ATaskStore):
        def get(self, task_id: str) -> dict[str, Any] | None:
            task = super().get(task_id)
            if task is None and task_id == "task-a":
                time.sleep(0.05)
                return super().get(task_id)
            return task

    bridge = A2ABridge(
        agent=FakeAgent(),
        agent_card={},
        target="WORKER",
        store=SlowMissStore(),
    )
    results: list[dict[str, Any]] = []
    errors: list[ValueError] = []

    def send(index: int) -> None:
        try:
            results.append(
                bridge.send_message(
                    {
                        "message": {
                            "taskId": "task-a",
                            "messageId": f"m-{index}",
                            "role": "ROLE_USER",
                            "parts": [{"text": f"task {index}"}],
                        }
                    }
                )
            )
        except ValueError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=send, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)

    assert len(results) == 1
    assert [str(error) for error in errors] == ["message.taskId already exists"]
    assert bridge.list_tasks()["totalSize"] == 1


def test_concurrent_direct_task_creation_serializes_same_target_submission() -> None:
    agent = SlowAgent()
    bridge = A2ABridge(agent=agent, agent_card={}, target="WORKER", store=A2ATaskStore())

    def create_task(index: int) -> None:
        bridge.create_working_task(
            {
                "taskId": f"task-{index}",
                "messageId": f"m-{index}",
                "role": "ROLE_USER",
                "parts": [{"text": f"task {index}"}],
            },
            target="WORKER",
        )

    threads = [threading.Thread(target=create_task, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)

    assert agent.max_active_chats == 1
    assert bridge.list_tasks()["totalSize"] == 2


def test_late_correlated_reply_does_not_complete_canceled_task() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    canceled = bridge.cancel_task(task["id"])
    assert canceled is not None

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"late\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_CANCELED"
    assert updated.get("artifacts") == []


def test_duplicate_correlated_reply_does_not_append_second_completion() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    frame = {
        "type": "chat",
        "sender": "WORKER",
        "payload": f"done\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
    }

    bridge.handle_synapse_frame(frame)
    completed = bridge.store.get(task["id"])
    assert completed is not None
    history_len = len(completed.get("history", []))
    artifact_len = len(completed.get("artifacts", []))
    bridge.handle_synapse_frame(frame)

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(updated.get("history", [])) == history_len
    assert len(updated.get("artifacts", [])) == artifact_len


def test_concurrent_duplicate_correlated_reply_completes_once() -> None:
    bridge = A2ABridge(agent=FakeAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    original_set_status = bridge._set_task_status

    def slow_set_status(*args: Any, **kwargs: Any) -> dict[str, Any]:
        time.sleep(0.05)
        return original_set_status(*args, **kwargs)

    bridge._set_task_status = slow_set_status  # type: ignore[method-assign]
    frame = {
        "type": "chat",
        "sender": "WORKER",
        "payload": f"done\n[A2A-TASK:{task['id']} contextId={task['contextId']}]",
    }

    threads = [
        threading.Thread(target=bridge.handle_synapse_frame, args=(frame,)) for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(updated.get("history", [])) == 2
    assert len(updated.get("artifacts", [])) == 1
