# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for git-hook auto-release of branch-scoped claims

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import git_repo, git_run
from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.git import githook
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.githook import (
    _paths_overlap,
    run_git_release,
)
from synapse_channel.git.path_identity import resolve_claim_scope_identity

GitPayload = dict[str, str]
ClaimSpec = tuple[str, list[str], GitPayload | None]


def _release_scope(
    _root: Path,
    paths: list[str],
    *,
    runner: object,
) -> tuple[Path, tuple[str, ...], ClaimScopeIdentity]:
    """Return one deterministic canonical scope for hook orchestration tests."""
    del runner
    displays = tuple(paths)
    return (
        Path("/repo"),
        displays,
        ClaimScopeIdentity(
            worktree_path="/repo",
            case_sensitive=True,
            paths=tuple(CanonicalPathIdentity(path, path) for path in displays),
        ),
    )


def _release_runner(
    changed: str,
    *,
    root: str = "/repo",
    branch: str = "x",
) -> Callable[[list[str]], str]:
    def run(args: list[str]) -> str:
        if args in (
            ["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            ["diff", "--name-only", "ORIG_HEAD", "HEAD"],
        ):
            return changed
        if args == ["rev-parse", "--show-toplevel"]:
            return root
        if args == ["symbolic-ref", "--quiet", "--short", "HEAD"]:
            return branch
        raise AssertionError(f"unexpected git args: {args!r}")

    return run


def test_release_overlap_does_not_trust_historical_object_identity() -> None:
    claim_identity = ClaimScopeIdentity(
        worktree_path="/repo",
        worktree_object_id="root:1",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("owned.py", "owned.py", "1:2"),),
    )
    changed_identity = ClaimScopeIdentity(
        worktree_path="/repo",
        worktree_object_id="root:1",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity("alias.py", "alias.py", "1:2"),),
    )

    assert not _paths_overlap(
        ["owned.py"],
        ["alias.py"],
        claim_identity,
        changed_identity,
    )


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 3.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("condition did not become true")


async def _claim_many(uri: str, owner: str, claims: list[ClaimSpec]) -> None:
    handle = await connect_agent(owner, uri)
    try:
        for task_id, paths, git in claims:
            await handle.agent.claim(task_id, worktree="/repo", paths=paths, git=git)

            def saw_claim_granted(message: dict[str, Any], expected: str = task_id) -> bool:
                return message.get("type") == "claim_granted" and message.get("task_id") == expected

            await handle.recorder.wait_for(saw_claim_granted)
    finally:
        await close_agents(handle)


async def test_run_git_release_releases_matching_claim(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(githook, "resolve_claim_scope_identity", _release_scope)
    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "me",
            [("T1", ["src/a.py"], {"branch": "x", "base": "main", "auto_release_on": "commit"})],
        )

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=_release_runner("src/a.py\n"),
        )

        assert rc == 0
        await _wait_until(lambda: "T1" not in hub.state.claims)
    assert "released on commit: T1" in capsys.readouterr().out


async def test_run_git_release_skips_non_matching_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(githook, "resolve_claim_scope_identity", _release_scope)
    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "other",
            [
                (
                    "T1",
                    ["src/a.py"],
                    {"branch": "x", "base": "main", "auto_release_on": "commit"},
                )
            ],
        )
        await _claim_many(
            uri,
            "me",
            [
                ("T2", ["src/b.py"], None),
                ("T3", ["src/c.py"], {"branch": "x", "base": "main", "auto_release_on": "merge"}),
                ("T4", ["docs/x"], {"branch": "x", "base": "main", "auto_release_on": "commit"}),
                (
                    "T5",
                    ["src/target.py"],
                    {"branch": "x", "base": "main", "auto_release_on": "commit"},
                ),
            ],
        )

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=_release_runner("src/target.py\n"),
        )

        assert rc == 0
        await _wait_until(lambda: "T5" not in hub.state.claims)
        assert set(hub.state.claims) == {"T1", "T2", "T3", "T4"}


async def test_run_git_release_unreachable_hub_never_blocks() -> None:
    port = _free_port()
    rc = await run_git_release(
        uri=f"ws://localhost:{port}",
        name="me",
        trigger="commit",
        runner=_release_runner("src/a.py\n"),
        ready_timeout=0.1,
    )
    assert rc == 0


async def test_run_git_release_git_error_returns_one() -> None:
    def bad_runner(_args: list[str]) -> str:
        raise GitError("not a git repository")

    rc = await run_git_release(
        uri="ws://localhost:9", name="me", trigger="commit", runner=bad_runner, ready_timeout=0.1
    )
    assert rc == 1


async def test_run_git_release_without_active_claims() -> None:
    async with running_hub() as (hub, uri):
        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=_release_runner("src/a.py\n"),
        )

        assert rc == 0
        assert not hub.state.claims


async def test_run_git_release_releases_whole_worktree_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(githook, "resolve_claim_scope_identity", _release_scope)
    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "me",
            [("T1", [], {"branch": "x", "base": "main", "auto_release_on": "commit"})],
        )

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=_release_runner("src/a.py\n"),
        )

        assert rc == 0
        await _wait_until(lambda: "T1" not in hub.state.claims)


async def test_run_git_release_keeps_same_owner_claim_in_other_worktree(tmp_path: Path) -> None:
    """Canonical release matching cannot free an identical path in another repository."""
    repo = git_repo(tmp_path / "repo")
    other = git_repo(tmp_path / "other")
    for root in (repo, other):
        (root / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
        git_run(root, "add", "owned.py")
        git_run(root, "commit", "-q", "-m", "owned fixture")
    _, paths, identity = resolve_claim_scope_identity(repo, ("owned.py",))
    _, other_paths, other_identity = resolve_claim_scope_identity(other, ("owned.py",))
    # Claims must match the attached branch of the firing worktree (release_context).
    branch = subprocess.run(  # noqa: S603, S607 - fixed git, test fixture paths
        ["git", "-C", str(repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git = {"branch": branch, "base": "main", "auto_release_on": "commit"}

    async with running_hub() as (hub, uri):
        handle = await connect_agent("me", uri)
        try:
            await handle.agent.claim(
                "HERE",
                worktree=str(repo),
                paths=paths,
                path_identity=identity.as_dict(),
                git=git,
            )
            await handle.agent.claim(
                "AWAY",
                worktree=str(other),
                paths=other_paths,
                path_identity=other_identity.as_dict(),
                git=git,
            )
            await handle.recorder.wait_for(
                lambda message: (
                    message.get("type") == "claim_granted" and message.get("task_id") == "AWAY"
                )
            )
        finally:
            await close_agents(handle)

        def runner(args: list[str]) -> str:
            command = ["git", *args] if "-C" in args else ["git", "-C", str(repo), *args]
            return subprocess.run(  # noqa: S603, S607 - fixed git, test fixture paths
                command,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        assert await run_git_release(uri=uri, name="me", trigger="commit", runner=runner) == 0
        await _wait_until(lambda: "HERE" not in hub.state.claims)
        assert "AWAY" in hub.state.claims


class _SilentReleaseAgent:
    """Connects and reports ready, but the state snapshot never arrives."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.running = True

    async def connect(self) -> None:
        while self.running:
            await asyncio.sleep(0.01)

    async def wait_until_ready(self, *, timeout: float) -> bool:
        return True

    async def request_state(self) -> None:
        return None


async def test_run_git_release_never_blocks_on_a_hub_that_answers_nothing() -> None:
    """A ready hub whose snapshot never lands releases nothing and exits zero."""
    rc = await run_git_release(
        uri="ws://unused",
        name="me",
        trigger="commit",
        runner=_release_runner("src/a.py\n"),
        agent_factory=_SilentReleaseAgent,  # type: ignore[arg-type]
    )
    assert rc == 0


class _SnapshotReleaseAgent(_SilentReleaseAgent):
    """Return one controlled snapshot and record attempted releases."""

    def __init__(
        self,
        _name: str,
        callback: Callable[[dict[str, Any]], Any],
        *,
        snapshot: dict[str, Any],
        released: list[str],
        **_kwargs: object,
    ) -> None:
        super().__init__()
        self.callback = callback
        self.snapshot = snapshot
        self.released = released

    async def request_state(self) -> None:
        await self.callback({"type": "state_snapshot", "snapshot": self.snapshot})

    async def release(self, task_id: str) -> None:
        self.released.append(task_id)


async def test_run_git_release_skips_present_invalid_snapshot_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed additive identity never downgrades to legacy auto-release."""
    monkeypatch.setattr(githook, "resolve_claim_scope_identity", _release_scope)
    released: list[str] = []
    snapshot = {
        "active_claims": [
            {
                "task_id": "MALFORMED",
                "owner": "me",
                "worktree": "/repo",
                "paths": ["src/a.py"],
                "path_identity": {"version": 999},
                "git": {"auto_release_on": "commit"},
            }
        ]
    }

    def factory(name: str, callback: Callable[[dict[str, Any]], Any], **kwargs: object) -> Any:
        return _SnapshotReleaseAgent(
            name,
            callback,
            snapshot=snapshot,
            released=released,
            **kwargs,
        )

    assert (
        await run_git_release(
            uri="ws://unused",
            name="me",
            trigger="commit",
            runner=_release_runner("src/a.py\n"),
            agent_factory=factory,
        )
        == 0
    )
    assert released == []


async def test_run_git_release_local_identity_failure_is_noop() -> None:
    """An unresolved local repository never reaches the release mutation plane."""
    constructed = False

    def factory(*_args: object, **_kwargs: object) -> Any:
        nonlocal constructed
        constructed = True
        raise AssertionError("release client must not be constructed")

    assert (
        await run_git_release(
            uri="ws://unused",
            name="me",
            trigger="commit",
            runner=_release_runner("src/a.py\n"),
            agent_factory=factory,
        )
        == 0
    )
    assert not constructed
