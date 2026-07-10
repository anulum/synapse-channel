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
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import read_json, read_until_type, running_hub, send_json
from synapse_channel.core.hub import SynapseHub

NAME = "PROJ/agent-under-siege"


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
