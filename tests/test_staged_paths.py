# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strict staged Git path grammar tests

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from synapse_channel.core.scoping import MAX_PATH_LENGTH
from synapse_channel.git.gitclaim import GitError
from synapse_channel.git.staged_paths import parse_staged_name_status, read_staged_paths


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603, S607 - fixed test-only Git invocation
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout


def _repo(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "staged-test")
    _git(root, "config", "user.email", "staged@example.test")
    return root


def _commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")


def test_parse_expands_copy_and_rename_and_deduplicates() -> None:
    raw = "M\0same.py\0R100\0old.py\0new.py\0C087\0same.py\0copy.py\0"
    assert parse_staged_name_status(raw) == ("same.py", "old.py", "new.py", "copy.py")


@pytest.mark.parametrize("status", ["A", "M", "D", "T", "U", "X", "B"])
def test_parse_accepts_every_single_path_status(status: str) -> None:
    assert parse_staged_name_status(f"{status}\0dir/file\0") == ("dir/file",)


def test_parse_normalises_safe_separators_and_segments() -> None:
    assert parse_staged_name_status("A\0dir\\sub/./file.py\0") == ("dir/sub/file.py",)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("M\0file", "truncated staged-path output"),
        ("Q\0file\0", "unknown staged-path status"),
        ("M\0", "truncated staged-path record"),
        ("R100\0old\0", "truncated staged-path record"),
        ("M\0\0", "invalid staged path"),
        ("M\0/file\0", "absolute staged path"),
        ("M\0C:\\file\0", "absolute staged path"),
        ("M\0../file\0", "parent-escaping staged path"),
        ("M\0file \0", "invalid staged path"),
        ("M\0.\0", "worktree root"),
        ("M\0bad\rname\0", "unsupported control"),
        (f"M\0{'x' * (MAX_PATH_LENGTH + 1)}\0", "invalid staged path"),
    ],
)
def test_parse_refuses_malformed_records(raw: str, message: str) -> None:
    with pytest.raises(GitError, match=message):
        parse_staged_name_status(raw)


def test_empty_diff_is_empty() -> None:
    assert parse_staged_name_status("") == ()


def test_reader_uses_the_exact_nul_delimited_index_command() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        return "A\0file.py\0"

    assert read_staged_paths(runner=runner) == ("file.py",)
    assert calls == [["diff", "--cached", "--name-status", "-z", "--find-renames", "--find-copies"]]


def test_real_git_covers_add_modify_delete_type_and_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    for name in ("delete.txt", "type.txt", "old.txt", "modify\nname.txt"):
        (repo / name).write_text(f"seed {name}\n", encoding="utf-8")
    _commit_all(repo)

    (repo / "added space.txt").write_text("added\n", encoding="utf-8")
    (repo / "modify\nname.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "rm", "-q", "delete.txt")
    _git(repo, "mv", "old.txt", "renamed.txt")
    (repo / "type.txt").unlink()
    os.symlink("target", repo / "type.txt")
    _git(repo, "add", "-A")

    monkeypatch.chdir(repo)
    assert set(read_staged_paths()) == {
        "added space.txt",
        "delete.txt",
        "modify\nname.txt",
        "old.txt",
        "renamed.txt",
        "type.txt",
    }


def test_real_git_copy_record_covers_source_and_both_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "source.txt").write_text("copy me\n" * 20, encoding="utf-8")
    _commit_all(repo)
    content = (repo / "source.txt").read_text(encoding="utf-8")
    (repo / "copy-one.txt").write_text(content, encoding="utf-8")
    (repo / "copy-two.txt").write_text(content, encoding="utf-8")
    (repo / "source.txt").unlink()
    _git(repo, "add", "-A")

    raw = _git(repo, "diff", "--cached", "--name-status", "-z", "--find-renames", "--find-copies")
    assert "C100\0" in raw
    monkeypatch.chdir(repo)
    assert set(read_staged_paths()) == {"source.txt", "copy-one.txt", "copy-two.txt"}


def test_real_git_clean_index_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _commit_all(repo)
    monkeypatch.chdir(repo)
    assert read_staged_paths() == ()
