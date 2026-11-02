# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a child bound to its parent never outlives it
"""Bind real child processes to their parents and watch them stop with them."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import synapse_channel
from synapse_channel.parent_binding import bind_to_parent, parent_alive

_PACKAGE_ROOT = str(Path(synapse_channel.__file__).resolve().parents[1])


def _environment() -> dict[str, str]:
    """Return a child environment that imports the package under test."""
    inherited = os.environ.get("PYTHONPATH", "")
    return dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (_PACKAGE_ROOT, inherited) if p))


def test_no_binding_and_the_current_parent_are_alive() -> None:
    """Without a requested parent nothing is bound; the real parent is recognised."""
    assert parent_alive(None)
    assert bind_to_parent(None)
    assert parent_alive(os.getppid())
    assert not parent_alive(os.getppid() + 1_000_000)


def test_binding_to_a_process_that_is_not_the_parent_reports_it() -> None:
    """A child told the wrong parent learns at once that it must stop."""
    code = (
        "import os\n"
        "from synapse_channel.parent_binding import bind_to_parent\n"
        "print(bind_to_parent(os.getppid()), bind_to_parent(os.getppid() + 1_000_000))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
        env=_environment(),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "False"]


def test_a_bound_child_stops_when_its_parent_exits(tmp_path: Path) -> None:
    """The child ends with its parent: by the kernel signal on Linux, by polling elsewhere.

    The child records how it ended; either way it must end within seconds of the
    parent's exit instead of running on as an orphan.
    """
    marker = tmp_path / "ended"
    child = (
        "import signal, sys, time\n"
        "from pathlib import Path\n"
        "from synapse_channel.parent_binding import bind_to_parent, parent_alive\n"
        "marker = Path(sys.argv[2])\n"
        "def stopped(*_):\n"
        "    marker.write_text('signal')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stopped)\n"
        "parent = int(sys.argv[1])\n"
        "print(bind_to_parent(parent), flush=True)\n"
        "while parent_alive(parent):\n"
        "    time.sleep(0.05)\n"
        "marker.write_text('poll')\n"
    )
    parent = (
        "import os, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', sys.argv[1], str(os.getpid()),"
        " sys.argv[2]], stdout=subprocess.PIPE, text=True)\n"
        "print(child.stdout.readline().strip(), flush=True)\n"
        "os._exit(0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", parent, child, str(marker)],
        capture_output=True,
        text=True,
        timeout=20,
        env=_environment(),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
    deadline = time.monotonic() + 10
    while not (marker.exists() and marker.read_text()):
        assert time.monotonic() < deadline, "the bound child outlived its parent"
        time.sleep(0.05)
    expected = "signal" if sys.platform.startswith("linux") else "poll"
    assert marker.read_text() == expected


def test_a_refused_parent_death_signal_is_logged_and_the_parent_check_remains(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An invalid signal makes ``prctl`` refuse; the refusal is logged, not hidden."""
    with caplog.at_level(logging.WARNING, logger="synapse_channel.parent_binding"):
        assert bind_to_parent(os.getppid(), signal_number=100_000)
    if sys.platform.startswith("linux"):
        assert "parent-death signal not set" in caplog.text
    else:
        assert caplog.text == ""
