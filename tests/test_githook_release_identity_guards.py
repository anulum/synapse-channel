# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — post-commit auto-release in a real repository: hub loss and identity guards
"""The release hook reaches the hub from a real repository and never frees a mismatched claim."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from claim_outcome_helpers import ClaimProxy, claim_proxy
from cli_e2e_helpers import git_repo, git_run
from hub_e2e_helpers import _free_port, running_hub
from synapse_channel.git.gitclaim import run_git_claim
from synapse_channel.git.githook import run_git_release
from synapse_channel.git.semantic_scope import semantic_scope_path


def _committed_repo(tmp_path: Path) -> Path:
    """A repository whose last commit changed notes.md (lower case, so case folding is inert)."""
    repo = git_repo(tmp_path / "repo")
    (repo / "notes.md").write_text("changed\n", encoding="utf-8")
    git_run(repo, "add", "notes.md")
    git_run(repo, "commit", "-q", "-m", "changed")
    return repo


async def test_unreachable_hub_from_a_real_repository_never_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The hook resolves the real repository, finds no hub, reports it and exits 0."""
    monkeypatch.chdir(_committed_repo(tmp_path))
    uri = f"ws://127.0.0.1:{_free_port()}"
    assert await run_git_release(uri=uri, name="me", trigger="commit", ready_timeout=0.2) == 0
    assert f"[me] Could not reach hub at {uri}." in capsys.readouterr().out


def _misaligned_paths(claim: dict[str, Any]) -> None:
    claim["paths"] = [*claim["paths"], "other.md"]


def _recreated_worktree(claim: dict[str, Any]) -> None:
    claim["path_identity"]["worktree_object_id"] = "811:1:1"


def _opposite_case_policy(claim: dict[str, Any]) -> None:
    # Flip the recorded policy so it differs on any host: a case-sensitive Linux
    # worktree and a case-insensitive macOS one both disagree with the result.
    claim["path_identity"]["case_sensitive"] = not claim["path_identity"]["case_sensitive"]


@pytest.mark.parametrize(
    "damage",
    [_misaligned_paths, _recreated_worktree, _opposite_case_policy],
    ids=["misaligned-identity", "other-worktree-object", "case-policy-differs"],
)
async def test_release_retains_a_claim_whose_identity_does_not_bind_this_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: Callable[[dict[str, Any]], None],
) -> None:
    """A snapshot claim that no longer binds this worktree's identity is never released."""
    monkeypatch.chdir(_committed_repo(tmp_path))

    def transform(data: dict[str, Any]) -> None:
        if data.get("type") == "state_snapshot":
            damage(data["snapshot"]["active_claims"][0])

    async with running_hub() as (hub, upstream):
        claimed = await run_git_claim(
            uri=upstream, name="me", task_id="T", paths=["notes.md"], auto_release_on="commit"
        )
        assert claimed == 0
        original = hub.state.claims["T"].as_persisted_dict()
        proxy = ClaimProxy(upstream, drop_grants=False, transform=transform)
        async with claim_proxy(proxy) as uri:
            assert await run_git_release(uri=uri, name="me", trigger="commit") == 0
        assert "state_request" in proxy.requests
        assert proxy.requests.count("release") == 0
        assert hub.state.claims["T"].as_persisted_dict() == original
        # The undamaged snapshot does release it, so only the identity guard held it.
        control = ClaimProxy(upstream, drop_grants=False)
        async with claim_proxy(control) as uri:
            assert await run_git_release(uri=uri, name="me", trigger="commit") == 0
        assert control.requests.count("release") == 1


def _merged_repo(tmp_path: Path) -> Path:
    """Return a repository whose latest commit is a merge that added notes.md."""
    repo = git_repo(tmp_path / "repo")
    git_run(repo, "branch", "-M", "main")
    git_run(repo, "checkout", "-q", "-b", "feature")
    (repo / "notes.md").write_text("from feature\n", encoding="utf-8")
    git_run(repo, "add", "notes.md")
    git_run(repo, "commit", "-q", "-m", "feature")
    git_run(repo, "checkout", "-q", "main")
    git_run(repo, "merge", "--no-ff", "-m", "merge feature", "feature")
    return repo


async def test_merge_hook_releases_only_the_path_the_merge_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-merge hook frees the merged path and keeps an untouched one."""
    monkeypatch.chdir(_merged_repo(tmp_path))
    async with running_hub() as (hub, uri):
        assert (
            await run_git_claim(
                uri=uri,
                name="me",
                task_id="MERGED",
                paths=["notes.md"],
                auto_release_on="merge",
            )
            == 0
        )
        assert (
            await run_git_claim(
                uri=uri,
                name="me",
                task_id="UNTOUCHED",
                paths=["README.md"],
                auto_release_on="merge",
            )
            == 0
        )
        assert set(hub.state.claims) == {"MERGED", "UNTOUCHED"}
        assert await run_git_release(uri=uri, name="me", trigger="merge") == 0
        assert set(hub.state.claims) == {"UNTOUCHED"}


def _initial_commit_repo(tmp_path: Path) -> Path:
    """Return a repository whose only commit added a source file and a note."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git_run(repo, "init", "-q")
    git_run(repo, "config", "user.email", "e2e@example.test")
    git_run(repo, "config", "user.name", "e2e")
    git_run(repo, "config", "commit.gpgsign", "false")
    git_run(repo, "branch", "-M", "main")
    source = repo / "src" / "a.py"
    source.parent.mkdir()
    source.write_text("class Worker:\n    pass\n", encoding="utf-8")
    (repo / "notes.md").write_text("noted\n", encoding="utf-8")
    git_run(repo, "add", "src/a.py", "notes.md")
    git_run(repo, "commit", "-q", "-m", "initial")
    return repo


async def test_root_commit_retains_claims_when_semantic_evidence_has_no_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A root commit has no parent, so the hook keeps both claims and exits 0."""
    monkeypatch.chdir(_initial_commit_repo(tmp_path))
    symbol = semantic_scope_path("src/a.py", "Worker")
    async with running_hub() as (hub, uri):
        assert (
            await run_git_claim(
                uri=uri,
                name="me",
                task_id="SYMBOL",
                paths=[symbol],
                auto_release_on="commit",
            )
            == 0
        )
        assert (
            await run_git_claim(
                uri=uri,
                name="me",
                task_id="NOTE",
                paths=["notes.md"],
                auto_release_on="commit",
            )
            == 0
        )
        assert set(hub.state.claims) == {"SYMBOL", "NOTE"}
        assert await run_git_release(uri=uri, name="me", trigger="commit") == 0
        assert set(hub.state.claims) == {"SYMBOL", "NOTE"}
    captured = capsys.readouterr()
    assert "semantic auto-release retained symbol claims" in captured.out
    assert "bad revision 'HEAD^'" in captured.out
    assert "git error:" not in captured.out


async def test_merge_hook_stops_before_the_hub_when_orig_head_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A merge hook with no ORIG_HEAD reports the git failure and keeps the claim."""
    monkeypatch.chdir(_committed_repo(tmp_path))
    async with running_hub() as (hub, uri):
        assert (
            await run_git_claim(
                uri=uri,
                name="me",
                task_id="NOTE",
                paths=["notes.md"],
                auto_release_on="merge",
            )
            == 0
        )
        assert set(hub.state.claims) == {"NOTE"}
        assert await run_git_release(uri=uri, name="me", trigger="merge") == 1
        assert set(hub.state.claims) == {"NOTE"}
    captured = capsys.readouterr()
    assert "git error:" in captured.out
    assert "ORIG_HEAD" in captured.out
    assert "Could not reach hub" not in captured.out


async def test_detached_head_stops_before_the_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A release from a detached HEAD reports the git failure and keeps the claim."""
    repo = _committed_repo(tmp_path)
    monkeypatch.chdir(repo)
    async with running_hub() as (hub, uri):
        assert (
            await run_git_claim(
                uri=uri,
                name="me",
                task_id="NOTE",
                paths=["notes.md"],
                auto_release_on="commit",
            )
            == 0
        )
        git_run(repo, "checkout", "--detach", "-q")
        assert set(hub.state.claims) == {"NOTE"}
        assert await run_git_release(uri=uri, name="me", trigger="commit") == 1
        assert set(hub.state.claims) == {"NOTE"}
    captured = capsys.readouterr()
    assert "git error:" in captured.out
    assert "symbolic-ref" in captured.out
    assert "Could not reach hub" not in captured.out
