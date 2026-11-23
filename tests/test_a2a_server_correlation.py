# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from a2a_server_helpers import RecordingAgent, SlowRecordingAgent
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


async def test_create_working_task_forwards_metadata_correlated_request_through_live_hub() -> None:
    async with running_hub() as (_hub, uri):
        bridge_handle = await connect_agent("A2A-BRIDGE", uri)
        worker = await connect_agent("WORKER", uri)
        submitted: list[asyncio.Task[Any]] = []

        def submit(coro: Any) -> asyncio.Task[Any]:
            task = asyncio.create_task(coro)
            submitted.append(task)
            return task

        try:
            bridge = A2ABridge(
                agent=bridge_handle.agent,
                agent_card={},
                target="WORKER",
                store=A2ATaskStore(),
                submit=submit,
            )
            task = bridge.create_working_task(
                {
                    "taskId": "task-live-forward",
                    "contextId": "ctx-live-forward",
                    "messageId": "m-live-forward",
                    "role": "ROLE_USER",
                    "parts": [{"text": "compute through the live hub"}],
                },
                target="WORKER",
            )
            await asyncio.gather(*submitted)
            frame = await worker.recorder.wait_for(
                lambda message: (
                    message.get("type") == "chat"
                    and message.get("sender") == "A2A-BRIDGE"
                    and message.get("target") == "WORKER"
                    and "compute through the live hub" in str(message.get("payload", ""))
                )
            )
        finally:
            await close_agents(worker, bridge_handle)

    assert task["id"] == "task-live-forward"
    assert frame.get("payload") == "compute through the live hub"
    assert frame.get("metadata") == {
        "a2aTaskId": "task-live-forward",
        "a2aContextId": "ctx-live-forward",
        "a2aOperationId": task["metadata"]["synapseExecutionOutcome"]["operation_id"],
    }


def test_handle_synapse_frame_correlates_reply_and_completes_task() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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
        "payload": "the answer is 42",
        "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
    }
    bridge.handle_synapse_frame(reply_frame)
    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(updated.get("history", [])) >= 2
    assert any("42" in str(h) for h in updated.get("history", []))


def test_handle_synapse_frame_rejects_metadata_from_wrong_sender() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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
            "payload": "wrong actor",
            "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_WORKING"


def test_handle_synapse_frame_rejects_metadata_with_wrong_context() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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
            "payload": "wrong context",
            "metadata": {"a2aTaskId": task["id"], "a2aContextId": "other-context"},
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_WORKING"
    assert updated.get("artifacts") == []


def test_handle_synapse_frame_rejects_metadata_without_context() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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
            "payload": "missing context",
            "metadata": {"a2aTaskId": task["id"]},
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_WORKING"
    assert updated.get("artifacts") == []


def test_handle_synapse_frame_preserves_reply_body_under_metadata_correlation() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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
            "payload": "answer body",
            "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    status_message = updated["status"]["message"]
    assert status_message["parts"][0]["text"] == "answer body"


def test_fallback_correlation_preserves_fifo_tasks_for_same_sender() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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
        agent=RecordingAgent(),
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
    agent = SlowRecordingAgent()
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
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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
            "payload": "late",
            "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_CANCELED"
    assert updated.get("artifacts") == []


def test_duplicate_correlated_reply_does_not_append_second_completion() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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
        "payload": "done",
        "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
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
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
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

    bridge._set_task_status = slow_set_status  # type: ignore[method-assign] # probe double
    frame = {
        "type": "chat",
        "sender": "WORKER",
        "payload": "done",
        "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
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


def _fallback_ids(bridge: A2ABridge, target: str) -> list[str]:
    """Return one target's fallback queue on the bridge or its correlator."""
    holder: Any = bridge
    correlator = getattr(holder, "_correlator", None)
    pending = correlator._pending if correlator is not None else holder._pending_by_target
    return [str(task_id) for task_id in pending.get(target, ())]


def _user_message(task_id: str, context_id: str, message_id: str, text: str) -> dict[str, Any]:
    """Build one user message the bridge can forward."""
    return {
        "taskId": task_id,
        "contextId": context_id,
        "messageId": message_id,
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }


def test_metadata_completion_leaves_the_next_plain_reply_for_the_later_task() -> None:
    """A correlated completion leaves the sender's next plain reply for the later task."""
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    first = bridge.create_completed_task(
        _user_message("task-a", "ctx-a", "m1", "first"),
        target="WORKER",
    )
    second = bridge.create_completed_task(
        _user_message("task-b", "ctx-b", "m2", "second"),
        target="WORKER",
    )

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": "answer for a",
            "metadata": {"a2aTaskId": first["id"], "a2aContextId": first["contextId"]},
        }
    )
    bridge.handle_synapse_frame({"type": "chat", "sender": "WORKER", "payload": "answer for b"})

    first_updated = bridge.store.get(first["id"])
    second_updated = bridge.store.get(second["id"])
    assert first_updated is not None
    assert second_updated is not None
    assert first_updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert second_updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert second_updated["status"]["message"]["parts"][0]["text"] == "answer for b"


def test_a_second_forward_keeps_one_fallback_slot_for_the_later_task() -> None:
    """Two forwards of one open task leave the later task's plain reply free."""
    agent = RecordingAgent()
    bridge = A2ABridge(agent=agent, agent_card={}, target="WORKER", store=A2ATaskStore())
    first = bridge.create_completed_task(
        _user_message("task-a", "ctx-a", "m1", "first forward"),
        target="WORKER",
    )
    bridge.send_message(
        {"message": _user_message(first["id"], first["contextId"], "m1-again", "second forward")},
        protocol_version="1.0",
    )
    assert [target for target, _payload in agent.messages] == ["WORKER", "WORKER"]
    assert _fallback_ids(bridge, "WORKER").count(first["id"]) == 1

    second = bridge.create_completed_task(
        _user_message("task-b", "ctx-b", "m2", "later"),
        target="WORKER",
    )
    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": "answer for a",
            "metadata": {"a2aTaskId": first["id"], "a2aContextId": first["contextId"]},
        }
    )
    bridge.handle_synapse_frame({"type": "chat", "sender": "WORKER", "payload": "answer for b"})

    first_updated = bridge.store.get(first["id"])
    second_updated = bridge.store.get(second["id"])
    assert first_updated is not None
    assert second_updated is not None
    assert first_updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert second_updated["status"]["state"] == "TASK_STATE_COMPLETED"
    assert second_updated["status"]["message"]["parts"][0]["text"] == "answer for b"


def test_a_different_exchange_reply_leaves_the_task_open_and_pending() -> None:
    """A reply for a different exchange leaves the task open and still pending."""
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        _user_message("task-a", "ctx-a", "m1", "compute"),
        target="WORKER",
    )
    expected = task["metadata"]["synapseExecutionOutcome"]["operation_id"]
    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": "wrong exchange",
            "metadata": {
                "a2aTaskId": task["id"],
                "a2aContextId": task["contextId"],
                "a2aOperationId": f"{expected}-other",
            },
        }
    )

    updated = bridge.store.get(task["id"])
    assert updated is not None
    assert updated["status"]["state"] == "TASK_STATE_WORKING"
    assert updated.get("artifacts") == []
    assert _fallback_ids(bridge, "WORKER") == [task["id"]]
