# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real authenticated owner-profile retirement
"""Exercise lock cleanup while a prior same-owner profile is retiring."""

from __future__ import annotations

import asyncio
import logging
import secrets
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import run_cli
from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore


@pytest.mark.asyncio
@pytest.mark.parametrize("retire_holder", [False, True])
async def test_cleanup_respects_a_live_authenticated_name_holder(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, retire_holder: bool
) -> None:
    """Retry registration after actual retirement, never take over a live profile."""
    owner = "cleanup-profile/owner"
    token = secrets.token_urlsafe(32)
    started, finish = tmp_path / "child.started", tmp_path / "child.finish"
    child = (
        "import sys,time\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).touch()\n"
        "while not Path(sys.argv[2]).exists():\n    time.sleep(0.01)\n"
    )
    journal = EventStore(tmp_path / "hub.db")
    caplog.set_level(logging.INFO, logger="synapse.hub")
    try:
        async with running_hub(
            SynapseHub(journal=journal, authenticator=TokenAuthenticator({token: [owner]}))
        ) as (hub, uri):
            operation = asyncio.create_task(
                asyncio.to_thread(
                    run_cli,
                    "lock",
                    "profile-mutex",
                    "--name",
                    owner,
                    "--ready-timeout",
                    "3",
                    "--",
                    sys.executable,
                    "-c",
                    child,
                    str(started),
                    str(finish),
                    uri=uri,
                    env={"SYNAPSE_TOKEN": token},
                    timeout=15,
                )
            )
            deadline = asyncio.get_running_loop().time() + 8
            while not started.exists() or owner in hub.clients.agent_sockets:
                assert not operation.done()
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            holder = SynapseAgent(owner, uri=uri, token=token, verbose=False)
            listener = asyncio.create_task(holder.connect())
            try:
                assert await holder.wait_until_ready(3)
                epoch = hub.state.claims["profile-mutex"].epoch
                finish.touch()
                conflict = f"name conflict sender={owner} "
                while not any(conflict in row.getMessage() for row in caplog.records):
                    assert not operation.done()
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.01)
                if retire_holder:
                    holder.running = False
                    listener.cancel()
                    await asyncio.gather(listener, return_exceptions=True)
                result = await asyncio.wait_for(operation, 10)
                assert result.returncode == (0 if retire_holder else 3), result.output
                releases = [row for row in journal.iter_events() if row.kind == "release"]
                assert len(releases) == (1 if retire_holder else 0)
                if retire_holder:
                    assert "profile-mutex" not in hub.state.claims
                else:
                    assert hub.state.claims["profile-mutex"].epoch == epoch
                    assert owner in hub.clients.agent_sockets
                    assert not listener.done()
                    assert holder.connection is not None
            finally:
                finish.touch()
                holder.running = False
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)
                await asyncio.wait_for(operation, 15)
    finally:
        journal.close()
