# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — complete editor-driver process-group supervision
"""Verify the outer editor runner bounds and cleans the complete process tree."""

from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from e2e.opencode_editors.editor_process_runner import (
    PROCESS_GROUP_SUPERVISION_ENV,
    run_isolated_editor_command,
)
from e2e.opencode_editors.process_group import PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("command", "timeout", "message"),
    [
        ((), 30.0, "non-empty string arguments"),
        (("python", ""), 30.0, "non-empty string arguments"),
        (("python",), 20.0, "reserve the complete"),
        (("python",), math.inf, "reserve the complete"),
    ],
)
def test_editor_runner_rejects_unbounded_inputs(
    tmp_path: Path,
    command: tuple[str, ...],
    timeout: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        run_isolated_editor_command(command, cwd=tmp_path, env={}, timeout=timeout)


def test_editor_runner_sets_supervision_marker_and_captures_status(tmp_path: Path) -> None:
    script = (
        "import os,sys; "
        f"print(os.environ[{PROCESS_GROUP_SUPERVISION_ENV!r}]); "
        "print('diagnostic', file=sys.stderr); sys.exit(7)"
    )
    completed = run_isolated_editor_command(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=dict(os.environ),
        timeout=PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS + 5.0,
    )
    assert completed.returncode == 7
    assert completed.stdout == "1\n"
    assert completed.stderr == "diagnostic\n"


def test_editor_runner_kills_a_surviving_descendant_after_leader_exit(
    tmp_path: Path,
) -> None:
    script = (
        "import subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "print(child.pid, flush=True)"
    )
    completed = run_isolated_editor_command(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=dict(os.environ),
        timeout=PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS + 5.0,
    )
    child_pid = int(completed.stdout.strip())
    deadline = time.monotonic() + 2.0
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not Path(f"/proc/{child_pid}").exists()


def test_editor_runner_cleans_group_before_reporting_timeout(tmp_path: Path) -> None:
    timeout = PROCESS_GROUP_CLEANUP_TIMEOUT_SECONDS + 0.2
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        run_isolated_editor_command(
            [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(60)"],
            cwd=tmp_path,
            env=dict(os.environ),
            timeout=timeout,
        )
    assert raised.value.timeout == timeout
    assert isinstance(raised.value.output, str)
