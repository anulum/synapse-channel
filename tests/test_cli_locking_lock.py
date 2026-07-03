# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the lease-serialising CLI commands (lock/release)

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any, cast

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_locking
from synapse_channel.cli_locking import AgentFactory
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


async def test_lock_surfaces_name_conflict_instead_of_timing_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def never_runs(command: list[str]) -> int:
        raise AssertionError("command must not run when the lock is never held")

    async with running_hub(SynapseHub()) as (_hub, uri):
        holder = await connect_agent("X", uri)
        try:
            code = await cli_locking._lock(
                uri=uri,
                name="X",
                task_id="g:git",
                command=["echo", "hi"],
                paths=[],
                wait_timeout=0.0,
                runner=never_runs,
            )
        finally:
            await close_agents(holder)

    assert code == 1
    out = capsys.readouterr().out
    assert "already online" in out
    assert "code 4009" in out
    assert "timed out" not in out


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


# --- scripted-agent paths a live hub cannot exercise deterministically ---


class _ScriptedLockAgent:
    """Feeds a scripted claim verdict per ``claim()`` call, without a hub."""

    def __init__(
        self,
        name: str,
        callback: Any,
        **_kwargs: Any,
    ) -> None:
        self.name = name
        self.callback = callback
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""
        self.claim_calls = 0
        self.release_error: Exception | None = None

    async def connect(self) -> None:
        await asyncio.sleep(3600)

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return True

    async def claim(self, task_id: str, **_kwargs: Any) -> None:
        self.claim_calls += 1
        if self.claim_calls == 1:
            # foreign frames the collector must ignore before the real verdict
            await self.callback({"type": MessageType.CLAIM_GRANTED, "task_id": "other"})
            await self.callback(
                {"type": MessageType.CLAIM_GRANTED, "task_id": task_id, "owner": "someone-else"}
            )
            await self.callback(
                {"type": MessageType.CLAIM_DENIED, "task_id": task_id, "payload": "held"}
            )
            return
        await self.callback(
            {"type": MessageType.CLAIM_GRANTED, "task_id": task_id, "owner": self.name}
        )

    async def release(self, task_id: str, **_kwargs: Any) -> None:
        if self.release_error is not None:
            raise self.release_error
        # mirror the real hub: the release is confirmed back to its owner
        await self.callback(
            {"type": MessageType.RELEASE_GRANTED, "task_id": task_id, "owner": self.name}
        )


async def test_lock_retries_after_a_denial_and_wins_the_second_round() -> None:
    """A denied first claim sleeps the retry interval and re-claims — and a
    grant for another task or another owner never counts as ours."""
    ran: list[list[str]] = []

    async def runner(command: list[str]) -> int:
        ran.append(command)
        return 0

    code = await cli_locking._lock(
        uri="ws://unused",
        name="X",
        task_id="g",
        command=["c"],
        paths=[],
        wait_timeout=30.0,
        retry_interval=0.01,
        poll_interval=0.001,
        agent_factory=cast("AgentFactory", _ScriptedLockAgent),
        runner=runner,
    )
    assert code == 0
    assert ran == [["c"]]


async def test_lock_logs_a_teardown_release_failure_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A release that fails on teardown leaves a debug trace, never masks the result."""

    class _GrantThenFailRelease(_ScriptedLockAgent):
        def __init__(self, name: str, callback: Any, **kwargs: Any) -> None:
            super().__init__(name, callback, **kwargs)
            self.claim_calls = 1  # grant immediately on the first claim
            self.release_error = RuntimeError("release refused during teardown")

    async def runner(_command: list[str]) -> int:
        return 0

    with caplog.at_level(logging.DEBUG, logger="synapse.lock"):
        code = await cli_locking._lock(
            uri="ws://unused",
            name="X",
            task_id="g",
            command=["c"],
            paths=[],
            wait_timeout=1.0,
            poll_interval=0.001,
            agent_factory=cast("AgentFactory", _GrantThenFailRelease),
            runner=runner,
        )
    assert code == 0
    assert "best-effort release of 'g' failed on teardown" in caplog.text


async def test_lock_teardown_waits_for_the_release_confirmation() -> None:
    """The process exits only after the hub confirms the release — the durable
    log already carries it, so a follow-up step never races the teardown."""
    seen: dict[str, float] = {}

    class _SlowConfirmRelease(_ScriptedLockAgent):
        def __init__(self, name: str, callback: Any, **kwargs: Any) -> None:
            super().__init__(name, callback, **kwargs)
            self.claim_calls = 1  # grant immediately on the first claim

        async def release(self, task_id: str, **_kwargs: Any) -> None:
            seen["released_at"] = asyncio.get_event_loop().time()

            async def confirm_later() -> None:
                await asyncio.sleep(0.05)
                await self.callback(
                    {
                        "type": MessageType.RELEASE_GRANTED,
                        "task_id": task_id,
                        "owner": self.name,
                    }
                )
                seen["confirmed_at"] = asyncio.get_event_loop().time()

            asyncio.get_event_loop().create_task(confirm_later())

    async def runner(_command: list[str]) -> int:
        return 0

    code = await cli_locking._lock(
        uri="ws://unused",
        name="X",
        task_id="g",
        command=["c"],
        paths=[],
        wait_timeout=1.0,
        poll_interval=0.01,
        agent_factory=cast("AgentFactory", _SlowConfirmRelease),
        runner=runner,
    )

    assert code == 0
    assert "confirmed_at" in seen  # teardown waited through the late confirmation


async def test_lock_teardown_wait_is_bounded_without_a_confirmation() -> None:
    """A hub that never confirms costs only the bounded wait, never a hang."""

    class _NeverConfirmRelease(_ScriptedLockAgent):
        def __init__(self, name: str, callback: Any, **kwargs: Any) -> None:
            super().__init__(name, callback, **kwargs)
            self.claim_calls = 1

        async def release(self, task_id: str, **_kwargs: Any) -> None:
            del task_id  # fire-and-forget with no confirmation ever arriving

    async def runner(_command: list[str]) -> int:
        return 0

    code = await asyncio.wait_for(
        cli_locking._lock(
            uri="ws://unused",
            name="X",
            task_id="g",
            command=["c"],
            paths=[],
            wait_timeout=1.0,
            attempts=3,
            poll_interval=0.01,
            agent_factory=cast("AgentFactory", _NeverConfirmRelease),
            runner=runner,
        ),
        timeout=5.0,
    )

    assert code == 0
