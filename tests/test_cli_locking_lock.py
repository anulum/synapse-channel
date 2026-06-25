# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the lease-serialising CLI commands (lock/release)

from __future__ import annotations

import argparse

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_locking
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType


def test_parser_lock() -> None:
    args = cli.build_parser().parse_args(["lock", "q:git", "--name", "X", "--", "git", "push"])
    assert args.task_id == "q:git"
    assert args.command == ["git", "push"]
    assert args.func is cli_locking._cmd_lock


async def test_run_subprocess_returns_exit_code() -> None:
    assert await cli_locking._run_subprocess(["true"]) == 0
    assert await cli_locking._run_subprocess(["false"]) == 1


async def test_lock_runs_command_holding_lease() -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        ran: list[list[str]] = []

        async def runner(command: list[str]) -> int:
            ran.append(command)
            claim = hub.state.claims["g"]
            assert claim.owner == "X"
            assert claim.worktree == ""
            assert claim.paths == ("src",)
            return 0

        code = await cli_locking._lock(
            uri=uri,
            name="X",
            task_id="g",
            command=["echo", "hi"],
            paths=["src"],
            wait_timeout=5.0,
            runner=runner,
        )

    assert code == 0
    assert ran == [["echo", "hi"]]
    assert "g" not in hub.state.claims


async def test_lock_keyless_namespaces_worktree_to_task_id() -> None:
    async with running_hub(SynapseHub()) as (hub, uri):

        async def runner(_command: list[str]) -> int:
            assert hub.state.claims["repo:git"].worktree == "repo:git"
            return 0

        code = await cli_locking._lock(
            uri=uri,
            name="X",
            task_id="repo:git",
            command=["git", "push"],
            paths=[],
            wait_timeout=5.0,
            runner=runner,
        )

    assert code == 0


async def test_lock_fails_fast_when_held(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        holder = await connect_agent("api-dev", uri)
        await holder.agent.claim("g", worktree="g", paths=[])
        await holder.recorder.wait_for(
            lambda message: (
                message.get("type") == MessageType.CLAIM_GRANTED and message.get("task_id") == "g"
            )
        )

        async def runner(_command: list[str]) -> int:
            raise AssertionError("command must not run without the lease")

        try:
            code = await cli_locking._lock(
                uri=uri,
                name="X",
                task_id="g",
                command=["x"],
                paths=[],
                wait_timeout=0.0,
                runner=runner,
                attempts=2,
            )
        finally:
            await close_agents(holder)

    assert code == 1
    assert "Could not acquire lock 'g'" in capsys.readouterr().out


async def test_lock_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_locking._lock(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="X",
        task_id="g",
        command=["x"],
        paths=[],
        wait_timeout=1.0,
        ready_timeout=0.1,
        attempts=1,
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_lock_times_out_while_held(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        holder = await connect_agent("api-dev", uri)
        await holder.agent.claim("g", worktree="g", paths=[])
        await holder.recorder.wait_for(
            lambda message: (
                message.get("type") == MessageType.CLAIM_GRANTED and message.get("task_id") == "g"
            )
        )
        try:
            code = await cli_locking._lock(
                uri=uri,
                name="X",
                task_id="g",
                command=["x"],
                paths=[],
                wait_timeout=0.05,
                retry_interval=0.01,
                attempts=1,
            )
        finally:
            await close_agents(holder)

    assert code == 1
    assert "Could not acquire lock 'g'" in capsys.readouterr().out


def test_cmd_lock_dispatches_real_command(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="X",
        task_id="g",
        command=["x"],
        paths=None,
        wait_timeout=0.0,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_locking._cmd_lock(ns) == 1
    assert "Could not reach hub" in capsys.readouterr().out
