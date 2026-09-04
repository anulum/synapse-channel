# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — service command timeout and process cleanup tests
"""Exercise real command processes and pipe-holding descendants."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from _platform_caps import requires_proc
from synapse_channel.waker_commands import command_timeout, run_waker_command


@pytest.mark.parametrize("value", [True, False, 0, -1, float("nan"), float("inf")])
def test_invalid_wait_is_rejected_before_launch(value: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        run_waker_command(["not-executed"], capture_output=True, text=True, timeout=value)


def test_capture_and_checked_exit_use_real_process() -> None:
    args = [
        sys.executable,
        "-c",
        "import sys; print('out'); print('err', file=sys.stderr); sys.exit(4)",
    ]
    result = run_waker_command(args, capture_output=True, text=True, timeout=3)
    assert (result.returncode, result.stdout, result.stderr) == (4, "out\n", "err\n")
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run_waker_command(args, capture_output=True, text=True, check=True, timeout=3)
    assert failure.value.returncode == 4
    assert command_timeout(0.25) == 0.25


def test_uncaptured_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="captured text"):
        run_waker_command(["not-executed"])


@requires_proc
@pytest.mark.parametrize("leader_exits", [False, True])
def test_timeout_reaps_local_process_group_with_pipe_holding_child(
    tmp_path: Path,
    leader_exits: bool,
) -> None:
    pid_file = tmp_path / "child.pid"
    code = (
        "import os, sys, time; from pathlib import Path; "
        "pid = os.fork(); "
        "Path(sys.argv[1]).write_text(str(os.getpid())) if pid == 0 else None; "
        "os._exit(0) if pid != 0 and sys.argv[2] == 'exit' else None; "
        "time.sleep(20)"
    )
    began = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_waker_command(
            [sys.executable, "-c", code, str(pid_file), "exit" if leader_exits else "wait"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    assert time.monotonic() - began < 5
    pid = int(pid_file.read_text())
    # A dead adopted child may briefly remain a zombie until the host reaper runs.
    deadline = time.monotonic() + 2
    while True:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            state = "gone"
        if state in {"gone", "Z"} or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    assert state in {"gone", "Z"}


def test_timeout_reaps_command_without_descendants() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        run_waker_command(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            capture_output=True,
            text=True,
            timeout=0.1,
        )


def test_process_creation_error_is_not_swallowed(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        run_waker_command([str(tmp_path / "missing")], capture_output=True, text=True)
