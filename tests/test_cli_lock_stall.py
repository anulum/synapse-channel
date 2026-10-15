# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native paused-supervisor controls
"""Use actual OS process suspension, real lease durations and the public CLI."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from test_cli_lock_supervision import renewal_proxy


@pytest.mark.skipif(os.name == "nt", reason="SIGSTOP/SIGCONT are POSIX process controls")
@pytest.mark.parametrize("phase", ["admission", "running"])
async def test_paused_supervisor_refuses_exhausted_confirmed_lifetime(
    tmp_path: Path, phase: str
) -> None:
    started, finish, granted = (tmp_path / name for name in ("started", "finish", "granted"))
    child = (
        "import sys,time;from pathlib import Path\n"
        "Path(sys.argv[1]).touch()\n"
        "while not Path(sys.argv[2]).exists(): time.sleep(.01)\n"
        "print('CHILD_FINISHED',flush=True)"
    )
    root = Path(__file__).resolve().parent.parent
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=os.pathsep.join((str(root / "src"), str(root / "tests"))),
        SYN_HOME=str(tmp_path / "home"),
        SYNAPSE_TOKEN="",
        SYNAPSE_TOKEN_FILE="",
    )
    async with running_hub(SynapseHub(default_ttl_seconds=30)) as (_, uri):
        async with renewal_proxy(
            uri, "admission-delay" if phase == "admission" else "pass", gate=granted
        ) as proxy:
            command = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "synapse_channel.cli",
                "lock",
                "paused",
                "--name",
                "paused-owner",
                "--uri",
                proxy,
                "--release-timeout",
                "1",
                "--ready-timeout",
                "1",
                "--",
                sys.executable,
                "-c",
                child,
                str(started),
                str(finish),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            paused = False
            try:
                marker = granted if phase == "admission" else started
                deadline = asyncio.get_running_loop().time() + 10
                while not marker.exists():
                    assert command.returncode is None
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.005)
                os.kill(command.pid, signal.SIGSTOP)
                paused = True
                await asyncio.sleep(26)
                os.kill(command.pid, signal.SIGCONT)
                paused = False
                stdout, stderr = await asyncio.wait_for(command.communicate(), 10)
                assert command.returncode == 3, (stdout + stderr).decode()
                assert b"CHILD_FINISHED" not in stdout
                assert started.exists() == (phase == "running")
                assert (
                    b"insufficient remaining lifetime" if phase == "admission" else b"lease unknown"
                ) in stderr
            finally:
                if paused:
                    os.kill(command.pid, signal.SIGCONT)
                finish.touch()
                if command.returncode is None:
                    command.kill()
                await command.communicate()
