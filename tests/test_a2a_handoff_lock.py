# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A hand-off outside the bridge-wide creation lock
"""A slow hand-off to the hub must not delay unrelated A2A task acceptance.

The bridge reserves a task by durably recording its working state inside a
short critical section and performs the hand-off afterwards, outside the
bridge-wide creation lock. Two protections are kept: a second send that reuses
a reserved task id is refused at once, and hand-offs to the *same* target stay
in send order because fallback reply correlation pairs a sender's replies with
its pending tasks in that order.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from a2a_server_helpers import RecordingAgent
from synapse_channel import a2a_errors
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore

HELD_SECONDS = 2.0


class GatedAgent(RecordingAgent):
    """Agent whose hand-off of the ``slow`` message blocks until released."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    async def chat(
        self,
        payload: str,
        *,
        target: str = "all",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Hold the ``slow`` hand-off, then record every hand-off."""
        if payload == "slow":
            self.entered.set()
            await asyncio.to_thread(self.release.wait, HELD_SECONDS)
        await super().chat(payload, target=target, metadata=metadata)


def _message(text: str, *, task_id: str | None = None, target: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "messageId": f"m-{text}-{time.monotonic_ns()}",
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    if task_id is not None:
        message["taskId"] = task_id
    if target is not None:
        message["metadata"] = {"target": target}
    return message


def _bridge(agent: GatedAgent) -> A2ABridge:
    return A2ABridge(agent=agent, agent_card={}, target="WORKER", store=A2ATaskStore())


def _start_slow_send(bridge: A2ABridge, **fields: Any) -> threading.Thread:
    """Start the held hand-off in a worker thread and wait until it is in flight."""
    agent = bridge.agent
    assert isinstance(agent, GatedAgent)
    worker = threading.Thread(
        target=bridge.send_message, args=({"message": _message("slow", **fields)},)
    )
    worker.start()
    assert agent.entered.wait(HELD_SECONDS), "the held hand-off never started"
    return worker


def test_unrelated_task_is_accepted_while_another_hand_off_is_held() -> None:
    """Acceptance latency of a task for another target stays far below the hold."""
    agent = GatedAgent()
    bridge = _bridge(agent)
    worker = _start_slow_send(bridge)
    try:
        started = time.monotonic()
        result = bridge.send_message({"message": _message("fast", target="OTHER")})
        latency = time.monotonic() - started
        assert latency < HELD_SECONDS / 4, f"acceptance waited {latency:.3f}s behind the hand-off"
        assert result["task"]["status"]["state"] == "TASK_STATE_WORKING"
        assert agent.messages == [("OTHER", "fast")]
    finally:
        agent.release.set()
        worker.join(HELD_SECONDS + 1)
    assert sorted(text for _target, text in agent.messages) == ["fast", "slow"]


def test_reserved_task_id_is_refused_while_its_hand_off_is_held() -> None:
    """The short critical section still reserves the id before the hand-off."""
    agent = GatedAgent()
    bridge = _bridge(agent)
    worker = _start_slow_send(bridge, task_id="task-reserved")
    try:
        assert bridge.store.get("task-reserved") is not None
        with pytest.raises(a2a_errors.A2AConflictError, match="already exists"):
            bridge.send_message({"message": _message("again", task_id="task-reserved")})
    finally:
        agent.release.set()
        worker.join(HELD_SECONDS + 1)
    assert [text for _target, text in agent.messages] == ["slow"]


def test_continuing_a_task_does_not_wait_for_an_unrelated_held_hand_off() -> None:
    """An A2A 1.0 continuation of another task is not serialised behind the hold."""
    agent = GatedAgent()
    bridge = _bridge(agent)
    first = bridge.send_message({"message": _message("first", task_id="task-open", target="OTHER")})
    assert first["task"]["status"]["state"] == "TASK_STATE_WORKING"
    worker = _start_slow_send(bridge)
    try:
        started = time.monotonic()
        continued = bridge.send_message(
            {"message": _message("next", task_id="task-open")}, protocol_version="1.0"
        )
        latency = time.monotonic() - started
        assert latency < HELD_SECONDS / 4, f"continuation waited {latency:.3f}s"
        assert continued["task"]["id"] == "task-open"
    finally:
        agent.release.set()
        worker.join(HELD_SECONDS + 1)


class SignallingStore(A2ATaskStore):
    """Task store that signals once a chosen task id is durably recorded."""

    def __init__(self, watched_id: str) -> None:
        super().__init__()
        self.watched_id = watched_id
        self.recorded = threading.Event()

    def put(self, task: dict[str, Any]) -> dict[str, Any]:
        """Store ``task`` and signal when it carries the watched id."""
        stored = super().put(task)
        if stored["id"] == self.watched_id:
            self.recorded.set()
        return stored


def test_hand_offs_to_the_same_target_keep_their_send_order() -> None:
    """A second task for the held target is reserved at once and forwarded after it."""
    agent = GatedAgent()
    store = SignallingStore("task-second")
    bridge = A2ABridge(agent=agent, agent_card={}, target="WORKER", store=store)
    worker = _start_slow_send(bridge)
    second = threading.Thread(
        target=bridge.send_message,
        args=({"message": _message("second", task_id="task-second")},),
    )
    second.start()
    try:
        assert store.recorded.wait(HELD_SECONDS), (
            "the second task was not reserved while the first hand-off was held"
        )
        assert agent.messages == []
    finally:
        agent.release.set()
        worker.join(HELD_SECONDS + 1)
        second.join(HELD_SECONDS + 1)
    assert [text for _target, text in agent.messages] == ["slow", "second"]
