# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the LLM-free stall supervisor

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.supervisor import SupervisorWorker, detect_stalls


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


class FakeAgent:
    """Stand-in for SynapseAgent capturing the supervisor's actions."""

    def __init__(self, *, ready: bool = True, connect_exc: Exception | None = None) -> None:
        self.running = True
        self.board_requests = 0
        self.progress: list[tuple[str, str, str]] = []
        self.updates: list[tuple[str, str | None]] = []
        self._ready = ready
        self._connect_exc = connect_exc

    async def connect(self) -> None:
        if self._connect_exc is not None:
            raise self._connect_exc

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._ready

    async def request_board(self) -> None:
        self.board_requests += 1

    async def post_progress(self, task_id: str, text: str, *, kind: str = "note") -> None:
        self.progress.append((task_id, text, kind))

    async def update_ledger_task(
        self, task_id: str, *, status: str | None = None, suggested_owner: str | None = None
    ) -> None:
        self.updates.append((task_id, status))


def _worker(**kwargs: Any) -> SupervisorWorker:
    worker = SupervisorWorker(clock=lambda: 1000.0, settle_seconds=0.0, **kwargs)
    worker.agent = FakeAgent()  # type: ignore[assignment]
    return worker


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
    worker = _worker(idle_seconds=300.0)
    worker.latest_board = _board([{"task_id": "T1", "status": "in_progress", "updated_at": 0.0}])
    applied = await worker.evaluate_and_apply()
    assert [i.task_id for i in applied] == ["T1"]
    agent: FakeAgent = worker.agent  # type: ignore[assignment]
    assert agent.updates == [("T1", "open")]
    assert agent.progress[0][0] == "T1"
    assert agent.progress[0][2] == "assessment"


async def test_cycle_requests_board_then_applies() -> None:
    worker = _worker(idle_seconds=300.0)
    worker.latest_board = _board([{"task_id": "T1", "status": "blocked", "updated_at": 0.0}])
    applied = await worker._cycle()
    agent: FakeAgent = worker.agent  # type: ignore[assignment]
    assert agent.board_requests == 1
    assert [i.task_id for i in applied] == ["T1"]


async def test_cycle_settles_before_evaluating(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = SupervisorWorker(clock=lambda: 1000.0, settle_seconds=0.1)
    worker.agent = FakeAgent()  # type: ignore[assignment]
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("synapse_channel.supervisor.asyncio.sleep", fake_sleep)
    await worker._cycle()
    assert 0.1 in slept


async def test_supervise_loop_runs_a_pass_then_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _worker()

    async def stop_after_sleep(_seconds: float) -> None:
        worker.agent.running = False  # end the loop after the first interval

    monkeypatch.setattr("synapse_channel.supervisor.asyncio.sleep", stop_after_sleep)
    await worker._supervise_loop()
    agent: FakeAgent = worker.agent  # type: ignore[assignment]
    assert agent.board_requests == 1


async def test_run_completes_when_connection_finishes() -> None:
    worker = SupervisorWorker(settle_seconds=0.0)
    worker.agent = FakeAgent(ready=True)  # type: ignore[assignment]
    await worker.run()  # connect returns -> supervise task cancelled -> run returns


async def test_run_warns_on_handshake_timeout(capsys: pytest.CaptureFixture[str]) -> None:
    worker = SupervisorWorker(settle_seconds=0.0)
    worker.agent = FakeAgent(ready=False)  # type: ignore[assignment]
    await worker.run()
    assert "handshake timeout" in capsys.readouterr().out


async def test_run_reports_connection_error(capsys: pytest.CaptureFixture[str]) -> None:
    worker = SupervisorWorker(settle_seconds=0.0)
    worker.agent = FakeAgent(connect_exc=RuntimeError("dropped"))  # type: ignore[assignment]
    await worker.run()
    assert "supervisor stopped: dropped" in capsys.readouterr().out
