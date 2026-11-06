# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a test that leaves a file open fails under the project settings

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_OUTER_BOUND_SECONDS = 120.0

_PROBE = (
    "import gc\n\n"
    "def test_leaves_a_file_open():\n"
    "    handle = open(__file__, encoding='utf-8')\n"
    "    handle.read(1)\n"
    "    del handle\n"
    "    gc.collect()\n\n"
    "def test_closes_its_file():\n"
    "    with open(__file__, encoding='utf-8') as handle:\n"
    "        handle.read(1)\n"
)


def _run_probe(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """Run the probe in a child pytest that reads this project's pyproject."""
    probe = tmp_path / "test_leak_probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    env = dict(os.environ)
    env.pop("PYTHONWARNINGS", None)
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(probe),
        "-c",
        str(ROOT / "pyproject.toml"),
        "--rootdir",
        str(tmp_path),
        "--noconftest",
        "-p",
        "no:cacheprovider",
        "-q",
        "-rA",
        *extra,
    ]
    return subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=_OUTER_BOUND_SECONDS,
        check=False,
    )


def test_a_test_that_leaves_a_file_open_fails_under_the_project_settings(
    tmp_path: Path,
) -> None:
    """The project's filterwarnings turn an unclosed file into that test's failure.

    The closing test in the same probe still passes, so the failure belongs
    to the leak and not to the child run as a whole.
    """
    result = _run_probe(tmp_path)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "FAILED test_leak_probe.py::test_leaves_a_file_open" in output, output
    assert "PASSED test_leak_probe.py::test_closes_its_file" in output, output
    assert "ResourceWarning" in output, output


def test_without_the_project_filters_the_same_leak_only_warns(tmp_path: Path) -> None:
    """With the filters cleared, the same probe passes: the setting does the work."""
    result = _run_probe(tmp_path, "-o", "filterwarnings=")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "PASSED test_leak_probe.py::test_leaves_a_file_open" in output, output
    assert "PASSED test_leak_probe.py::test_closes_its_file" in output, output
