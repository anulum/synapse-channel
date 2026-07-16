# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real Git-scoped MCP claim contract
"""Exercise MCP Git claims against a real repository and live hub."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli_e2e_helpers import git_repo, git_run
from hub_e2e_helpers import running_hub
from mcp_server_helpers import start_bridge
from synapse_channel.mcp.git_claim import McpGitClaimError, resolve_mcp_git_claim_scope


def test_resolve_mcp_git_claim_scope_uses_real_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = git_repo(tmp_path / "repo")
    git_run(repo, "branch", "-M", "fixture-main")
    monkeypatch.chdir(repo)

    scope = resolve_mcp_git_claim_scope(
        ["src/owner.py", "tests/test_owner.py"],
        base="main",
        auto_release_on="manual",
    )

    assert scope.worktree == str(repo.resolve())
    assert scope.paths == ("src/owner.py", "tests/test_owner.py")
    assert scope.git == {
        "branch": "fixture-main",
        "base": "main",
        "auto_release_on": "manual",
    }
    assert scope.path_identity["version"] == 1
    identity_paths = scope.path_identity["paths"]
    assert isinstance(identity_paths, list)
    assert len(identity_paths) == 2


def test_resolve_mcp_git_claim_scope_requires_explicit_whole_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = git_repo(tmp_path / "repo")
    git_run(repo, "branch", "-M", "fixture-main")
    monkeypatch.chdir(repo)

    scope = resolve_mcp_git_claim_scope(
        None,
        base="main",
        auto_release_on="commit",
        whole_worktree=True,
    )

    assert scope.paths == ()
    assert scope.git["auto_release_on"] == "commit"


@pytest.mark.parametrize(
    ("paths", "whole_worktree"),
    [
        (None, False),
        ([], False),
        (["../outside"], False),
        (["/absolute"], False),
        (["line\nbreak"], False),
        (["src/./owner.py"], False),
        ([" owner.py"], False),
        (["src"], True),
    ],
)
def test_resolve_mcp_git_claim_scope_refuses_ambiguous_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    paths: list[str] | None,
    whole_worktree: bool,
) -> None:
    repo = git_repo(tmp_path / "repo")
    git_run(repo, "branch", "-M", "fixture-main")
    monkeypatch.chdir(repo)

    with pytest.raises(McpGitClaimError):
        resolve_mcp_git_claim_scope(
            paths,
            base="main",
            auto_release_on="manual",
            whole_worktree=whole_worktree,
        )


@pytest.mark.parametrize(
    ("base", "auto_release_on"),
    [("", "manual"), ("main\nnext", "manual"), ("main", "unknown")],
)
def test_resolve_mcp_git_claim_scope_refuses_invalid_git_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base: str,
    auto_release_on: str,
) -> None:
    repo = git_repo(tmp_path / "repo")
    git_run(repo, "branch", "-M", "fixture-main")
    monkeypatch.chdir(repo)

    with pytest.raises(McpGitClaimError):
        resolve_mcp_git_claim_scope(
            ["src/owner.py"],
            base=base,
            auto_release_on=auto_release_on,
        )


def test_resolve_mcp_git_claim_scope_fails_closed_outside_git(tmp_path: Path) -> None:
    def failed_git(_args: list[str]) -> str:
        raise RuntimeError("git unavailable")

    with pytest.raises(McpGitClaimError, match="could not resolve"):
        resolve_mcp_git_claim_scope(
            ["src/owner.py"],
            base="main",
            auto_release_on="manual",
            runner=failed_git,
        )


async def test_mcp_git_claim_and_release_carry_scope_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = git_repo(tmp_path / "repo")
    git_run(repo, "branch", "-M", "fixture-main")
    monkeypatch.chdir(repo)

    async with running_hub() as (hub, uri):
        handle = await start_bridge(uri, name="editor-seat")
        try:
            claimed = await handle.bridge.git_claim(
                "EDITOR-GOVERNANCE",
                ["allowed.txt"],
                base="main",
                auto_release_on="manual",
            )
            claim = hub.state.claims["EDITOR-GOVERNANCE"]
            released = await handle.bridge.release(
                "EDITOR-GOVERNANCE",
                evidence=["real editor governance turn"],
                changed_files=["allowed.txt"],
                confidence="high",
            )
        finally:
            await handle.close()

    assert "claim granted" in claimed
    assert claim.owner == "editor-seat"
    assert claim.worktree == str(repo.resolve())
    assert claim.paths == ("allowed.txt",)
    assert claim.path_identity is not None
    assert claim.git is not None
    assert claim.git.branch == "fixture-main"
    assert released == "released 'EDITOR-GOVERNANCE' with receipt owner 'editor-seat'"
    assert "EDITOR-GOVERNANCE" not in hub.state.claims
    assert hub.blackboard.progress[-1].author == "editor-seat"
    assert "evidence=real editor governance turn" in hub.blackboard.progress[-1].text
    assert "changed_files=allowed.txt" in hub.blackboard.progress[-1].text
