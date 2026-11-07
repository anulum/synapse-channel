# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a locked command survives the cancellation of its own cleanup
"""Cancelling every task, the cleanup included, still reaps the locked command."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from synapse_channel.cli_lock_process import run_locked_subprocess

_RECORD_PID_AND_WAIT = (
    "import os, pathlib, sys, time\n"
    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
    "time.sleep(60)\n"
)


async def _cancel_the_command_and_its_cleanup(pid_file: Path) -> None:
    command = [sys.executable, "-c", _RECORD_PID_AND_WAIT, str(pid_file)]
    locked = asyncio.create_task(run_locked_subprocess(command))
    deadline = time.monotonic() + 10
    while not pid_file.exists() or not pid_file.read_text(encoding="utf-8"):
        assert time.monotonic() < deadline, "the locked command did not start"
        await asyncio.sleep(0.01)
    locked.cancel()
    await asyncio.sleep(0)
    # The cancelled command has scheduled its cleanup; cancel that too, as a
    # handler that cancels every task would, before the cleanup ever runs.
    cleanup = asyncio.all_tasks() - {locked, asyncio.current_task()}
    assert len(cleanup) == 1
    cleanup.pop().cancel()
    with pytest.raises(asyncio.CancelledError):
        await locked


def test_a_cancelled_cleanup_still_terminates_and_reaps_the_command(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    started = time.monotonic()
    asyncio.run(_cancel_the_command_and_its_cleanup(pid_file))
    assert time.monotonic() - started < 10
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text(encoding="utf-8")), 0)
