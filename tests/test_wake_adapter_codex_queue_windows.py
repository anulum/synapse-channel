# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native-queue wake through a Windows command
"""Start the queue command as a suspended job, then let it answer.

The shared stand-in is a shebang script. CreateProcess does not run that.
This test uses a ``.cmd`` Windows can start.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from synapse_channel.wake_adapters import ACCEPTED, WakeTarget
from synapse_channel.wake_adapters.codex_queue import PROVES, CodexQueueAdapter

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="a suspended job object is a Windows process",
)

_SESSION = "01a0fcac-3407-71c3-9081-0bff345ef6c2"
_HOST = "this-host"
_TARGET = WakeTarget("PROJECT/codex-aaaa", _SESSION, _HOST)


def test_a_cmd_queue_is_owned_and_accepted(tmp_path: Path) -> None:
    """A command Windows can start is accepted when it prints the queued line."""
    program = tmp_path / "codex.cmd"
    program.write_text(
        "@echo off\r\n"
        "echo Queued message 01a10909-405b-7471-86b2-3a44cd8bf3bc for thread %3\r\n"
        "exit /b 0\r\n",
        encoding="utf-8",
    )
    adapter = CodexQueueAdapter(program, host=_HOST)

    outcome = adapter.wake(_TARGET, 1, "fixed prompt", 20.0)

    assert outcome.state == ACCEPTED
    assert outcome.proves == PROVES
    assert outcome.detail == "queued message 01a10909-405b-7471-86b2-3a44cd8bf3bc"
