# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the one Git write of a publication: a compare-and-swap, read back
"""The ref moves only from exactly the declared commit; a fast-forward would not check that."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.git.compare_and_swap import compare_and_swap, read_ref
from synapse_channel.git.git_runtime import git_executable

MAIN = "refs/heads/main"


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _commit(repository: Path, parent: str | None, name: str) -> str:
    """Write one file and return a commit of it on ``parent``, moving no ref."""
    (repository / name).write_text(name + "\n", encoding="utf-8")
    _git(repository, "add", name)
    tree = _git(repository, "write-tree")
    args = ["commit-tree", tree, "-m", name]
    if parent is not None:
        args += ["-p", parent]
    return _git(repository, *args)


@pytest.fixture
def history(tmp_path: Path) -> dict[str, Any]:
    """A repository whose main holds B; A is B's parent, X and O are children of B."""
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Suite")
    _git(repository, "config", "user.email", "suite@example.invalid")
    a = _commit(repository, None, "a.txt")
    b = _commit(repository, a, "b.txt")
    x = _commit(repository, b, "x.txt")
    o = _commit(repository, b, "o.txt")
    _git(repository, "update-ref", MAIN, b)
    return {"repository": repository, "a": a, "b": b, "x": x, "o": o, "git": git_executable()}


def test_the_ref_moves_from_exactly_the_declared_commit(history: dict[str, Any]) -> None:
    result = compare_and_swap(
        history["repository"], MAIN, new=history["x"], expected=history["b"], git=history["git"]
    )
    assert (result.applied, result.head, result.detail) == (True, history["x"], "")


def test_a_ref_that_moved_to_a_sibling_is_left_alone_with_gits_own_words(
    history: dict[str, Any],
) -> None:
    repository = history["repository"]
    _git(repository, "update-ref", MAIN, history["o"])
    result = compare_and_swap(
        repository, MAIN, new=history["x"], expected=history["b"], git=history["git"]
    )
    said = subprocess.run(
        ["git", "-C", str(repository), "update-ref", MAIN, history["x"], history["b"]],
        capture_output=True,
        text=True,
        check=False,
    ).stderr.strip()
    assert said
    assert (result.applied, result.head, result.detail) == (False, history["o"], said[:200])


def test_a_success_that_the_ref_does_not_show_is_not_applied(
    history: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_run = subprocess.run

    def silent_update(command: list[str], **kwargs: Any) -> Any:
        if "update-ref" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", silent_update)
    result = compare_and_swap(
        history["repository"], MAIN, new=history["x"], expected=history["b"], git=history["git"]
    )
    assert (result.applied, result.head, result.detail) == (
        False,
        history["b"],
        "the ref was not changed",
    )


def test_a_ref_rewound_to_an_ancestor_is_refused_where_a_fast_forward_would_pass(
    history: dict[str, Any],
) -> None:
    repository = history["repository"]
    _git(repository, "update-ref", MAIN, history["a"])
    ancestry = subprocess.run(
        ["git", "-C", str(repository), "merge-base", "--is-ancestor", history["a"], history["x"]],
        check=False,
    )
    assert ancestry.returncode == 0
    result = compare_and_swap(
        repository, MAIN, new=history["x"], expected=history["b"], git=history["git"]
    )
    assert (result.applied, result.head) == (False, history["a"])


def test_a_missing_ref_reads_as_none(history: dict[str, Any]) -> None:
    assert read_ref(history["repository"], "refs/heads/absent", git=history["git"]) is None
    assert read_ref(history["repository"], MAIN, git=history["git"]) == history["b"]
