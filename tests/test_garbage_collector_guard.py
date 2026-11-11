# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - the garbage collector guard and the child mypy run
"""A test that reconfigures the collector fails, and contract mypy runs leave it alone."""

from __future__ import annotations

import gc
import os
import subprocess
import sys
from pathlib import Path

import pytest

from garbage_collector_guard import garbage_collector_unchanged
from mypy_process import run_mypy

TESTS = Path(__file__).resolve().parent


def test_an_unchanged_collector_passes() -> None:
    """Ordinary work inside the guard is not reported."""
    with garbage_collector_unchanged():
        gc.collect()


def test_raised_thresholds_fail_and_are_restored() -> None:
    """The mypy build's thresholds are reported, and later tests get the original ones."""
    original = gc.get_threshold()
    with pytest.raises(pytest.fail.Exception, match="changed the garbage collector"):
        with garbage_collector_unchanged():
            gc.set_threshold(200 * 1000, 30, 30)
    assert gc.get_threshold() == original


def test_disabled_collection_fails_and_is_enabled_again() -> None:
    """Turning collection off is a change too."""
    assert gc.isenabled()
    with pytest.raises(pytest.fail.Exception, match=r"\(\(.*\), True\) to \(\(.*\), False\)"):
        with garbage_collector_unchanged():
            gc.disable()
    assert gc.isenabled()


def test_a_block_that_enables_collection_restores_it_disabled() -> None:
    """The guard restores whatever state it found, including disabled collection."""
    gc.disable()
    try:
        with pytest.raises(pytest.fail.Exception, match="changed the garbage collector"):
            with garbage_collector_unchanged():
                gc.enable()
        assert not gc.isenabled()
    finally:
        gc.enable()


def test_a_child_mypy_run_reports_and_leaves_the_collector_alone() -> None:
    """The child returns mypy's own output and status; this process keeps its thresholds."""
    original = gc.get_threshold()
    stdout, stderr, status = run_mypy(["--version"])
    assert (status, stderr) == (0, "")
    assert stdout.startswith("mypy ")
    assert gc.get_threshold() == original


def test_a_session_with_the_shared_conftest_fails_the_test_that_raised_the_thresholds(
    tmp_path: Path,
) -> None:
    """A real pytest session loading the shared conftest reports the changing test.

    The probe test passes its own call phase; the guard turns its teardown into
    an error that names the leaked thresholds.
    """
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "import gc\n\n\ndef test_raises_thresholds() -> None:\n"
        "    gc.set_threshold(200 * 1000, 30, 30)\n",
        encoding="utf-8",
    )
    paths = [str(TESTS), *filter(None, [os.environ.get("PYTHONPATH")])]
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-p",
            "conftest",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-q",
            str(probe),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(paths)},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "to ((200000, 30, 30), True); run the code that changes it" in result.stdout
