# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — committed-source structure measurement journeys
"""Exercise actual Git history and refuse damaged subprocess object streams."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from tools.structure_revision import measure_revision, parse_blob_batch


def git(root: Path, *arguments: str) -> str:
    """Run a real Git operation on a test-owned repository."""
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=t@example.invalid",
            *arguments,
        ],
        text=True,
    ).strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Commit measured and ignored fixture blobs to a real repository."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "with space.py").write_text("class C:\n def __init__(self):\n  self.a = 1\n")
    (root / "pkg" / "ignored.txt").write_bytes(b"\xff")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "fixture")
    return root


def test_measurement_reads_committed_blobs_without_touching_index_or_worktree(
    repository: Path,
) -> None:
    """A staged replacement cannot supply the trusted parent figure."""
    parent = git(repository, "rev-parse", "HEAD")
    path = repository / "pkg" / "with space.py"
    path.write_text("class C:\n def __init__(self):\n  self.a = self.b = 1\n")
    git(repository, "add", ".")
    before = git(repository, "status", "--porcelain")
    figures = measure_revision(repository, parent, ("pkg",))
    assert figures["initialisation_attributes"] == {"pkg/with space.py::C": 1}
    assert git(repository, "status", "--porcelain") == before
    assert "self.b" in path.read_text()


def test_empty_revision_scope_does_not_request_blobs(repository: Path) -> None:
    """An absent measured path contributes no invented source."""
    assert all(not units for units in measure_revision(repository, "HEAD", ("absent",)).values())


@pytest.mark.parametrize("content", [b"def broken(:\n", b"\xff"])
def test_committed_source_that_cannot_be_parsed_is_refused(
    repository: Path, content: bytes
) -> None:
    """Corrupt Python in real history cannot become a zero baseline."""
    (repository / "pkg" / "bad.py").write_bytes(content)
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "invalid source")
    with pytest.raises((SyntaxError, UnicodeDecodeError)):
        measure_revision(repository, "HEAD", ("pkg",))


def test_missing_history_and_missing_git_are_refused(
    repository: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both Git refusal and OS executable absence fail the public reader."""
    with pytest.raises(ValueError, match="unavailable"):
        measure_revision(repository, "missing-revision", ("pkg",))
    monkeypatch.setenv("PATH", str(tmp_path / "no-executables"))
    with pytest.raises(ValueError, match="unavailable"):
        measure_revision(repository, "HEAD", ("pkg",))


@pytest.mark.parametrize(
    "fault",
    ["header", "identity", "type", "negative", "size", "newline", "trailing", "number"],
)
def test_damaged_git_batch_cannot_supply_committed_source(repository: Path, fault: str) -> None:
    """Corrupt an actual Git batch and exercise the production format boundary."""
    object_id = git(repository, "rev-parse", "HEAD:pkg/with space.py")
    raw = subprocess.check_output(
        ["git", "-C", str(repository), "cat-file", "--batch"],
        input=(object_id + "\n").encode(),
    )
    assert parse_blob_batch(raw, [object_id]) == [
        (repository / "pkg" / "with space.py").read_bytes()
    ]
    header, rest = raw.split(b"\n", 1)
    parts = header.split()
    if fault == "header":
        raw = b"incomplete"
    elif fault == "identity":
        raw = b"0" * 40 + b" " + b" ".join(parts[1:]) + b"\n" + rest
    elif fault == "type":
        raw = parts[0] + b" tree " + parts[2] + b"\n" + rest
    elif fault == "negative":
        raw = b" ".join(parts[:2]) + b" -1\n" + rest
    elif fault == "size":
        raw = b" ".join(parts[:2]) + b" 999999999\n" + rest
    elif fault == "newline":
        raw = header + b"\n" + rest[:-1]
    elif fault == "trailing":
        raw += b"extra"
    else:
        assert fault == "number"
        raw = b" ".join(parts[:2]) + b" invalid\n" + rest
    with pytest.raises(ValueError):
        parse_blob_batch(raw, [object_id])
