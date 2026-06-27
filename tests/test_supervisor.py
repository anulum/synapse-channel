# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the LLM-free stall supervisor

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, cast

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel.client.supervisor import SupervisorWorker, detect_stalls
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType


def _board(
    tasks: list[dict[str, Any]], progress: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"tasks": tasks, "progress": progress or [], "ready": []}


# --- detect_stalls (pure policy) ---------------------------------------------


def test_idle_in_progress_task_is_reoffered() -> None:
    board = _board([{"task_id": "T1", "status": "in_progress", "updated_at": 0.0}])
    out = detect_stalls(board, now=1000.0, idle_seconds=300.0)
    assert [i.task_id for i in out] == ["T1"]
    assert out[0].action == "reoffer"
    assert "no progress" in out[0].reason


def test_recent_progress_note_keeps_task_active() -> None:
    board = _board(
        [{"task_id": "T1", "status": "in_progress", "updated_at": 0.0}],
        progress=[{"task_id": "T1", "posted_at": 900.0}],
    )
    assert detect_stalls(board, now=1000.0, idle_seconds=300.0) == []


def test_recent_status_change_keeps_task_active() -> None:
    board = _board([{"task_id": "T1", "status": "in_progress", "updated_at": 950.0}])
    assert detect_stalls(board, now=1000.0, idle_seconds=300.0) == []


def test_blocked_task_with_satisfied_dependencies_is_reoffered() -> None:
    board = _board(
        [
            {"task_id": "D", "status": "done", "updated_at": 0.0},
            {"task_id": "T1", "status": "blocked", "updated_at": 0.0, "depends_on": ["D"]},
        ]
    )
    out = detect_stalls(board, now=1000.0, idle_seconds=300.0)
    assert [i.task_id for i in out] == ["T1"]
    assert out[0].reason == "dependencies satisfied"


def test_blocked_task_with_unfinished_dependency_is_left() -> None:
    board = _board(
        [
            {"task_id": "D", "status": "in_progress", "updated_at": 999.0},
            {"task_id": "T1", "status": "blocked", "updated_at": 0.0, "depends_on": ["D"]},
        ]
    )
    assert detect_stalls(board, now=1000.0, idle_seconds=300.0) == []


def test_blocked_task_with_no_dependencies_is_a_stale_block() -> None:
    board = _board([{"task_id": "T1", "status": "blocked", "updated_at": 0.0}])
    assert [i.task_id for i in detect_stalls(board, now=1000.0, idle_seconds=300.0)] == ["T1"]


def test_open_and_terminal_tasks_are_ignored() -> None:
    board = _board(
        [
            {"task_id": "O", "status": "open", "updated_at": 0.0},
            {"task_id": "DN", "status": "done", "updated_at": 0.0},
            {"task_id": "CX", "status": "cancelled", "updated_at": 0.0},
        ]
    )
    assert detect_stalls(board, now=1000.0, idle_seconds=300.0) == []


def test_detect_stalls_sorts_multiple_interventions() -> None:
    board = _board(
        [
            {"task_id": "B", "status": "blocked", "updated_at": 0.0},
            {"task_id": "A", "status": "in_progress", "updated_at": 0.0},
        ]
    )
    assert [i.task_id for i in detect_stalls(board, now=1000.0, idle_seconds=300.0)] == ["A", "B"]


def test_detect_stalls_on_empty_board() -> None:
    assert detect_stalls({}, now=1000.0) == []


# --- SupervisorWorker --------------------------------------------------------


def _worker(**kwargs: Any) -> SupervisorWorker:
    params: dict[str, Any] = {"clock": lambda: 1000.0, "settle_seconds": 0.0}
    params.update(kwargs)
    return SupervisorWorker(**params)


async def _start_supervisor_agent(worker: SupervisorWorker) -> asyncio.Task[None]:
    task = asyncio.create_task(worker.agent.connect())
    if not await worker.agent.wait_until_ready(3.0):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        raise TimeoutError("supervisor agent did not connect")
    return task


async def _stop_supervisor_agent(worker: SupervisorWorker, task: asyncio.Task[None]) -> None:
    worker.agent.running = False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _post_task(
    uri: str,
    task_id: str,
    *,
    status: str = "open",
    depends_on: list[str] | None = None,
) -> AgentHandle:
    handle = await connect_agent(f"PLANNER-{task_id}", uri)
    await handle.agent.post_task(task_id, title=task_id, depends_on=depends_on or [])
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == MessageType.LEDGER_TASK_POSTED
            and message.get("task", {}).get("task_id") == task_id
        )
    )
    if status != "open":
        await handle.agent.update_ledger_task(task_id, status=status)
        await handle.recorder.wait_for(
            lambda message: (
                message.get("type") == MessageType.LEDGER_TASK_UPDATED
                and message.get("task", {}).get("task_id") == task_id
                and message.get("task", {}).get("status") == status
            )
        )
    return handle


def test_interval_and_settle_are_clamped() -> None:
    worker = SupervisorWorker(interval=0.0, settle_seconds=-1.0)
    assert worker.interval == 1.0
    assert worker.settle_seconds == 0.0


async def test_on_message_captures_board_and_ignores_others() -> None:
    worker = _worker()
    await worker.on_message({"type": "board_snapshot", "board": {"tasks": []}})
    assert worker.latest_board == {"tasks": []}
    await worker.on_message({"type": "chat", "payload": "noise"})
    assert worker.latest_board == {"tasks": []}  # unchanged


async def test_evaluate_and_apply_without_board_is_noop() -> None:
    worker = _worker()
    assert await worker.evaluate_and_apply() == []


async def test_evaluate_and_apply_reoffers_and_flags() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        planner = await _post_task(uri, "T1", status="in_progress")
        observer = await connect_agent("OBSERVER", uri)
        worker = _worker(uri=uri, idle_seconds=300.0)
        task = await _start_supervisor_agent(worker)
        worker.latest_board = _board(
            [{"task_id": "T1", "status": "in_progress", "updated_at": 0.0}]
        )
        try:
            applied = await worker.evaluate_and_apply()
            progress = await observer.recorder.wait_for(
                lambda message: (
                    message.get("type") == MessageType.LEDGER_PROGRESS_POSTED
                    and message.get("note", {}).get("task_id") == "T1"
                    and message.get("note", {}).get("kind") == "assessment"
                )
            )
            update = await observer.recorder.wait_for(
                lambda message: (
                    message.get("type") == MessageType.LEDGER_TASK_UPDATED
                    and message.get("task", {}).get("task_id") == "T1"
                    and message.get("task", {}).get("status") == "open"
                )
            )
        finally:
            await _stop_supervisor_agent(worker, task)
            await close_agents(observer, planner)

    assert [i.task_id for i in applied] == ["T1"]
    assert progress["note"]["kind"] == "assessment"
    assert update["task"]["status"] == "open"


async def test_cycle_requests_board_then_applies() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        planner = await _post_task(uri, "T1", status="blocked")
        worker = _worker(uri=uri, idle_seconds=0.0, settle_seconds=0.05)
        task = await _start_supervisor_agent(worker)
        try:
            applied = await worker._cycle()
        finally:
            await _stop_supervisor_agent(worker, task)
            await close_agents(planner)

    assert worker.latest_board is not None
    assert [i.task_id for i in applied] == ["T1"]


async def test_cycle_settles_before_evaluating() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        worker = _worker(uri=uri, settle_seconds=0.03)
        task = await _start_supervisor_agent(worker)
        started = time.monotonic()
        try:
            await worker._cycle()
        finally:
            await _stop_supervisor_agent(worker, task)

    assert time.monotonic() - started >= 0.02


async def test_cycle_without_settle_evaluates_immediately() -> None:
    worker = _worker(settle_seconds=0.0)

    class FakeAgent:
        async def request_board(self) -> None:
            worker.latest_board = {"tasks": [], "progress": []}

    worker.agent = cast(Any, FakeAgent())

    applied = await worker._cycle()

    assert worker.latest_board == {"tasks": [], "progress": []}
    assert applied == []


async def test_supervise_loop_runs_a_live_pass() -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        worker = _worker(uri=uri, settle_seconds=0.03, interval=1.0)
        task = await _start_supervisor_agent(worker)
        loop_task = asyncio.create_task(worker._supervise_loop())
        try:
            deadline = time.monotonic() + 1.0
            while worker.latest_board is None and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert worker.latest_board is not None
        finally:
            worker.agent.running = False
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
            await _stop_supervisor_agent(worker, task)


async def test_supervise_loop_sleeps_after_one_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _worker(interval=3.0)
    sleeps: list[float] = []

    async def fake_cycle() -> list[Any]:
        worker.agent.running = False
        return []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(worker, "_cycle", fake_cycle)
    monkeypatch.setattr("synapse_channel.client.supervisor.asyncio.sleep", fake_sleep)
    worker.agent.running = True

    await worker._supervise_loop()

    assert sleeps == [3.0]


async def test_run_completes_when_connection_finishes() -> None:
    worker = SupervisorWorker(uri=f"ws://127.0.0.1:{_free_port()}", ready_timeout=0.1)
    await worker.run()


async def test_run_with_ready_short_connection_does_not_warn(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAgent:
        running = False

        async def connect(self) -> None:
            await asyncio.sleep(60.0)

        async def wait_until_ready(self, timeout: float) -> bool:
            return True

    worker = SupervisorWorker(ready_timeout=0.1)
    worker.agent = cast(Any, FakeAgent())

    await worker.run()

    assert "handshake timeout" not in capsys.readouterr().out


async def test_run_warns_on_handshake_timeout(capsys: pytest.CaptureFixture[str]) -> None:
    worker = SupervisorWorker(uri=f"ws://127.0.0.1:{_free_port()}", ready_timeout=0.1)
    await worker.run()
    assert "handshake timeout" in capsys.readouterr().out


async def test_run_reports_connection_error(capsys: pytest.CaptureFixture[str]) -> None:
    worker = SupervisorWorker(uri="not-a-websocket-uri", ready_timeout=0.1)
    await worker.run()
    assert "supervisor stopped:" in capsys.readouterr().out
