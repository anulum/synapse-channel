# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for client-side git-scoped claims

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

from synapse_channel.core.protocol import MessageType
from synapse_channel.git.gitclaim import (
    AgentFactory,
    GitError,
    _default_git_runner,
    resolve_branch,
    resolve_repo,
    run_git_claim,
)


class FakeAgent:
    """A SynapseAgent stand-in that records claims and exposes its callback."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str = "ws://test",
        verbose: bool = False,
        token: str | None = None,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.token = token
        self.running = True
        self.ready = True
        self.claims: list[tuple[str, list[str], dict[str, Any] | None]] = []
        self.worktrees: list[str] = []

    async def connect(self) -> None:
        return None

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self.ready

    async def claim(
        self,
        task_id: str,
        *,
        worktree: str = "",
        paths: Any = (),
        git: dict[str, Any] | None = None,
        **_kw: Any,
    ) -> None:
        self.claims.append((task_id, list(paths), git))
        self.worktrees.append(worktree)


def make_factory(*, ready: bool = True) -> tuple[AgentFactory, list[FakeAgent]]:
    """Return an agent factory plus the list it appends each created agent to."""
    created: list[FakeAgent] = []

    def factory(name: str, callback: Any, **kwargs: Any) -> FakeAgent:
        agent = FakeAgent(name, callback, **kwargs)
        agent.ready = ready
        created.append(agent)
        return agent

    return cast(AgentFactory, factory), created


async def _await_claim_sent(created: list[FakeAgent]) -> FakeAgent:
    """Spin until the flow has created an agent and sent its claim."""

    for _ in range(100):
        if created and created[0].claims:
            return created[0]
        await asyncio.sleep(0)
    raise AssertionError("claim was never sent")


# -- _default_git_runner ------------------------------------------------------


def test_default_git_runner_returns_stripped_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        stdout = "feature/x\n"

    def fake_run(args: list[str], **_kw: Any) -> Result:
        assert args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]
        return Result()

    monkeypatch.setattr("synapse_channel.git.gitclaim.subprocess.run", fake_run)
    assert _default_git_runner(["rev-parse", "--abbrev-ref", "HEAD"]) == "feature/x"


def test_default_git_runner_missing_git(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **_kw: Any) -> Any:
        raise FileNotFoundError

    monkeypatch.setattr("synapse_channel.git.gitclaim.subprocess.run", fake_run)
    with pytest.raises(GitError, match="not installed"):
        _default_git_runner(["status"])


def test_default_git_runner_nonzero_uses_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **_kw: Any) -> Any:
        raise subprocess.CalledProcessError(1, args, stderr="fatal: not a git repository")

    monkeypatch.setattr("synapse_channel.git.gitclaim.subprocess.run", fake_run)
    with pytest.raises(GitError, match="not a git repository"):
        _default_git_runner(["status"])


def test_default_git_runner_nonzero_without_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **_kw: Any) -> Any:
        raise subprocess.CalledProcessError(1, args, stderr="")

    monkeypatch.setattr("synapse_channel.git.gitclaim.subprocess.run", fake_run)
    with pytest.raises(GitError, match="exited non-zero"):
        _default_git_runner(["status"])


# -- resolve_branch -----------------------------------------------------------


def test_resolve_branch_calls_rev_parse() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "main"

    assert resolve_branch(runner=runner) == "main"
    assert captured == [["rev-parse", "--abbrev-ref", "HEAD"]]


# -- resolve_repo -------------------------------------------------------------


def test_resolve_repo_calls_show_toplevel() -> None:
    captured: list[list[str]] = []

    def runner(args: list[str]) -> str:
        captured.append(args)
        return "/home/me/work/repo"

    assert resolve_repo(runner=runner) == "/home/me/work/repo"
    assert captured == [["rev-parse", "--show-toplevel"]]


def _branch_then_repo(branch: str, repo: str) -> Callable[[list[str]], str]:
    """A git runner that answers branch and top-level queries distinctly."""

    def runner(args: list[str]) -> str:
        if args == ["rev-parse", "--show-toplevel"]:
            return repo
        return branch

    return runner


# -- run_git_claim ------------------------------------------------------------


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
