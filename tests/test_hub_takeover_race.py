# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — takeover atomicity under concurrency, over real sockets

"""The takeover swap must be atomic from every other task's point of view.

An accepted takeover evicts the current owner socket with a real close
handshake — an await. These tests pin the invariant that no interleaved task
can ever observe the evicted socket through ``agent_sockets`` or co-claim the
name during that await: the registry rebinds both maps to the new owner
*before* the eviction close suspends (swap-then-close). Everything here runs
against a live hub with real websocket connections; the only reach into the
hub object is to read its registry maps, which are exactly what the invariant
is about.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import read_json, read_until_type, running_hub, send_json
from synapse_channel.core.hub import SynapseHub

NAME = "PROJ/agent-under-siege"

_CHILD_RACER = f"""
import asyncio
import json
import sys
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

NAME = {NAME!r}

async def main():
    uri, ready_value, gate_value, done_value = sys.argv[1:]
    ready = Path(ready_value)
    gate = Path(gate_value)
    done = Path(done_value)
    async with connect(uri) as websocket:
        await websocket.recv()
        ready.touch()
        while not gate.exists():
            await asyncio.sleep(0.005)
        await websocket.send(json.dumps({{
            "type": "heartbeat",
            "sender": NAME,
            "takeover": True,
        }}))
        try:
            while True:
                await asyncio.wait_for(websocket.recv(), timeout=1.5)
        except (TimeoutError, asyncio.TimeoutError):
            ready.with_suffix(".survivor").touch()
            while not done.exists():
                await asyncio.sleep(0.005)
            return 0
        except ConnectionClosed as exc:
            return 10 if exc.rcvd is not None and exc.rcvd.code == 4010 else 11

raise SystemExit(asyncio.run(main()))
"""


async def _await_bound(hub: SynapseHub, name: str, *, timeout: float = 3.0) -> Any:
    """Poll the live registry until ``name`` is bound; return its socket.

    Parameters
    ----------
    hub : SynapseHub
        The in-process hub under test.
    name : str
        The agent name whose binding to wait for.
    timeout : float, optional
        Seconds to keep polling before failing the test.

    Returns
    -------
    Any
        The server-side socket ``name`` resolved to.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        websocket = hub.clients.agent_sockets.get(name)
        if websocket is not None:
            return websocket
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} did not bind on the hub")


async def _await_unbound_socket(hub: SynapseHub, *, clients: int, timeout: float = 3.0) -> Any:
    """Poll until ``clients`` sockets are connected; return the nameless one.

    Parameters
    ----------
    hub : SynapseHub
        The in-process hub under test.
    clients : int
        The total number of connected sockets to wait for.
    timeout : float, optional
        Seconds to keep polling before failing the test.

    Returns
    -------
    Any
        The one connected server-side socket that has not bound a name.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if len(hub.clients.connected_clients) >= clients:
            nameless = [
                websocket
                for websocket in hub.clients.connected_clients
                if websocket not in hub.clients.socket_agent
            ]
            if len(nameless) == 1:
                return nameless[0]
        await asyncio.sleep(0.01)
    raise TimeoutError("the unbound socket did not appear on the hub")


async def _await_files(paths: tuple[Path, ...], *, timeout: float = 3.0) -> None:
    """Wait until every child process has reached its on-disk start barrier."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if all(path.is_file() for path in paths):
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("takeover child processes did not reach the race barrier")


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    """Bound cleanup of a child that outlived a failed race assertion."""
    if process.returncode is not None:
        return
    process.kill()
    await process.wait()


async def _await_process_race(
    hub: SynapseHub,
    processes: tuple[asyncio.subprocess.Process, ...],
    survivor_paths: tuple[Path, ...],
    *,
    timeout: float = 4.0,
) -> None:
    """Wait for one superseded child and one still-connected survivor."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    stable_since: float | None = None
    last_observation = "race not observed"
    while loop.time() < deadline:
        superseded = sum(process.returncode == 10 for process in processes)
        survivor_markers = tuple(path.is_file() for path in survivor_paths)
        survivors = sum(survivor_markers)
        bound = [socket for socket, name in hub.clients.socket_agent.items() if name == NAME]
        bijective = len(bound) == 1 and hub.clients.agent_sockets.get(NAME) is bound[0]
        last_observation = (
            f"returncodes={tuple(process.returncode for process in processes)!r}, "
            f"survivor_markers={survivor_markers!r}, bound={len(bound)}, "
            f"bijective={bijective}"
        )
        if superseded == 1 and survivors == 1 and bijective:
            if stable_since is None:
                stable_since = loop.time()
            elif loop.time() - stable_since >= 0.1:
                return
        else:
            stable_since = None
        await asyncio.sleep(0.01)
    raise TimeoutError(
        f"takeover processes did not settle a stable one-owner bijection: {last_observation}"
    )


async def _drain_until_closed_or_quiet(websocket: Any, *, window: float = 0.8) -> str:
    """Read a socket until the hub closes it or it goes quiet; classify which.

    Parameters
    ----------
    websocket : Any
        The client-side connection to drain.
    window : float, optional
        Per-read timeout; a read that outlives it means the socket stayed open.

    Returns
    -------
    str
        ``"superseded"`` when the hub closed the socket with code 4010,
        ``"open"`` when it is still alive with nothing more to say.
    """
    try:
        while True:
            await read_json(websocket, timeout=window)
    except (TimeoutError, asyncio.TimeoutError):
        return "open"
    except ConnectionClosed as exc:
        received = exc.rcvd
        assert received is not None and received.code == 4010
        return "superseded"


async def test_the_name_switches_to_the_new_owner_before_the_eviction_completes() -> None:
    """While the eviction close is in flight, the map already names the newcomer.

    This drives the real registry method on real server-side sockets and checks
    the window itself: one scheduler step after the takeover starts — with the
    eviction close handshake still pending — the name must already resolve to
    the new socket, never to the evicted one.
    """
    async with running_hub(SynapseHub(hub_id="syn-race")) as (hub, uri):
        async with connect(uri) as victim, connect(uri) as challenger:
            await read_json(victim)  # welcome
            await read_json(challenger)  # welcome
            await send_json(victim, sender=NAME, type="heartbeat")
            victim_ws = await _await_bound(hub, NAME)
            challenger_ws = await _await_unbound_socket(hub, clients=2)

            takeover = asyncio.create_task(
                hub.clients.resolve_sender(
                    NAME,
                    challenger_ws,
                    takeover=True,
                    send_json=hub._send_json,
                    system=hub._system,
                )
            )
            await asyncio.sleep(0)  # run the takeover up to the eviction await

            assert hub.clients.agent_sockets[NAME] is challenger_ws
            assert hub.clients.socket_agent.get(challenger_ws) == NAME
            assert hub.clients.socket_agent.get(victim_ws) is None
            assert await takeover == NAME
            # the settled state agrees with what the window already promised
            assert hub.clients.agent_sockets[NAME] is challenger_ws


async def test_racing_takeovers_leave_exactly_one_live_owner() -> None:
    """Two sockets storming one name concurrently must never co-bind it.

    Before the swap-then-close fix, the second takeover read the not-yet-swapped
    map at the eviction await, evicted the same stale owner again, and both
    challengers ended up bound — two live sockets holding one name. The whole
    exchange here goes through the real wire protocol; the end state must be a
    bijection: one challenger superseded, the other the sole owner, and a
    directed message reaches exactly that survivor.
    """
    hub = SynapseHub(hub_id="syn-race", takeover_cooldown=0.0)
    async with running_hub(hub) as (_, uri):
        async with (
            connect(uri) as victim,
            connect(uri) as first,
            connect(uri) as second,
        ):
            await read_json(victim)  # welcome
            await read_json(first)  # welcome
            await read_json(second)  # welcome
            await send_json(victim, sender=NAME, type="heartbeat")
            await _await_bound(hub, NAME)

            await asyncio.gather(
                send_json(first, sender=NAME, type="heartbeat", takeover=True),
                send_json(second, sender=NAME, type="heartbeat", takeover=True),
            )

            assert await _drain_until_closed_or_quiet(victim) == "superseded"
            outcomes = await asyncio.gather(
                _drain_until_closed_or_quiet(first),
                _drain_until_closed_or_quiet(second),
            )
            assert sorted(outcomes) == ["open", "superseded"]
            survivor = first if outcomes[0] == "open" else second

            bound_sockets = [
                websocket for websocket, name in hub.clients.socket_agent.items() if name == NAME
            ]
            assert len(bound_sockets) == 1
            assert hub.clients.agent_sockets[NAME] is bound_sockets[0]

            async with connect(uri) as prober:
                await read_json(prober)  # welcome
                await send_json(
                    prober, sender="PROJ/prober", type="chat", target=NAME, payload="ping"
                )
                delivered = await read_until_type(survivor, "chat")
            assert delivered["payload"] == "ping"
            assert delivered["sender"] == "PROJ/prober"


async def test_stale_handler_cannot_rebind_after_second_takeover(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Pause one resolved handler while a second takeover supersedes it."""
    caplog.set_level(logging.INFO, logger="synapse.hub")
    hub = SynapseHub(hub_id="syn-stale-continuation", takeover_cooldown=0.0)
    async with (
        running_hub(hub) as (_, uri),
        connect(uri) as victim,
        connect(uri) as first,
        connect(uri) as second,
    ):
        await read_json(victim)
        await read_json(first)
        await read_json(second)
        await send_json(victim, sender=NAME, type="heartbeat")
        await _await_bound(hub, NAME)

        original_run = hub.state_mutations.run
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        call_count = 0
        first_result: list[Any] = []

        async def controlled_run(
            state: Any,
            mutate: Any,
            *,
            persist: Any = None,
            publish: Any = None,
        ) -> Any:
            nonlocal call_count
            call_count += 1
            ordinal = call_count
            if ordinal == 1:
                first_entered.set()
                await release_first.wait()
            result = await original_run(state, mutate, persist=persist, publish=publish)
            if ordinal == 1:
                first_result.append(result)
            return result

        monkeypatch.setattr(hub.state_mutations, "run", controlled_run)
        await send_json(first, sender=NAME, type="heartbeat", takeover=True)
        await asyncio.wait_for(first_entered.wait(), timeout=3.0)
        first_server_socket = hub.clients.agent_sockets[NAME]

        await send_json(second, sender=NAME, type="heartbeat", takeover=True)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 3.0
        while hub.clients.agent_sockets.get(NAME) is first_server_socket:
            if loop.time() >= deadline:
                raise TimeoutError("second takeover did not replace the first racer")
            await asyncio.sleep(0.01)
        winner = hub.clients.agent_sockets[NAME]

        release_first.set()
        deadline = loop.time() + 3.0
        while not any(
            "superseded sender continuation dropped" in record.getMessage()
            for record in caplog.records
        ):
            if loop.time() >= deadline:
                raise TimeoutError("stale handler did not reach the continuation guard")
            await asyncio.sleep(0.01)

        assert hub.clients.agent_sockets[NAME] is winner
        assert hub.clients.socket_agent[winner] == NAME
        assert first_server_socket not in hub.clients.socket_agent
        assert first_result == [False]


async def test_two_os_processes_racing_one_name_leave_one_live_owner(tmp_path: Path) -> None:
    """Independent client processes cannot co-bind one identity during takeover.

    Each racer starts a separate Python interpreter and WebSocket connection,
    proves readiness through its own file, and waits on a shared on-disk barrier.
    Releasing the barrier makes both processes request the same identity. One is
    superseded with 4010 and one remains quiet/live; the hub registry stays a
    one-to-one mapping throughout the settled result.
    """
    hub = SynapseHub(hub_id="syn-process-race", takeover_cooldown=0.0)
    ready_paths = (tmp_path / "first.ready", tmp_path / "second.ready")
    survivor_paths = tuple(path.with_suffix(".survivor") for path in ready_paths)
    gate = tmp_path / "go"
    done = tmp_path / "done"
    processes: list[asyncio.subprocess.Process] = []
    try:
        async with running_hub(hub) as (_, uri), connect(uri) as victim:
            await read_json(victim)
            await send_json(victim, sender=NAME, type="heartbeat")
            await _await_bound(hub, NAME)

            for ready in ready_paths:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    _CHILD_RACER,
                    uri,
                    str(ready),
                    str(gate),
                    str(done),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                processes.append(process)
            # Importing the full client stack in two fresh Python 3.10
            # interpreters can exceed the generic three-second in-process
            # polling window on a loaded runner.  This allowance ends before
            # the shared race gate opens; it doesn't relax takeover settling.
            await _await_files(ready_paths, timeout=8.0)
            gate.touch()
            process_pair = (processes[0], processes[1])
            await _await_process_race(hub, process_pair, survivor_paths)
            bound = [socket for socket, name in hub.clients.socket_agent.items() if name == NAME]
            assert len(bound) == 1
            assert hub.clients.agent_sockets[NAME] is bound[0]

            done.touch()
            completed = await asyncio.wait_for(
                asyncio.gather(*(process.communicate() for process in processes)),
                timeout=8.0,
            )
            outcomes = [process.returncode for process in processes]
            diagnostics = [
                "stdout="
                f"{stdout.decode(errors='replace')!r} "
                f"stderr={stderr.decode(errors='replace')!r}"
                for stdout, stderr in completed
            ]
            assert sorted(outcome for outcome in outcomes if outcome is not None) == [0, 10], (
                outcomes,
                diagnostics,
            )
    finally:
        await asyncio.gather(*(_stop_process(process) for process in processes))
