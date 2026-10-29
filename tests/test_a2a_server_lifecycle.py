# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

import asyncio
import math
import queue
import threading
from pathlib import Path
from typing import Any, cast

import pytest

from a2a_server_helpers import RecordingAgent
from synapse_channel import a2a_errors
from synapse_channel.a2a_conformance_backend import ConformanceA2ABridge
from synapse_channel.a2a_server import A2ABridge, SynapseAgentRuntime
from synapse_channel.a2a_store import A2ATaskStore
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.http_authority import endpoint_authorities, normalise_authority


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


class _SignallingSubscribers(dict[str, list[queue.Queue[dict[str, Any]]]]):
    """Subscriber map that signals once a live queue is registered."""

    def __init__(self) -> None:
        super().__init__()
        self.registered = threading.Event()

    def setdefault(
        self, key: str, default: list[queue.Queue[dict[str, Any]]], /
    ) -> list[queue.Queue[dict[str, Any]]]:
        """Register like ``dict.setdefault`` and signal the waiting publisher."""
        result = super().setdefault(key, default)
        self.registered.set()
        return result


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
    registry = _SignallingSubscribers()
    bridge._events._subscribers = registry

    def collect_events() -> None:
        subscribed = bridge.subscribe_task_events(task["id"], wait_seconds=1.0) or []
        events.extend(subscribed)

    worker = threading.Thread(
        target=collect_events,
    )
    worker.start()
    # completing the task before the subscription registers would answer from
    # replay only; publish once the live queue is registered
    assert registry.registered.wait(2.0), "the subscriber never registered its live queue"
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


def test_agent_card_authorities_use_only_http_json_and_legacy_urls_once() -> None:
    """Foreign interface entries are ignored and repeated authorities are kept once."""
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={
            "supportedInterfaces": [
                "not-an-interface",
                {"protocolBinding": "GRPC", "url": "grpc://grpc.example.test:50051"},
                {"protocolBinding": "HTTP+JSON", "url": "https://a2a.example.test/a2a/v1"},
            ],
            "url": "https://a2a.example.test/legacy",
        },
        target="WORKER",
        store=A2ATaskStore(),
    )

    expected = tuple(
        normalise_authority(item)
        for item in endpoint_authorities("https://a2a.example.test/a2a/v1")
    )
    assert bridge.allowed_authorities == expected
    assert all("grpc.example.test" not in item for item in bridge.allowed_authorities)


@pytest.mark.parametrize("url", [None, "", "   "])
def test_advertised_http_json_interface_without_url_is_refused(url: object) -> None:
    """An HTTP+JSON interface that names no endpoint cannot define a trusted authority."""
    with pytest.raises(ValueError, match="requires an endpoint URL"):
        A2ABridge(
            agent=RecordingAgent(),
            agent_card={"supportedInterfaces": [{"protocolBinding": "http+json", "url": url}]},
            target="WORKER",
            store=A2ATaskStore(),
        )


def test_origin_allow_list_without_trusted_authority_is_refused() -> None:
    """Browser Origins cannot be admitted while the bridge knows no endpoint authority."""
    with pytest.raises(ValueError, match="requires a trusted endpoint authority"):
        A2ABridge(
            agent=RecordingAgent(),
            agent_card={},
            target="WORKER",
            store=A2ATaskStore(),
            allowed_origins=["https://ui.example.test"],
        )


def test_send_configuration_must_be_an_object() -> None:
    """A non-object send configuration is a validation error before any forward."""
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    with pytest.raises(a2a_errors.A2AValidationError, match="configuration must be an object"):
        bridge.send_message(
            {
                "message": {
                    "messageId": "m-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "work"}],
                },
                "configuration": ["not", "an", "object"],
            }
        )
    assert bridge.agent.messages == []


def test_conformance_answer_cannot_reuse_an_existing_task_id() -> None:
    """A canned TCK answer never overwrites a task that already exists."""
    bridge = ConformanceA2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
    )
    existing = bridge.create_working_task(
        {"taskId": "task-kept", "messageId": "first", "role": "ROLE_USER", "parts": [{"text": "a"}]}
    )
    with pytest.raises(a2a_errors.A2AConflictError, match="already exists"):
        bridge.send_message(
            {
                "message": {
                    "messageId": "tck-artifact-text-session",
                    "taskId": "task-kept",
                    "role": "ROLE_USER",
                    "parts": [{"text": "b"}],
                }
            }
        )
    assert bridge.store.get("task-kept") == existing


@pytest.mark.parametrize("deadline", [0.0, -1.0, math.inf, math.nan])
def test_runtime_refuses_an_unusable_caller_deadline_and_closes_the_coroutine(
    deadline: float,
) -> None:
    """An exhausted or non-finite deadline times out at once without scheduling work."""
    runtime = SynapseAgentRuntime(cast("SynapseAgent", RecordingAgent()))
    try:
        coroutine = asyncio.sleep(0)
        with pytest.raises(TimeoutError, match="timed out"):
            runtime.run(coroutine, timeout_seconds=deadline)
        with pytest.raises(RuntimeError, match="cannot reuse already awaited coroutine"):
            coroutine.send(None)
    finally:
        runtime.loop.close()
