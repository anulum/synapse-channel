# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for client-side git-scoped claims

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synapse_channel.git.gitclaim import (
    GitError,
    _default_git_runner,
)


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@contextmanager
def _inside(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def test_default_git_runner_returns_stripped_stdout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-b", "feature/x")
    _run_git(repo, "config", "user.email", "test@example.invalid")
    _run_git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _run_git(repo, "add", "tracked.txt")
    _run_git(repo, "commit", "-m", "initial")
    with _inside(repo):
        assert _default_git_runner(["rev-parse", "--abbrev-ref", "HEAD"]) == "feature/x"


def test_default_git_runner_missing_git() -> None:
    code = """
from synapse_channel.git.gitclaim import GitError, _default_git_runner
try:
    _default_git_runner(['status'])
except GitError as exc:
    raise SystemExit(0 if 'not installed' in str(exc) else 2)
raise SystemExit(3)
"""
    env = os.environ.copy()
    env["PATH"] = ""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_default_git_runner_nonzero_uses_stderr(tmp_path: Path) -> None:
    with _inside(tmp_path), pytest.raises(GitError, match="not a git repository"):
        _default_git_runner(["status"])


def test_default_git_runner_nonzero_without_stderr(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    with (
        _inside(repo),
        pytest.raises(GitError, match="git rev-parse --quiet --verify missing-ref exited non-zero"),
    ):
        _default_git_runner(["rev-parse", "--quiet", "--verify", "missing-ref"])


def test_default_git_runner_reports_git_absent_from_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing git binary is a named GitError, not a FileNotFoundError."""
    monkeypatch.setattr("synapse_channel.git.gitclaim.shutil.which", lambda _name: None)
    with pytest.raises(GitError, match="not installed or not on PATH"):
        _default_git_runner(["status"])


def test_default_git_runner_wraps_a_vanishing_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git disappearing between the which() check and the run is still a GitError."""
    monkeypatch.setattr("synapse_channel.git.gitclaim.shutil.which", lambda _name: "/usr/bin/git")

    def vanish(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("/usr/bin/git")

    monkeypatch.setattr("synapse_channel.git.gitclaim.subprocess.run", vanish)
    with pytest.raises(GitError, match="not installed or not on PATH"):
        _default_git_runner(["status"])
