# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real native coverage shard aggregation
"""Combine actual coverage databases with and without assigned native observations."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import coverage
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools/combine_native_coverage.sh"


def _run(root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    """Use the current interpreter's coverage tool with an isolated configuration."""
    env = dict(
        os.environ,
        PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        COVERAGE_RCFILE=str(root / "coverage.ini"),
        COVERAGE_FILE=str(root / ".coverage"),
    )
    return subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=15)


def _project(root: Path, native_count: int = 0) -> list[Path]:
    """Create two independently measured subprocess surfaces for the real combiner."""
    (root / "coverage.ini").write_text("[run]\nbranch = true\n")
    (root / "main.py").write_text("print('main observation')\n")
    (root / "native.py").write_text("print('native subprocess observation')\n")
    (root / "native-kernel-coverage").mkdir()
    nodes = [
        "tests/fixtures/protected_write_mounts_linux.py::"
        "test_real_private_namespace_mount_policy[" + case + "]"
        for case in ("accepted", "base-writable")[:native_count]
    ]
    (root / "shard-main.json").write_text(
        json.dumps({"success": True, "selected": ["tests/test_ordinary.py::test_observed", *nodes]})
    )
    directories = [
        root / "native-kernel-coverage" / hashlib.sha256(node.encode("utf-8")).hexdigest()
        for node in nodes
    ]
    for directory in directories:
        directory.mkdir()
    result = _run(root, [sys.executable, "-m", "coverage", "run", "main.py"])
    assert result.returncode == 0, result.stderr
    return directories


@pytest.mark.parametrize("native_count", [0, 1, 2])
def test_combiner_preserves_main_and_all_actual_native_observations(
    tmp_path: Path, native_count: int
) -> None:
    """Empty native assignment succeeds; every produced native database is consumed."""
    directories = _project(tmp_path, native_count)
    for directory in directories:
        result = _run(
            tmp_path,
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--data-file={directory / '.coverage.observed'}",
                "native.py",
            ],
        )
        assert result.returncode == 0, result.stderr
    result = _run(tmp_path, ["bash", str(SCRIPT)])
    assert result.returncode == 0, result.stdout + result.stderr
    data = coverage.CoverageData(basename=str(tmp_path / ".coverage"))
    data.read()
    assert data.lines(str(tmp_path / "main.py")) == [1]
    assert data.lines(str(tmp_path / "native.py")) == ([1] if native_count else None)
    assert all(not list(directory.iterdir()) for directory in directories)


@pytest.mark.parametrize("missing", ["main", "native_directory", "corrupt_native"])
def test_combiner_refuses_missing_or_corrupt_mandatory_inputs(tmp_path: Path, missing: str) -> None:
    """Optional native assignment never excuses a missing main database or bad data."""
    directories = _project(tmp_path, int(missing == "corrupt_native"))
    if missing == "main":
        (tmp_path / ".coverage").unlink()
    elif missing == "native_directory":
        (tmp_path / "native-kernel-coverage").rmdir()
    else:
        (directories[0] / ".coverage.corrupt").write_bytes(b"not sqlite")
    result = _run(tmp_path, ["bash", str(SCRIPT)])
    assert result.returncode != 0, result.stdout + result.stderr


@pytest.mark.parametrize("expected", [1, 2])
def test_combiner_refuses_absent_observations_from_selected_native_writers(
    tmp_path: Path, expected: int
) -> None:
    """A successful selected native case must supply its subprocess coverage database."""
    directories = _project(tmp_path, expected)
    for directory in directories[: expected - 1]:
        result = _run(
            tmp_path,
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--data-file={directory / '.coverage.observed'}",
                "native.py",
            ],
        )
        assert result.returncode == 0, result.stderr
    before = (tmp_path / ".coverage").read_bytes()
    result = _run(tmp_path, ["bash", str(SCRIPT)])
    assert result.returncode == 1
    assert "missing native subprocess coverage" in result.stderr
    assert (tmp_path / ".coverage").read_bytes() == before


def test_extra_observations_from_one_case_cannot_mask_another_missing_case(tmp_path: Path) -> None:
    """Two databases from the first case never replace the second case's observations."""
    directories = _project(tmp_path, 2)
    for index in range(2):
        result = _run(
            tmp_path,
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                f"--data-file={directories[0] / ('.coverage.' + str(index))}",
                "native.py",
            ],
        )
        assert result.returncode == 0, result.stderr
    before = (tmp_path / ".coverage").read_bytes()
    result = _run(tmp_path, ["bash", str(SCRIPT)])
    assert result.returncode == 1
    assert "missing native subprocess coverage for case" in result.stderr
    assert (tmp_path / ".coverage").read_bytes() == before
    assert len(list(directories[0].iterdir())) == 2


@pytest.mark.parametrize("invalid", ["unassigned", "missing", "unsuccessful", "bad_selection"])
def test_combiner_refuses_unbound_or_invalid_case_receipts(tmp_path: Path, invalid: str) -> None:
    """Unexpected observations and unsuccessful receipts cannot enter the main database."""
    _project(tmp_path)
    receipt = tmp_path / "shard-main.json"
    if invalid == "unassigned":
        (tmp_path / "native-kernel-coverage/unassigned").mkdir()
    elif invalid == "missing":
        receipt.unlink()
    elif invalid == "unsuccessful":
        receipt.write_text(json.dumps({"success": False, "selected": []}))
    else:
        receipt.write_text(json.dumps({"success": True, "selected": [17]}))
    before = (tmp_path / ".coverage").read_bytes()
    result = _run(tmp_path, ["bash", str(SCRIPT)])
    assert result.returncode != 0
    assert (tmp_path / ".coverage").read_bytes() == before
