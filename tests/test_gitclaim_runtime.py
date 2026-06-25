# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for client-side git-scoped claims

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from gitclaim_helpers import FakeAgent, _await_claim_sent, make_factory
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.gitclaim import (
    GitError,
    run_git_claim,
)
from synapse_channel.git.githook import install_hooks


def _branch_then_repo(branch: str, repo: str) -> Callable[[list[str]], str]:
    """A git runner that answers branch and top-level queries distinctly."""

    def runner(args: list[str]) -> str:
        if args == ["rev-parse", "--show-toplevel"]:
            return repo
        return branch

    return runner


async def test_run_git_claim_granted_sends_git_context() -> None:
    factory, created = make_factory()

    task = asyncio.create_task(
        run_git_claim(
            uri="ws://t",
            name="me",
            task_id="T1",
            paths=["src/a.py"],
            base="develop",
            auto_release_on="commit",
            agent_factory=factory,
            runner=lambda _a: "feature/x",
        )
    )
    agent = await _await_claim_sent(created)
    task_id, paths, git = agent.claims[0]
    assert task_id == "T1"
    assert paths == ["src/a.py"]
    assert git == {"branch": "feature/x", "base": "develop", "auto_release_on": "commit"}
    await agent.callback({"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"})
    assert await task == 0


def _branch_repo_hooks(branch: str, repo: str, hooks: str) -> Callable[[list[str]], str]:
    """A git runner answering branch, top-level, and hooks-dir queries distinctly."""

    def runner(args: list[str]) -> str:
        if args == ["rev-parse", "--show-toplevel"]:
            return repo
        if args == ["rev-parse", "--git-path", "hooks"]:
            return hooks
        return branch

    return runner


async def _grant(task: asyncio.Task[int], created: list[FakeAgent]) -> int:
    agent = await _await_claim_sent(created)
    await agent.callback({"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"})
    return await task


async def test_run_git_claim_warns_when_auto_release_hook_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    factory, created = make_factory()
    task = asyncio.create_task(
        run_git_claim(
            uri="ws://t",
            name="me",
            task_id="T1",
            paths=["src"],
            auto_release_on="merge",
            agent_factory=factory,
            runner=_branch_repo_hooks("feature/x", "/repo", str(tmp_path)),
        )
    )
    assert await _grant(task, created) == 0
    out = capsys.readouterr().out
    assert "will NOT fire" in out and "synapse git-hook" in out
    assert "synapse release T1 --name me" in out  # the manual escape hatch


async def test_run_git_claim_silent_when_hook_is_installed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_hooks(uri="ws://t", name="me", hooks_dir=tmp_path)  # writes post-merge
    factory, created = make_factory()
    task = asyncio.create_task(
        run_git_claim(
            uri="ws://t",
            name="me",
            task_id="T1",
            paths=["src"],
            auto_release_on="merge",
            agent_factory=factory,
            runner=_branch_repo_hooks("feature/x", "/repo", str(tmp_path)),
        )
    )
    assert await _grant(task, created) == 0
    assert "will NOT fire" not in capsys.readouterr().out


async def test_run_git_claim_no_warning_for_manual_auto_release(
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory, created = make_factory()
    task = asyncio.create_task(
        run_git_claim(
            uri="ws://t",
            name="me",
            task_id="T1",
            paths=["src"],
            auto_release_on="manual",
            agent_factory=factory,
            runner=lambda _a: "feature/x",
        )
    )
    assert await _grant(task, created) == 0
    assert "will NOT fire" not in capsys.readouterr().out


async def test_run_git_claim_scopes_worktree_to_repo() -> None:
    factory, created = make_factory()

    task = asyncio.create_task(
        run_git_claim(
            uri="ws://t",
            name="me",
            task_id="T1",
            paths=["src/a.py"],
            agent_factory=factory,
            runner=_branch_then_repo("feature/x", "/home/me/work/repo-a"),
        )
    )
    agent = await _await_claim_sent(created)
    # The claim is isolated to the resolved repository root, so a same-named path
    # in a different repository can never contend with it.
    assert agent.worktrees[0] == "/home/me/work/repo-a"
    await agent.callback({"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"})
    assert await task == 0


async def test_run_git_claim_git_error_on_repo_resolution() -> None:
    factory, _created = make_factory()

    def runner(args: list[str]) -> str:
        if args == ["rev-parse", "--show-toplevel"]:
            raise GitError("not inside a work tree")
        return "main"

    rc = await run_git_claim(
        uri="ws://t",
        name="me",
        task_id="T1",
        paths=[],
        agent_factory=factory,
        runner=runner,
    )
    assert rc == 1


async def test_run_git_claim_denied() -> None:
    factory, created = make_factory()

    task = asyncio.create_task(
        run_git_claim(
            uri="ws://t",
            name="me",
            task_id="T1",
            paths=[],
            agent_factory=factory,
            runner=lambda _a: "main",
        )
    )
    agent = await _await_claim_sent(created)
    await agent.callback(
        {"type": MessageType.CLAIM_DENIED, "task_id": "T1", "payload": "held by ALPHA"}
    )
    assert await task == 1


async def test_run_git_claim_ignores_noise_then_grants() -> None:
    factory, created = make_factory()

    task = asyncio.create_task(
        run_git_claim(
            uri="ws://t",
            name="me",
            task_id="T1",
            paths=[],
            agent_factory=factory,
            runner=lambda _a: "main",
        )
    )
    agent = await _await_claim_sent(created)
    # A grant for another task and a grant addressed to another owner are ignored.
    await agent.callback({"type": MessageType.CLAIM_GRANTED, "task_id": "OTHER", "owner": "me"})
    await agent.callback({"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "ELSE"})
    await agent.callback({"type": MessageType.CLAIM_GRANTED, "task_id": "T1", "owner": "me"})
    assert await task == 0


async def test_run_git_claim_unreachable_hub() -> None:
    factory, _created = make_factory(ready=False)
    rc = await run_git_claim(
        uri="ws://t",
        name="me",
        task_id="T1",
        paths=[],
        agent_factory=factory,
        runner=lambda _a: "main",
    )
    assert rc == 1


async def test_run_git_claim_git_error() -> None:
    factory, _created = make_factory()

    def bad_runner(_args: list[str]) -> str:
        raise GitError("not a git repository")

    rc = await run_git_claim(
        uri="ws://t",
        name="me",
        task_id="T1",
        paths=[],
        agent_factory=factory,
        runner=bad_runner,
    )
    assert rc == 1


async def test_run_git_claim_no_response(monkeypatch: pytest.MonkeyPatch) -> None:
    # With sleep elided, the poll loop exhausts without a reply and reports denial.
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.git.gitclaim.asyncio.sleep", no_sleep)
    factory, _created = make_factory()
    rc = await run_git_claim(
        uri="ws://t",
        name="me",
        task_id="T1",
        paths=[],
        agent_factory=factory,
        runner=lambda _a: "main",
    )
    assert rc == 1
