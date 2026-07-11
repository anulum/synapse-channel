# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — v0.99.4 path-claim governance incident regression

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.git.staged_claim_check import run_staged_claim_check

RELEASE_PATHS = (
    ".zenodo.json",
    "CHANGELOG.md",
    "CITATION.cff",
    "README.md",
    "docs/_generated/capability_manifest.json",
    "pyproject.toml",
    "server.json",
    "src/synapse_channel/__init__.py",
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603, S607 - fixed test-only Git invocation
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _staged_release_repo(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "incident-test")
    _git(root, "config", "user.email", "incident@example.test")
    _git(root, "config", "synapse.identity", "release/owner")
    _git(root, "config", "synapse.uri", "ws://isolated")
    for path in RELEASE_PATHS:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"release surface {path}\n", encoding="utf-8")
    _git(root, "add", "-A")
    return root


@pytest.mark.asyncio
async def test_git_lock_cannot_cover_the_exact_v0994_release_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _staged_release_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    lock: dict[str, Any] = {
        "task_id": "SYNAPSE-CHANNEL:git",
        "owner": "release/owner",
        "status": "claimed",
        "worktree": "SYNAPSE-CHANNEL:git",
        "paths": [],
        "git": None,
    }

    async def lock_only(**kwargs: Any) -> dict[str, Any]:
        return {"active_claims": [lock]}

    refused = await run_staged_claim_check(environment={}, state_fetcher=lock_only)
    assert refused.allowed is False
    assert refused.paths == RELEASE_PATHS
    assert all(path in refused.reason for path in RELEASE_PATHS)

    exact_claim = {
        "task_id": "SCH-CHANNEL-20260711-RELEASE-0994",
        "owner": "release/owner",
        "status": "claimed",
        "worktree": str(repo.resolve()),
        "paths": list(RELEASE_PATHS),
        "git": {"branch": "main", "base": "main", "auto_release_on": "manual"},
    }

    async def lock_and_claim(**kwargs: Any) -> dict[str, Any]:
        return {"active_claims": [lock, exact_claim]}

    allowed = await run_staged_claim_check(environment={}, state_fetcher=lock_and_claim)
    assert allowed.allowed is True
    assert allowed.paths == RELEASE_PATHS
