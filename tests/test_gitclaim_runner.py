# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for client-side git-scoped claims

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from synapse_channel.git.gitclaim import (
    GitError,
    _default_git_runner,
)


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
