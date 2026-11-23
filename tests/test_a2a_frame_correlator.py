# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — characterisation of A2A frame correlation

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from a2a_server_helpers import RecordingAgent
from synapse_channel.a2a_runtime import build_a2a_runtime
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore

_HOLD_SECONDS = 0.4


class _GatedAgent(RecordingAgent):
    """Hold one named target so another target can pass it."""

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
        """Block the held target, then record the hand-off."""
        if target == "HELD":
            self.entered.set()
            await asyncio.to_thread(self.release.wait, _HOLD_SECONDS)
        await super().chat(payload, target=target, metadata=metadata)


def _bridge(agent: RecordingAgent | None = None, **kwargs: Any) -> A2ABridge:
    return A2ABridge(
        agent=agent or RecordingAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        **kwargs,
    )


def _message(task_id: str = "task-a") -> dict[str, Any]:
    return {
        "taskId": task_id,
        "messageId": f"message-{task_id}",
        "role": "ROLE_USER",
        "parts": [{"text": "work"}],
    }


def _reply(task: dict[str, Any], sender: str, payload: str) -> dict[str, Any]:
    return {
        "type": "chat",
        "sender": sender,
        "payload": payload,
        "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
    }


def _correlation_lock(bridge: A2ABridge) -> Any:
    """Return the one correlation lock, before and after it moves."""
    lock = getattr(bridge, "_correlation_lock", None)
    if lock is None:
        lock = bridge._correlator.lock
    return lock


def test_matching_reply_completes_the_forwarded_task() -> None:
    """A reply from the task's target completes that task."""
    bridge = _bridge()
    task = bridge.create_working_task(_message(), target="WORKER")

    bridge.handle_synapse_frame(_reply(task, "WORKER", "done"))

    stored = bridge.store.get(str(task["id"]))
    assert stored is not None
    assert stored["status"]["state"] == "TASK_STATE_COMPLETED"


def test_reply_from_another_sender_leaves_the_task_open() -> None:
    """A reply whose sender is not the task target does not complete it."""
    bridge = _bridge()
    task = bridge.create_working_task(_message(), target="WORKER")

    bridge.handle_synapse_frame(_reply(task, "OTHER", "nope"))

    stored = bridge.store.get(str(task["id"]))
    assert stored is not None
    assert stored["status"]["state"] == "TASK_STATE_WORKING"


def test_two_targets_do_not_share_a_hand_off_lock() -> None:
    """A held hand-off does not block a forward to a different target."""
    agent = _GatedAgent()
    bridge = _bridge(agent)
    worker = threading.Thread(
        target=bridge.create_working_task,
        args=(_message("held"),),
        kwargs={"target": "HELD"},
    )
    worker.start()
    assert agent.entered.wait(_HOLD_SECONDS)
    try:
        started = time.monotonic()
        bridge.create_working_task(_message("other"), target="OTHER")
        assert time.monotonic() - started < _HOLD_SECONDS / 2
    finally:
        agent.release.set()
        worker.join(_HOLD_SECONDS + 1)
    assert {target for target, _payload in agent.messages} == {"HELD", "OTHER"}


def test_one_target_serialises_forwards() -> None:
    """Two hand-offs to the same target do not run their chats together."""
    agent = _GatedAgent()
    bridge = _bridge(agent)
    first = threading.Thread(
        target=bridge.create_working_task,
        args=(_message("first"),),
        kwargs={"target": "HELD"},
    )
    first.start()
    assert agent.entered.wait(_HOLD_SECONDS)
    entered_second = threading.Event()

    def second() -> None:
        bridge.create_working_task(_message("second"), target="HELD")
        entered_second.set()

    follower = threading.Thread(target=second)
    follower.start()
    try:
        assert entered_second.wait(0.1) is False
    finally:
        agent.release.set()
        first.join(_HOLD_SECONDS + 1)
        follower.join(_HOLD_SECONDS + 1)
    assert entered_second.is_set()
    assert [payload for _target, payload in agent.messages] == ["work", "work"]


def test_replacing_the_agent_is_used_by_the_next_forward() -> None:
    """An assignment after construction reaches the next hand-off."""
    first = RecordingAgent()
    second = RecordingAgent()
    bridge = _bridge(first)

    bridge.agent = second
    bridge.create_working_task(_message())

    assert first.messages == []
    assert [target for target, _payload in second.messages] == ["WORKER"]


def test_reply_and_expiry_share_one_lock() -> None:
    """Expiry waits for an in-flight reply and then leaves that reply in place."""
    bridge = _bridge(task_timeout_seconds=1.0)
    task = bridge.create_working_task(_message(), target="WORKER")
    stored = bridge.store.get(str(task["id"]))
    assert stored is not None
    stored["metadata"]["updatedAt"] = 0.0
    bridge.store.put(stored)
    started = threading.Event()
    lock = _correlation_lock(bridge)

    def expire() -> None:
        started.set()
        bridge.expire_timed_out_tasks(now=time.time())

    lock.acquire()
    try:
        worker = threading.Thread(target=expire)
        worker.start()
        assert started.wait(1.0)
        time.sleep(0.05)
        bridge.handle_synapse_frame(_reply(task, "WORKER", "done"))
    finally:
        lock.release()
    worker.join(1.0)

    finished = bridge.store.get(str(task["id"]))
    assert finished is not None
    assert finished["status"]["state"] == "TASK_STATE_COMPLETED"
    assert worker.is_alive() is False


def test_builder_gives_execution_the_correlator_lock() -> None:
    """Execution and the correlator are one lock, on the builder and the bridge."""
    bridge = _bridge()
    assert bridge.execution.transition_lock is bridge._correlator.lock
    runtime = build_a2a_runtime(
        agent=RecordingAgent(),
        store=A2ATaskStore(),
        run=bridge._run,
        write_status=bridge._set_task_status,
        publish=bridge._publish_task_update,
    )
    assert runtime.execution.transition_lock is runtime.correlator.lock
    bound: Any = runtime.execution.forward
    assert bound.__self__ is runtime.correlator
