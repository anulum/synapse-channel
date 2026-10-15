# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for git-hook auto-release of branch-scoped claims

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from claim_outcome_helpers import ClaimProxy, claim_proxy
from cli_e2e_helpers import git_repo, git_run
from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.git import githook
from synapse_channel.git.gitclaim import GitError, _default_git_runner, run_git_claim
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
    assert "release requested on commit: T1" in capsys.readouterr().out


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
    branch = _default_git_runner(["-C", str(repo), "branch", "--show-current"])
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
            command = args if "-C" in args else ["-C", str(repo), *args]
            return _default_git_runner(command)

        assert await run_git_release(uri=uri, name="me", trigger="commit", runner=runner) == 0
        await _wait_until(lambda: "HERE" not in hub.state.claims)
        assert "AWAY" in hub.state.claims


@pytest.mark.parametrize("fault", ["lost", "invalid-identity"])
async def test_run_git_release_retains_claim_when_snapshot_cannot_authorize_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Actual hook reads never release on missing or malformed scope evidence."""
    repo = git_repo(tmp_path / "repo")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    git_run(repo, "add", "README.md")
    git_run(repo, "commit", "-q", "-m", "changed")
    monkeypatch.chdir(repo)

    def damage(data: dict[str, Any]) -> None:
        if data.get("type") == "state_snapshot":
            data["snapshot"]["active_claims"][0]["path_identity"] = {"version": 999}

    async with running_hub() as (hub, upstream):
        assert (
            await run_git_claim(
                uri=upstream,
                name="me",
                task_id="T",
                paths=["README.md"],
                auto_release_on="commit",
            )
            == 0
        )
        original = hub.state.claims["T"].as_persisted_dict()
        proxy = ClaimProxy(
            upstream,
            drop_grants=False,
            drop_snapshots=fault == "lost",
            transform=damage if fault == "invalid-identity" else None,
        )
        async with claim_proxy(proxy) as uri:
            assert await run_git_release(uri=uri, name="me", trigger="commit") == 0
        assert hub.state.claims["T"].as_persisted_dict() == original
        assert proxy.requests.count("release") == 0
        assert await run_git_release(uri=upstream, name="me", trigger="commit") == 0
        await _wait_until(lambda: "T" not in hub.state.claims)


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
