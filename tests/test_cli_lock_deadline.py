# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native lease deadline against a busy owner profile
"""A long readiness setting cannot let a command outrun its confirmed lease."""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import time
from pathlib import Path

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub


async def test_lease_deadline_overrides_a_long_owner_readiness_timeout(tmp_path: Path) -> None:
    token = secrets.token_urlsafe(32)
    name = "deadline/owner"
    finish = tmp_path / "finish"
    root = Path(__file__).resolve().parent.parent
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=os.pathsep.join((str(root / "src"), str(root / "tests"))),
        SYN_HOME=str(tmp_path / "home"),
        SYNAPSE_TOKEN=token,
        SYNAPSE_TOKEN_FILE="",
    )
    child = (
        "from pathlib import Path;import sys,time\nprint('STARTED',flush=True)\n"
        "while not Path(sys.argv[1]).exists(): time.sleep(.01)\n"
        "print('CHILD_FINISHED',flush=True)"
    )
    async with running_hub(
        SynapseHub(
            default_ttl_seconds=30,
            authenticator=TokenAuthenticator({token: [name]}),
        )
    ) as (hub, uri):
        command = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "lock",
            "deadline",
            "--name",
            name,
            "--uri",
            uri,
            "--ready-timeout",
            "30",
            "--release-timeout",
            "1",
            "--",
            sys.executable,
            "-c",
            child,
            str(finish),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        holder = None
        try:
            assert command.stdout is not None and command.stderr is not None
            error_stream = command.stderr
            assert await asyncio.wait_for(command.stdout.readline(), 10) == b"STARTED\n"
            original_expiry = hub.state.claims["deadline"].lease_expires_at
            holder = await connect_agent(name, uri, token=token)

            async def observe_stop() -> bytes:
                while True:
                    line = await error_stream.readline()
                    assert line, "supervisor exited without reporting its deadline outcome"
                    if b"stopping the owned command" in line:
                        return line

            status = await asyncio.wait_for(observe_stop(), 30)
            assert b"lease unknown" in status
            assert time.time() < original_expiry
            await close_agents(holder)
            holder = None
            stdout, stderr = await asyncio.wait_for(command.communicate(), 10)
            assert command.returncode == 3, (status + stdout + stderr).decode()
            assert b"CHILD_FINISHED" not in stdout
            assert "deadline" not in hub.state.claims
        finally:
            if holder is not None:
                await close_agents(holder)
            finish.touch()
            if command.returncode is None:
                command.kill()
            await command.communicate()
