# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real fencing-domain and hub restart journeys
"""Exercise cache isolation, ownership transfer and restart through the mutex CLI."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import run_cli
from hub_e2e_helpers import _await_listening, close_agents, connect_agent, running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore

_CACHE_EDIT = """
from pathlib import Path
import sys
files=[p for p in (Path(sys.argv[1])/'synapse'/'lease-epoch').rglob('*') if p.is_file()]
assert len(files)==1
path=files[0]
mode=sys.argv[2]
if mode=='foreign-hub':path.parent.rename(path.parent.with_name('different-hub'))
elif mode=='foreign-task':path.rename(path.with_name('different-task'))
else:path.write_text(str(int(path.read_text())+(-1 if mode=='stale' else 1)),encoding='ascii')
"""

_HANDOFF = """
import asyncio,sys
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.protocol import MessageType
async def main():
    done=asyncio.Event()
    async def receive(frame):
        if frame.get('type')==MessageType.HANDOFF_GRANTED:done.set()
    agent=SynapseAgent('mutex-owner',receive,uri=sys.argv[1],verbose=False)
    listener=asyncio.create_task(agent.connect())
    try:
        assert await agent.wait_until_ready(3)
        await agent.handoff('mutex','next-owner')
        await asyncio.wait_for(done.wait(),3)
    finally:
        agent.running=False
        listener.cancel()
        await asyncio.gather(listener,return_exceptions=True)
asyncio.run(main())
"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cache_state", "expected_exit"),
    [("foreign-hub", 0), ("foreign-task", 0), ("stale", 1), ("ahead", 1)],
)
async def test_epoch_cache_never_crosses_a_domain_or_guesses_a_fence(
    tmp_path: Path, cache_state: str, expected_exit: int
) -> None:
    """Foreign cache entries are ignored; actual wrong fences are refused unchanged."""
    journal = EventStore(tmp_path / "hub.db")
    data_home = tmp_path / "data"
    try:
        async with running_hub(SynapseHub(journal=journal, require_fencing_epoch=True)) as (
            hub,
            uri,
        ):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "mutex",
                "--name",
                "mutex-owner",
                "--",
                sys.executable,
                "-c",
                _CACHE_EDIT,
                str(data_home),
                cache_state,
                uri=uri,
                env={"XDG_DATA_HOME": str(data_home)},
            )
            assert result.returncode == expected_exit, result.output
            releases = [row for row in journal.iter_events() if row.kind == "release"]
            if expected_exit:
                assert "lock: release refused" in result.stderr
                assert hub.state.claims["mutex"].owner == "mutex-owner"
                assert not releases
            else:
                assert not hub.state.claims
                assert len(releases) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_child_handoff_preserves_the_next_owners_claim(tmp_path: Path) -> None:
    """An actual child transfer prevents outer cleanup from releasing its successor."""
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal, require_fencing_epoch=True)) as (
            hub,
            uri,
        ):
            successor = await connect_agent("next-owner", uri)
            try:
                result = await asyncio.to_thread(
                    run_cli,
                    "lock",
                    "mutex",
                    "--name",
                    "mutex-owner",
                    "--",
                    sys.executable,
                    "-c",
                    _HANDOFF,
                    uri,
                    uri=uri,
                )
                assert result.returncode == 1, result.output
                assert "lock: release refused" in result.stderr
                assert hub.state.claims["mutex"].owner == "next-owner"
                assert not any(row.kind == "release" for row in journal.iter_events())
            finally:
                await close_agents(successor)
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_durable_hub_restart_during_child_retains_and_releases_the_claim(
    tmp_path: Path,
) -> None:
    """A fresh server restores the durable claim before the held command exits."""
    journal = EventStore(tmp_path / "hub.db")
    ready, finish = tmp_path / "ready", tmp_path / "finish"
    child = (
        "from pathlib import Path; import sys,time; Path(sys.argv[1]).touch(); "
        "deadline=time.monotonic()+15\n"
        "while not Path(sys.argv[2]).exists():\n"
        " assert time.monotonic()<deadline\n time.sleep(0.01)\n"
    )
    context = running_hub(SynapseHub(hub_id="restart-test", journal=journal))
    hub, uri = await context.__aenter__()
    closed = False
    server: asyncio.Task[None] | None = None
    operation = asyncio.create_task(
        asyncio.to_thread(
            run_cli,
            "lock",
            "mutex",
            "--name",
            "mutex-owner",
            "--",
            sys.executable,
            "-c",
            child,
            str(ready),
            str(finish),
            uri=uri,
            timeout=25,
        )
    )
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while not ready.exists():
            assert not operation.done()
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        epoch = hub.state.claims["mutex"].epoch
        await context.__aexit__(None, None, None)
        closed = True
        replacement = SynapseHub(hub_id="restart-test", journal=journal)
        assert replacement.state.claims["mutex"].epoch == epoch
        port = int(uri.rsplit(":", 1)[1])
        server = asyncio.create_task(replacement.serve("localhost", port))
        await _await_listening(port)
        finish.touch()
        result = await asyncio.wait_for(operation, 25)
        assert result.returncode == 0, result.output
        assert not replacement.state.claims
        assert len([row for row in journal.iter_events() if row.kind == "release"]) == 1
    finally:
        finish.touch()
        await asyncio.gather(operation, return_exceptions=True)
        if server is not None:
            server.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server
        if not closed:
            await context.__aexit__(None, None, None)
        journal.close()
