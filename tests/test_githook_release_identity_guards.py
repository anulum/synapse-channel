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
