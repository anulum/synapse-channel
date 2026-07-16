# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic auto-release policy regressions
"""Prove release context and fail-safe candidate selection."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.githook import run_git_release
from synapse_channel.git.semantic_diff import SemanticDiffRecord
from synapse_channel.git.semantic_release import release_candidates, release_context
from synapse_channel.git.semantic_scope import semantic_scope_path

GitPayload = dict[str, str]
ClaimSpec = tuple[str, list[str], GitPayload | None]


def _runner(values: dict[tuple[str, ...], str]) -> Callable[[list[str]], str]:
    def run(args: list[str]) -> str:
        return values[tuple(args)]

    return run


def _claim(
    root: Path,
    *,
    owner: str = "seat/one",
    branch: str = "main",
    trigger: str = "commit",
    paths: object = None,
) -> dict[str, Any]:
    return {
        "task_id": "TASK",
        "owner": owner,
        "status": "claimed",
        "worktree": str(root),
        "paths": ["src/a.py"] if paths is None else paths,
        "git": {
            "branch": branch,
            "base": "main",
            "auto_release_on": trigger,
        },
    }


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 3.0) -> None:
    """Wait until the isolated hub reflects one release."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("condition did not become true")


async def _claim_many(
    uri: str,
    owner: str,
    claims: list[ClaimSpec],
    *,
    worktree: str = "/repo",
) -> None:
    """Create exact release claims against an isolated hub."""
    handle = await connect_agent(owner, uri)
    try:
        for task_id, paths, git in claims:
            await handle.agent.claim(task_id, paths=paths, git=git, worktree=worktree)

            def saw_claim_granted(message: dict[str, Any], expected: str = task_id) -> bool:
                return message.get("type") == "claim_granted" and message.get("task_id") == expected

            await handle.recorder.wait_for(saw_claim_granted)
    finally:
        await close_agents(handle)


def _release_runner(
    changed: str,
    *,
    root: str = "/repo",
    branch: str = "x",
) -> Callable[[list[str]], str]:
    """Return a deterministic Git runner for release orchestration tests."""

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
        raise AssertionError(args)

    return run


def _semantic_record(
    source: str,
    *,
    claim_paths: tuple[str, ...],
    narrowed: bool = True,
) -> SemanticDiffRecord:
    """Build one semantic release record."""
    return SemanticDiffRecord(
        status="M",
        source=source,
        old_source=source,
        language="python",
        symbols=("run",) if narrowed else (),
        semantic_scopes=claim_paths if narrowed else (),
        claim_paths=claim_paths,
        narrowed=narrowed,
        reason="test evidence",
    )


def _git(repo: Path, *args: str) -> str:
    """Run Git in one temporary release worktree."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_runner(repo: Path) -> Callable[[list[str]], str]:
    """Adapt the temporary repository to the production Git runner protocol."""

    def run(args: list[str]) -> str:
        return _git(repo, *args)

    return run


def test_release_context_requires_canonical_attached_worktree(tmp_path: Path) -> None:
    runner = _runner(
        {
            ("rev-parse", "--show-toplevel"): str(tmp_path),
            ("symbolic-ref", "--quiet", "--short", "HEAD"): "main",
        }
    )
    assert release_context(runner=runner) == (tmp_path.resolve(), "main")

    for root, branch in (("", "main"), (str(tmp_path), "")):
        invalid = _runner(
            {
                ("rev-parse", "--show-toplevel"): root,
                ("symbolic-ref", "--quiet", "--short", "HEAD"): branch,
            }
        )
        with pytest.raises(GitError, match="no release"):
            release_context(runner=invalid)

    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    unresolvable = _runner(
        {
            ("rev-parse", "--show-toplevel"): str(loop),
            ("symbolic-ref", "--quiet", "--short", "HEAD"): "main",
        }
    )
    with pytest.raises(GitError, match="invalid release worktree"):
        release_context(runner=unresolvable)


def test_release_candidates_are_exact_context_owner_and_trigger_bound(
    tmp_path: Path,
) -> None:
    matching = _claim(tmp_path)
    claims: list[object] = [
        matching,
        _claim(tmp_path, owner="seat/two"),
        _claim(tmp_path, branch="other"),
        _claim(tmp_path, trigger="merge"),
        _claim(tmp_path / "other"),
        "malformed",
    ]

    assert release_candidates(
        claims,
        name="seat/one",
        trigger="commit",
        root=tmp_path.resolve(),
        branch="main",
    ) == ((matching, ("src/a.py",)),)


@pytest.mark.parametrize("paths", ["src/a.py", [7]])
def test_malformed_matching_claim_is_retained_not_released(
    tmp_path: Path,
    paths: object,
) -> None:
    claim = _claim(tmp_path, paths=paths)
    assert (
        release_candidates(
            [claim],
            name="seat/one",
            trigger="commit",
            root=tmp_path.resolve(),
            branch="main",
        )
        == ()
    )


async def test_release_context_must_match_worktree_and_branch() -> None:
    """Claims from another Git context remain active."""
    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "me",
            [
                (
                    "OTHER_ROOT",
                    ["src/a.py"],
                    {"branch": "x", "base": "main", "auto_release_on": "commit"},
                )
            ],
            worktree="/other",
        )
        await _claim_many(
            uri,
            "me",
            [
                (
                    "OTHER_BRANCH",
                    ["src/a.py"],
                    {"branch": "other", "base": "main", "auto_release_on": "commit"},
                )
            ],
        )

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=_release_runner("src/a.py\n"),
        )

        assert rc == 0
        assert set(hub.state.claims) == {"OTHER_ROOT", "OTHER_BRANCH"}


async def test_semantic_release_releases_only_the_changed_symbol() -> None:
    """Injected exact semantic evidence releases one sibling claim."""
    source = "src/a.py"
    changed_scope = semantic_scope_path(source, "changed")
    sibling_scope = semantic_scope_path(source, "sibling")
    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "me",
            [
                (
                    "CHANGED",
                    [changed_scope],
                    {"branch": "x", "base": "main", "auto_release_on": "commit"},
                ),
                (
                    "SIBLING",
                    [sibling_scope],
                    {"branch": "x", "base": "main", "auto_release_on": "commit"},
                ),
            ],
        )

        def resolve(
            root: Path,
            trigger: str,
            paths: Sequence[str],
        ) -> tuple[SemanticDiffRecord, ...]:
            assert root == Path("/repo")
            assert trigger == "commit"
            assert paths == (source,)
            return (_semantic_record(source, claim_paths=(changed_scope,)),)

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=_release_runner(f"{source}\n"),
            semantic_resolver=resolve,
        )

        assert rc == 0
        await _wait_until(lambda: "CHANGED" not in hub.state.claims)
        assert set(hub.state.claims) == {"SIBLING"}


async def test_real_committed_diff_releases_only_the_proven_symbol(
    tmp_path: Path,
) -> None:
    """Production committed-diff resolution releases only one changed declaration."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    source_path = repo / "worker.py"
    source_path.write_text(
        "def changed():\n    return 1\n\ndef sibling():\n    return 1\n",
        encoding="utf-8",
    )
    _git(repo, "add", "worker.py")
    _git(repo, "commit", "-qm", "base")
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    changed_scope = semantic_scope_path("worker.py", "changed")
    sibling_scope = semantic_scope_path("worker.py", "sibling")

    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "me",
            [
                (
                    "CHANGED",
                    [changed_scope],
                    {
                        "branch": branch,
                        "base": branch,
                        "auto_release_on": "commit",
                    },
                ),
                (
                    "SIBLING",
                    [sibling_scope],
                    {
                        "branch": branch,
                        "base": branch,
                        "auto_release_on": "commit",
                    },
                ),
            ],
            worktree=str(repo),
        )
        source_path.write_text(
            "def changed():\n    return 2\n\ndef sibling():\n    return 1\n",
            encoding="utf-8",
        )
        _git(repo, "add", "worker.py")
        _git(repo, "commit", "-qm", "change one symbol")

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=_git_runner(repo),
        )

        assert rc == 0
        await _wait_until(lambda: "CHANGED" not in hub.state.claims)
        assert set(hub.state.claims) == {"SIBLING"}


async def test_ambiguous_or_unavailable_semantics_retain_symbol_claims(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unproven semantic changes never release a symbol claim."""
    source = "src/a.py"
    scope = semantic_scope_path(source, "run")
    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "me",
            [
                (
                    "AMBIGUOUS",
                    [scope],
                    {"branch": "x", "base": "main", "auto_release_on": "commit"},
                )
            ],
        )

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=_release_runner(f"{source}\n"),
            semantic_resolver=lambda _root, _trigger, _paths: (
                _semantic_record(source, claim_paths=(source,), narrowed=False),
            ),
        )
        assert rc == 0
        assert set(hub.state.claims) == {"AMBIGUOUS"}

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=_release_runner(f"{source}\n"),
            semantic_resolver=lambda _root, _trigger, _paths: (_ for _ in ()).throw(
                RuntimeError("parser unavailable")
            ),
        )
        assert rc == 0
        assert set(hub.state.claims) == {"AMBIGUOUS"}
    assert "retained symbol claims: parser unavailable" in capsys.readouterr().out


async def test_out_of_scope_resolver_record_cannot_release_a_symbol_claim() -> None:
    """Only records for the requested physical source participate in release."""
    source = "src/a.py"
    scope = semantic_scope_path(source, "run")
    async with running_hub() as (hub, uri):
        await _claim_many(
            uri,
            "me",
            [
                (
                    "SCOPED",
                    [scope],
                    {"branch": "x", "base": "main", "auto_release_on": "commit"},
                )
            ],
        )

        rc = await run_git_release(
            uri=uri,
            name="me",
            trigger="commit",
            runner=_release_runner(f"{source}\n"),
            semantic_resolver=lambda _root, _trigger, _paths: (
                _semantic_record("src/other.py", claim_paths=(scope,)),
            ),
        )

        assert rc == 0
        assert set(hub.state.claims) == {"SCOPED"}
