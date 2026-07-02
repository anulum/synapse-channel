# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — workflow CLI regressions

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.cli_workflow import (
    _AgentGateway,
    _cmd_run,
    _drive_run,
    _render_run,
    _snapshot_from_board,
    add_parsers,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.workflow import compile_to_tasks, parse_workflow
from synapse_channel.core.workflow_driver import WorkflowState
from synapse_channel.core.workflow_run import RunResult

_GOOD = {
    "name": "release",
    "steps": [
        {"id": "build", "title": "Build", "task_class": "ci"},
        {"id": "test", "title": "Test", "depends_on": ["build"]},
    ],
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser


def _write(tmp_path: Path, data: object) -> str:
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_validate_accepts_a_good_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "validate", _write(tmp_path, _GOOD)])
    assert args.func(args) == 0
    assert "release" in capsys.readouterr().out


def test_validate_rejects_a_cycle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cyclic = {
        "name": "w",
        "steps": [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}],
    }
    parser = _parser()
    args = parser.parse_args(["workflow", "validate", _write(tmp_path, cyclic)])
    assert args.func(args) == 2
    assert "cycle" in capsys.readouterr().err


def test_validate_reports_a_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "validate", "/no/such/workflow.json"])
    assert args.func(args) == 2
    assert "could not read" in capsys.readouterr().err


def test_validate_reports_invalid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    parser = _parser()
    args = parser.parse_args(["workflow", "validate", str(path)])
    assert args.func(args) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_compile_human_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "compile", _write(tmp_path, _GOOD)])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "release/build [ci] <- (none)" in out
    assert "release/test <- release/build" in out


def test_compile_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "compile", "--json", _write(tmp_path, _GOOD)])
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["task_id"] == "release/build"
    assert payload[0]["task_class"] == "ci"
    assert payload[1]["depends_on"] == ["release/build"]


def test_compile_reports_a_malformed_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "compile", _write(tmp_path, {"name": "w"})])
    assert args.func(args) == 2
    assert "steps" in capsys.readouterr().err


def _write_named(tmp_path: Path, name: str, data: object) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_plan_routes_ready_tasks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wf = _write(tmp_path, _GOOD)
    status = _write_named(tmp_path, "status.json", {"release/build": "done"})
    agents = _write_named(tmp_path, "agents.json", {"alpha": ["ci"]})
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", wf, "--status", status, "--agents", agents])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "1 done" in out
    assert "release/test -> alpha" in out


def test_plan_json_with_no_files_uses_defaults(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", _write(tmp_path, _GOOD), "--json"])
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"]["ready"] == ["release/build"]
    assert payload["plan"] == []  # no agents available


def test_plan_reports_no_assignments(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", _write(tmp_path, _GOOD)])
    assert args.func(args) == 0
    assert "no assignments" in capsys.readouterr().out


def test_plan_rejects_a_non_object_status_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _write_named(tmp_path, "status.json", [1, 2])
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", _write(tmp_path, _GOOD), "--status", status])
    assert args.func(args) == 2
    assert "status file must be a JSON object" in capsys.readouterr().err


def test_plan_rejects_a_non_object_agents_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agents = _write_named(tmp_path, "agents.json", "nope")
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", _write(tmp_path, _GOOD), "--agents", agents])
    assert args.func(args) == 2
    assert "agents file must be a JSON object" in capsys.readouterr().err


def test_plan_rejects_agent_without_a_class_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agents = _write_named(tmp_path, "agents.json", {"alpha": "ci"})
    parser = _parser()
    args = parser.parse_args(["workflow", "plan", _write(tmp_path, _GOOD), "--agents", agents])
    assert args.func(args) == 2
    assert "list of task classes" in capsys.readouterr().err


# --- workflow run: gateway, rendering, and the live loop ---------------------


def test_snapshot_from_board_keeps_status_and_owner_and_skips_blank_ids() -> None:
    board = {
        "tasks": [
            {"task_id": "w/a", "status": "done", "suggested_owner": "alpha"},
            {"task_id": "w/b", "status": "open"},
            {"task_id": "  ", "status": "open"},
        ]
    }
    snapshot = _snapshot_from_board(board)
    assert snapshot.status == {"w/a": "done", "w/b": "open"}
    assert snapshot.suggested_owner == {"w/a": "alpha"}


class _FakeAgent:
    """A minimal stand-in exercising the gateway without a hub."""

    def __init__(self, boards: list[Any], *, board_payload: dict[str, Any] | None) -> None:
        self._boards = boards
        self._board_payload = board_payload
        self.posts: list[tuple[str, str, str, tuple[str, ...]]] = []
        self.assigns: list[tuple[str, str]] = []
        self.cancels: list[tuple[str, str]] = []

    async def post_task(
        self,
        task_id: str,
        *,
        title: str,
        description: str,
        depends_on: tuple[str, ...],
    ) -> None:
        self.posts.append((task_id, title, description, tuple(depends_on)))

    async def request_board(self) -> None:
        if self._board_payload is not None:
            self._boards.append(self._board_payload)

    async def update_ledger_task(
        self, task_id: str, *, suggested_owner: str | None = None, status: str | None = None
    ) -> None:
        if suggested_owner is not None:
            self.assigns.append((task_id, suggested_owner))
        if status is not None:
            self.cancels.append((task_id, status))


async def test_gateway_posts_reads_and_assigns() -> None:
    boards: list[Any] = []
    payload = {"tasks": [{"task_id": "w/a", "status": "open", "suggested_owner": ""}]}
    agent = _FakeAgent(boards, board_payload=payload)
    gateway = _AgentGateway(agent, boards)  # type: ignore[arg-type]

    tasks = compile_to_tasks(parse_workflow(_GOOD))
    await gateway.post_tasks(tasks)
    assert agent.posts[0][0] == "release/build"
    assert agent.posts[1][3] == ("release/build",)

    snapshot = await gateway.read_board()
    assert snapshot.status == {"w/a": "open"}

    await gateway.assign("w/a", "alpha")
    assert agent.assigns == [("w/a", "alpha")]

    await gateway.cancel("w/a")
    assert agent.cancels == [("w/a", "cancelled")]


async def test_gateway_read_board_returns_empty_when_no_snapshot_arrives() -> None:
    boards: list[Any] = []
    agent = _FakeAgent(boards, board_payload=None)
    gateway = _AgentGateway(agent, boards, attempts=2, poll=0.0)  # type: ignore[arg-type]
    snapshot = await gateway.read_board()
    assert snapshot.status == {}
    assert snapshot.suggested_owner == {}


def _empty_state() -> WorkflowState:
    return WorkflowState(done=(), in_flight=(), ready=(), blocked=())


def test_render_run_human_lists_assignments_and_retirements(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = RunResult(
        complete=True,
        timed_out=False,
        polls=2,
        assignments=(("w/a", "alpha"),),
        cancellations=("w/rollback",),
        state=_empty_state(),
    )
    _render_run(result, json_out=False)
    out = capsys.readouterr().out
    assert "workflow complete after 2 board reads" in out
    assert "w/a -> alpha" in out
    assert "retired (branch not taken):" in out
    assert "w/rollback" in out


def test_render_run_human_reports_no_assignments(capsys: pytest.CaptureFixture[str]) -> None:
    result = RunResult(
        complete=False,
        timed_out=True,
        polls=5,
        assignments=(),
        cancellations=(),
        state=_empty_state(),
    )
    _render_run(result, json_out=False)
    out = capsys.readouterr().out
    assert "incomplete (deadline reached)" in out
    assert "no assignments made" in out


def test_render_run_json(capsys: pytest.CaptureFixture[str]) -> None:
    result = RunResult(
        complete=True,
        timed_out=False,
        polls=1,
        assignments=(),
        cancellations=(),
        state=_empty_state(),
    )
    _render_run(result, json_out=True)
    assert json.loads(capsys.readouterr().out)["complete"] is True


class _NotReadyAgent:
    """An agent whose welcome never arrives — models an unreachable hub."""

    def __init__(self, name: str, callback: Any, *, uri: str, verbose: bool, token: Any) -> None:
        del name, callback, uri, verbose, token
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason: str | None = None

    async def connect(self) -> None:
        while True:
            await asyncio.sleep(0.05)

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return False


def test_run_command_reports_a_malformed_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "run", _write(tmp_path, {"name": "w"})])
    assert _cmd_run(args) == 2
    assert "steps" in capsys.readouterr().err


def test_run_command_reports_an_unreachable_hub(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _parser()
    args = parser.parse_args(["workflow", "run", _write(tmp_path, _GOOD)])
    assert _cmd_run(args, agent_factory=_NotReadyAgent) == 1  # type: ignore[arg-type]
    assert "WORKFLOW" in capsys.readouterr().out


async def _complete_owned_tasks(handle: Any, *, rounds: int = 60) -> None:
    """A worker that marks any advised task done, until every task is terminal."""
    agent = handle.agent
    terminal = {"done", "cancelled"}
    for _ in range(rounds):
        handle.recorder.messages.clear()
        await agent.request_board()
        message = await handle.recorder.wait_for(lambda m: m.get("type") == "board_snapshot")
        tasks = message.get("board", {}).get("tasks", [])
        if tasks and all(task.get("status") in terminal for task in tasks):
            return
        for task in tasks:
            if task.get("suggested_owner") and task.get("status") not in terminal:
                await agent.update_ledger_task(task["task_id"], status="done")
        await asyncio.sleep(0.05)


async def test_drive_run_completes_against_a_live_hub(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        worker = await connect_agent("WORKER", uri)
        parser = _parser()
        args = parser.parse_args(
            [
                "workflow",
                "run",
                "ignored.json",
                "--uri",
                uri,
                "--name",
                "DRIVER",
                "--poll-interval",
                "0.05",
                "--deadline",
                "10",
            ]
        )
        tasks = compile_to_tasks(parse_workflow(_GOOD))
        agents = {"alpha": frozenset({"ci"}), "beta": frozenset[str]()}
        try:
            code, _ = await asyncio.gather(
                _drive_run(args, tasks, agents),
                _complete_owned_tasks(worker),
            )
        finally:
            await close_agents(worker)
    assert code == 0
    assert "workflow complete" in capsys.readouterr().out


async def test_drive_run_reports_a_name_conflict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        blocker = await connect_agent("DRIVER", uri)
        parser = _parser()
        args = parser.parse_args(
            ["workflow", "run", "ignored.json", "--uri", uri, "--name", "DRIVER"]
        )
        tasks = compile_to_tasks(parse_workflow(_GOOD))
        try:
            code = await _drive_run(args, tasks, {})
        finally:
            await close_agents(blocker)
    assert code == 1
    assert "DRIVER" in capsys.readouterr().out


# --- contention (workflow-scoped yield advice) ---------------------------------------


def _seed_claims(db: Path, *claims: tuple[str, str, list[str]]) -> None:
    """Append live claims as (task_id, owner, paths) triples in one worktree."""
    store = EventStore(db)
    try:
        for task_id, owner, paths in claims:
            store.append(
                EventKind.CLAIM,
                {
                    "task_id": task_id,
                    "owner": owner,
                    "status": "claimed",
                    "paths": paths,
                    "worktree": "repo",
                },
            )
    finally:
        store.close()


def test_contention_reports_a_pair_involving_a_workflow_task(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_claims(
        db,
        ("release/build", "alice", ["src/build.py"]),
        ("hotfix", "bob", ["src/build.py"]),
    )
    parser = _parser()
    args = parser.parse_args(["workflow", "contention", _write(tmp_path, _GOOD), str(db)])
    assert args.func(args) == 1
    out = capsys.readouterr().out
    assert "1 overlapping live claim pair(s)" in out
    assert "release/build" in out
    assert "advisory only" in out


def test_contention_ignores_pairs_outside_the_workflow_and_notes_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_claims(
        db,
        ("stray-one", "alice", ["src/other.py"]),
        ("stray-two", "bob", ["src/other.py"]),
    )
    parser = _parser()
    args = parser.parse_args(["workflow", "contention", _write(tmp_path, _GOOD), str(db)])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "No live claims involving this workflow's tasks overlap" in out
    assert "1 other overlapping pair(s) do not involve this workflow" in out


def test_contention_quiet_log_prints_no_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_claims(db, ("release/build", "alice", ["src/build.py"]))
    parser = _parser()
    args = parser.parse_args(["workflow", "contention", _write(tmp_path, _GOOD), str(db)])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "No live claims involving this workflow's tasks overlap" in out
    assert "other overlapping" not in out


def test_contention_json_carries_only_the_scoped_pairs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_claims(
        db,
        ("release/build", "alice", ["src/build.py"]),
        ("hotfix", "bob", ["src/build.py"]),
        ("stray-one", "carol", ["src/other.py"]),
        ("stray-two", "dave", ["src/other.py"]),
    )
    parser = _parser()
    args = parser.parse_args(
        ["workflow", "contention", _write(tmp_path, _GOOD), str(db), "--json"]
    )
    assert args.func(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["holder"]["task_id"] == "release/build"
    assert payload[0]["yielder"]["task_id"] == "hotfix"


def test_contention_json_scoped_empty_is_an_empty_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_claims(
        db,
        ("stray-one", "alice", ["src/other.py"]),
        ("stray-two", "bob", ["src/other.py"]),
    )
    parser = _parser()
    args = parser.parse_args(
        ["workflow", "contention", _write(tmp_path, _GOOD), str(db), "--json"]
    )
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_contention_rejects_a_malformed_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_claims(db, ("release/build", "alice", ["src/build.py"]))
    parser = _parser()
    args = parser.parse_args(["workflow", "contention", _write(tmp_path, {"name": "w"}), str(db)])
    assert args.func(args) == 2
    assert "steps" in capsys.readouterr().err


def test_contention_missing_store_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = _parser()
    args = parser.parse_args(
        ["workflow", "contention", _write(tmp_path, _GOOD), str(tmp_path / "absent.db")]
    )
    assert args.func(args) == 2
    assert "missing event store" in capsys.readouterr().err


def test_contention_honours_the_node_ceiling(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_claims(
        db,
        ("release/build", "alice", ["src/build.py"]),
        ("hotfix", "bob", ["src/build.py"]),
    )
    parser = _parser()
    args = parser.parse_args(
        ["workflow", "contention", _write(tmp_path, _GOOD), str(db), "--max-nodes", "1"]
    )
    assert args.func(args) == 2
    assert "would exceed" in capsys.readouterr().err
