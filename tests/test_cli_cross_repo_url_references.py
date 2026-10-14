# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real Git reference ambiguity through the cross-repo CLI
"""Exercise unresolved source references without suppressing numeric conflicts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from synapse_channel.cli import main
from synapse_channel.core.version_constraints import NOT_COMPARABLE, compare_constraints


def _git(root: Path, *args: str) -> str:
    """Run a bounded Git command against the test-owned repository."""
    env = dict(os.environ)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(name, None)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(
        [
            "git",
            "-c",
            f"core.hooksPath={root / '.test-hooks'}",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(root),
            *args,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout.strip()


def _source_repository(root: Path) -> Path:
    """Commit two package subdirectories in an isolated real Git repository."""
    root.mkdir()
    (root / ".test-hooks").mkdir()
    for name, version in (("left", "1.0"), ("right", "2.0")):
        directory = root / name
        directory.mkdir()
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "sharedsource"\nversion = "{version}"\n', encoding="utf-8"
        )
    _git(root, "init", "--quiet")
    _git(root, "add", "left", "right")
    _git(
        root,
        "-c",
        "user.name=Synapse fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    return root


@pytest.mark.parametrize(
    ("left_ref", "right_ref", "left_fragment", "right_fragment"),
    [
        ("abcdef1", "fedcba2", "", ""),
        ("a" * 40, "b" * 40, "", ""),
        ("abcdef1", "abcdef1", "#subdirectory=left", "#subdirectory=right"),
    ],
)
def test_unresolved_git_references_preserve_numeric_conflicts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    left_ref: str,
    right_ref: str,
    left_fragment: str,
    right_fragment: str,
) -> None:
    """Hex branches can alias one commit, while fragments select different packages."""
    origin = _source_repository(tmp_path / "origin")
    for name in dict.fromkeys((left_ref, right_ref)):
        _git(origin, "branch", name)
    assert _git(origin, "rev-parse", f"refs/heads/{left_ref}^{{commit}}") == _git(
        origin, "rev-parse", f"refs/heads/{right_ref}^{{commit}}"
    )
    left = f"@ git+{origin.as_uri()}@{left_ref}{left_fragment}"
    right = f"@ git+{origin.as_uri()}@{right_ref}{right_fragment}"
    consumers = tmp_path / "consumers"
    consumers.mkdir()
    for name, reference, numeric in (("alpha", left, ">=2"), ("beta", right, "<2")):
        directory = consumers / name
        directory.mkdir()
        dependencies = json.dumps([f"sharedsource {reference}", f"sharednumeric{numeric}"])
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\ndependencies = {dependencies}\n', encoding="utf-8"
        )
    assert main(["cross-repo", str(consumers), "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    conflicts = [edge for edge in report["edges"] if edge["kind"] == "version_conflict"]
    assert len(conflicts) == 1
    assert "sharednumeric" in conflicts[0]["detail"]
    assert compare_constraints(left, right, "python") == NOT_COMPARABLE
