# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the bounded Git runner: exit code kept, standard input, a deadline
"""The bounded runner returns a refusal to read, feeds standard input, and stops at its deadline."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.git.git_runtime import (
    GIT_DEADLINE_SECONDS,
    NO_GIT,
    GitError,
    git_executable,
    run_git,
)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    assert run_git(git_executable(), path, ["init", "-q"]).returncode == 0
    return path


def test_a_refusal_is_returned_with_its_exit_code_and_words(repository: Path) -> None:
    completed = run_git(git_executable(), repository, ["rev-parse", "--verify", "refs/x"])
    assert completed.returncode == 128
    assert completed.stdout == ""
    assert completed.stderr.strip() == "fatal: Needed a single revision"


def test_standard_input_reaches_the_command(repository: Path) -> None:
    git = git_executable()
    written = run_git(git, repository, ["hash-object", "-w", "--stdin"], input_text="marker\n")
    blob = written.stdout.strip()
    assert written.returncode == 0 and len(blob) == 40
    assert run_git(git, repository, ["cat-file", "-p", blob]).stdout == "marker\n"


def test_a_host_without_git_is_refused_with_words(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(GitError) as refused:
        git_executable()
    assert str(refused.value) == NO_GIT


def test_a_git_call_past_its_deadline_is_refused(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deadlines: list[object] = []

    def too_slow(*_args: Any, **kwargs: Any) -> Any:
        deadlines.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd="git", timeout=GIT_DEADLINE_SECONDS)

    git = git_executable()
    monkeypatch.setattr(subprocess, "run", too_slow)
    with pytest.raises(GitError) as refused:
        run_git(git, repository, ["rev-parse", "HEAD"])
    assert str(refused.value) == "git rev-parse did not finish within 30 seconds"
    assert deadlines == [30.0]
