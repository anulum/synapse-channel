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
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import git_repo, run_cli
from cli_lock_release_helpers import release_reply_proxy
from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_locking
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.git_claim import resolve_mcp_git_claim_scope


@pytest.mark.parametrize(
    ("paths", "accepted"),
    [(["a.py,b.py"], False), (["a.py", "b.py"], True), (["odd,name.txt", "b.py"], True)],
)
async def test_packaged_lock_path_arguments(
    tmp_path: Path, paths: list[str], accepted: bool
) -> None:
    repo = git_repo(tmp_path / "repository")
    (repo / "odd,name.txt").touch()
    async with running_hub(SynapseHub()) as (hub, uri):
        argv = [
            "lock",
            "--uri",
            uri,
            "--name",
            "path-input",
            "--ready-timeout",
            "2",
        ]
        for path in paths:
            argv.extend(["--paths", path])
        argv.extend(["path-input-task", "--", sys.executable, "-c", "print('command-executed')"])
        result = await asyncio.to_thread(run_cli, *argv, cwd=repo, timeout=10)
        assert result.returncode == (0 if accepted else 2), result.stdout + result.stderr
        assert ("command-executed" in result.stdout) is accepted
        assert "path-input-task" not in hub.state.claims
        if not accepted:
            assert "lock: --paths takes one path per flag" in result.stderr
            assert "Repeat --paths" in result.stderr


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
            assert claim.worktree == Path.cwd().resolve().as_posix()
            assert claim.paths == ("src",)
            assert claim.path_identity is not None
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


async def test_lock_path_scope_contends_with_git_claim_in_same_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ordinary CLI lock cannot bypass an overlapping Git-aware claim."""
    repo = git_repo(tmp_path / "repo")
    source = repo / "src" / "owned.py"
    source.parent.mkdir()
    source.write_text("owned = True\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    scope = resolve_mcp_git_claim_scope(
        ["src/owned.py"],
        base="main",
        auto_release_on="manual",
    )

    async with running_hub(SynapseHub()) as (_hub, uri):
        holder = await connect_agent("git-owner", uri)
        await holder.agent.claim(
            "GIT-OWNER",
            worktree=scope.worktree,
            paths=list(scope.paths),
            path_identity=scope.path_identity,
            git=scope.git,
        )
        await holder.recorder.wait_for(
            lambda message: (
                message.get("type") == MessageType.CLAIM_GRANTED
                and message.get("task_id") == "GIT-OWNER"
            )
        )

        async def never_runs(_command: list[str]) -> int:
            raise AssertionError("overlapping ordinary lock must not run")

        try:
            code = await cli_locking._lock(
                uri=uri,
                name="plain-owner",
                task_id="PLAIN-OWNER",
                command=["true"],
                paths=["src/owned.py"],
                wait_timeout=0.0,
                runner=never_runs,
            )
        finally:
            await close_agents(holder)

    assert code == 1
    assert "file scope conflicts" in capsys.readouterr().out


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
        release_timeout=None,
    )
    assert cli_locking._cmd_lock(ns) == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_lock_retries_after_a_denial_and_wins_the_second_round() -> None:
    """An actual denied first attempt retries only after the real holder releases."""
    async with running_hub() as (hub, uri):
        holder = await connect_agent("first-owner", uri)
        try:
            await holder.agent.claim("g", worktree="g")
            await holder.recorder.wait_for(
                lambda frame: frame.get("type") == MessageType.CLAIM_GRANTED
            )
            async with release_reply_proxy(uri, drop_confirmation=False) as (proxy, fault):
                fault["active"] = False
                operation = asyncio.create_task(
                    asyncio.to_thread(
                        run_cli,
                        "lock",
                        "g",
                        "--name",
                        "second-owner",
                        "--wait-timeout",
                        "5",
                        "--",
                        sys.executable,
                        "-c",
                        "print('command-ran')",
                        uri=proxy,
                    )
                )
                deadline = asyncio.get_running_loop().time() + 5
                while not fault["claim_denied"]:
                    assert not operation.done()
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.01)
                assert hub.state.claims["g"].owner == "first-owner"
                await holder.agent.release("g")
                await holder.recorder.wait_for(
                    lambda frame: frame.get("type") == MessageType.RELEASE_GRANTED
                )
                result = await asyncio.wait_for(operation, 10)
                assert result.returncode == 0, result.output
                assert result.stdout.strip() == "command-ran"
                assert "g" not in hub.state.claims
        finally:
            await close_agents(holder)


async def test_lock_exposes_cleanup_failure_separately_from_child_exit() -> None:
    """Real loss of both confirmation paths cannot silently return the child's zero."""
    async with running_hub() as (_hub, uri):
        async with release_reply_proxy(uri, drop_confirmation=True) as (proxy, _fault):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "g",
                "--name",
                "owner",
                "--release-timeout",
                "0.1",
                "--",
                sys.executable,
                "-c",
                "pass",
                uri=proxy,
            )
            assert result.returncode == 3, result.output
            assert "lock: release unknown" in result.stderr
            assert "child exit=0" in result.stderr
            assert "Do not replay release" in result.stderr


async def test_lock_teardown_waits_for_the_release_confirmation(tmp_path: Path) -> None:
    """A delayed real grant keeps the CLI alive until actual confirmation arrives."""
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            async with release_reply_proxy(uri, drop_confirmation=False, grant_delay=0.2) as (
                proxy,
                fault,
            ):
                fault["active"] = False
                operation = asyncio.create_task(
                    asyncio.to_thread(
                        run_cli,
                        "lock",
                        "g",
                        "--name",
                        "owner",
                        "--",
                        sys.executable,
                        "-c",
                        "pass",
                        uri=proxy,
                    )
                )
                deadline = asyncio.get_running_loop().time() + 5
                while not fault["release_seen"]:
                    assert not operation.done()
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.01)
                assert not operation.done(), "CLI must await the delayed keyed confirmation"
                result = await asyncio.wait_for(operation, 10)
                assert result.returncode == 0, result.output
                assert "g" not in hub.state.claims
                assert any(row.kind == "release" for row in journal.iter_events())
    finally:
        journal.close()


async def test_lock_teardown_wait_is_bounded_without_a_confirmation() -> None:
    """A real volatile hub and lost grant finish boundedly with explicit uncertainty."""
    async with running_hub() as (_hub, uri):
        async with release_reply_proxy(uri, drop_confirmation=False) as (proxy, _fault):
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    run_cli,
                    "lock",
                    "g",
                    "--name",
                    "owner",
                    "--release-timeout",
                    "0.1",
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                    uri=proxy,
                ),
                10,
            )
            assert result.returncode == 3, result.output
            assert "lock: release unknown" in result.stderr


def test_parser_lock_release_timeout() -> None:
    args = cli.build_parser().parse_args(["lock", "g", "--release-timeout", "7.5", "--", "true"])
    assert args.release_timeout == 7.5
    default = cli.build_parser().parse_args(["lock", "g", "--", "true"])
    assert default.release_timeout is None


async def test_release_timeout_bounds_the_teardown_wait(tmp_path: Path) -> None:
    """The explicit real deadline leads to exact recovery of one delayed operation."""
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (_hub, uri):
            async with release_reply_proxy(uri, drop_confirmation=False) as (proxy, _fault):
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        run_cli,
                        "lock",
                        "g",
                        "--name",
                        "owner",
                        "--release-timeout",
                        "1",
                        "--",
                        sys.executable,
                        "-c",
                        "pass",
                        uri=proxy,
                    ),
                    10,
                )
                assert result.returncode == 0, result.output
                assert len([row for row in journal.iter_events() if row.kind == "release"]) == 1
    finally:
        journal.close()
