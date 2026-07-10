# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for the hub's outbound messaging unit

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from hub_e2e_helpers import read_json, running_hub, send_json
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_broadcast import HubBroadcaster
from synapse_channel.core.hub_clients import HubClientRegistry
from synapse_channel.core.hub_relay import RelayMirror
from synapse_channel.core.protocol import MessageType, system_message
from synapse_channel.relay import decode_lite, read_jsonl_since


class _RecordingSocket:
    """A stand-in websocket that records every raw frame sent to it."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        self.sent.append(raw)


def _registry() -> HubClientRegistry:
    return HubClientRegistry(
        max_clients=8,
        max_unauth_clients=None,
        max_connections_per_host=None,
        takeover_cooldown=0.0,
        clock=lambda: 0.0,
    )


def _broadcaster(
    clients: HubClientRegistry,
    relay: RelayMirror,
    *,
    online: list[str] | None = None,
) -> HubBroadcaster:
    return HubBroadcaster(
        clients,
        relay,
        system=lambda payload, **extra: system_message(payload, hub_id="syn-test", **extra),
        online_agents=lambda: list(online or []),
    )


async def test_send_json_serialises_to_a_single_socket() -> None:
    socket = _RecordingSocket()
    broadcaster = _broadcaster(_registry(), RelayMirror(None, 8))

    await broadcaster.send_json(socket, {"type": "chat", "payload": "hi"})

    assert socket.sent == [json.dumps({"type": "chat", "payload": "hi"})]


async def test_broadcast_mirrors_then_fans_out_to_every_client(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    clients = _registry()
    first, second = _RecordingSocket(), _RecordingSocket()
    clients.connected_clients.update({first, second})
    broadcaster = _broadcaster(clients, RelayMirror(log, 8))

    data = {"type": "chat", "sender": "A", "payload": "hello"}
    await broadcaster.broadcast(data)

    raw = json.dumps(data)
    assert first.sent == [raw]
    assert second.sent == [raw]
    events, _ = read_jsonl_since(log, 0)
    assert [decode_lite(event)["payload"] for event in events] == ["hello"]


async def test_broadcast_with_no_clients_still_mirrors(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    broadcaster = _broadcaster(_registry(), RelayMirror(log, 8))

    await broadcaster.broadcast({"type": "chat", "sender": "A", "payload": "solo"})

    events, _ = read_jsonl_since(log, 0)
    assert [decode_lite(event)["payload"] for event in events] == ["solo"]


async def test_broadcast_presence_composes_a_presence_update(tmp_path: Path) -> None:
    clients = _registry()
    socket = _RecordingSocket()
    clients.connected_clients.add(socket)
    broadcaster = _broadcaster(clients, RelayMirror(None, 8), online=["A", "B"])

    await broadcaster.broadcast_presence("join", agent="B")

    sent = json.loads(socket.sent[0])
    assert sent["type"] == MessageType.PRESENCE_UPDATE
    assert sent["online_agents"] == ["A", "B"]
    assert sent["event"] == "join"
    assert sent["agent"] == "B"


async def test_send_to_agent_delivers_and_reports_success() -> None:
    clients = _registry()
    socket = _RecordingSocket()
    clients.agent_sockets["A"] = socket
    broadcaster = _broadcaster(clients, RelayMirror(None, 8))

    delivered = await broadcaster.send_to_agent("A", {"type": "chat", "payload": "x"})

    assert delivered is True
    assert socket.sent == [json.dumps({"type": "chat", "payload": "x"})]


async def test_send_directed_reaches_named_and_sender_not_others(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    clients = _registry()
    broadcaster = _broadcaster(clients, RelayMirror(log, 8))
    sender_sock, beta, gamma = _RecordingSocket(), _RecordingSocket(), _RecordingSocket()
    clients.agent_sockets["BETA"] = beta
    clients.agent_sockets["GAMMA"] = gamma

    await broadcaster.send_directed(
        {"type": "chat", "payload": "hi"}, names=["BETA"], sender_socket=sender_sock
    )

    raw = json.dumps({"type": "chat", "payload": "hi"})
    assert beta.sent == [raw]  # recipient reached
    assert sender_sock.sent == [raw]  # sender echo (wire parity with broadcast)
    assert gamma.sent == []  # uninvolved socket not reached
    events, _ = read_jsonl_since(log, 0)
    assert [decode_lite(event)["payload"] for event in events] == ["hi"]  # still mirrored


async def test_send_directed_deduplicates_a_socket_named_twice() -> None:
    clients = _registry()
    broadcaster = _broadcaster(clients, RelayMirror(None, 8))
    beta = _RecordingSocket()
    clients.agent_sockets["BETA"] = beta

    # BETA is both a named recipient and the sender socket — it is sent to once.
    await broadcaster.send_directed(
        {"type": "chat", "payload": "x"}, names=["BETA"], sender_socket=beta
    )

    assert len(beta.sent) == 1


async def test_send_directed_skips_a_name_with_no_live_socket() -> None:
    clients = _registry()
    broadcaster = _broadcaster(clients, RelayMirror(None, 8))
    beta = _RecordingSocket()
    clients.agent_sockets["BETA"] = beta

    await broadcaster.send_directed(
        {"type": "chat", "payload": "x"}, names=["BETA", "GHOST"], sender_socket=None
    )

    assert beta.sent == [json.dumps({"type": "chat", "payload": "x"})]


async def test_send_directed_with_no_live_targets_still_mirrors(tmp_path: Path) -> None:
    log = tmp_path / "relay.ndjson"
    clients = _registry()
    broadcaster = _broadcaster(clients, RelayMirror(log, 8))

    await broadcaster.send_directed(
        {"type": "chat", "payload": "void"}, names=["GHOST"], sender_socket=None
    )

    events, _ = read_jsonl_since(log, 0)
    assert [decode_lite(event)["payload"] for event in events] == ["void"]


async def test_send_to_agent_reports_a_miss_for_a_name_that_never_bound() -> None:
    """A recipient with no live socket is a reported miss, not an error.

    The registry here is real and genuinely empty — this is exactly the state
    a channel fan-out sees when a member disconnects between the roster
    snapshot and its turn in the send loop.
    """
    broadcaster = _broadcaster(_registry(), RelayMirror(None, 8))

    delivered = await broadcaster.send_to_agent("PROJ/vanished", {"type": "chat", "payload": "x"})

    assert delivered is False


async def _bound_server_socket(hub: SynapseHub, name: str, *, timeout: float = 3.0) -> Any:
    """Poll the live registry until ``name`` binds; return its server-side socket.

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
        The real server-side socket the hub bound ``name`` to.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        websocket = hub.clients.agent_sockets.get(name)
        if websocket is not None:
            return websocket
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} did not bind on the hub")


async def _await_unbound(hub: SynapseHub, name: str, *, timeout: float = 3.0) -> None:
    """Poll the live registry until the hub has reaped ``name``'s binding.

    Parameters
    ----------
    hub : SynapseHub
        The in-process hub under test.
    name : str
        The agent name whose disappearance to wait for.
    timeout : float, optional
        Seconds to keep polling before failing the test.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if name not in hub.clients.agent_sockets:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"{name} was not reaped from the hub")


async def test_send_to_agent_reports_a_recipient_whose_socket_died_in_flight() -> None:
    """A send onto a genuinely dead socket is a reported miss, not a crash.

    A real client registers on a live hub and disconnects; the test then pins
    the exact race window — the socket is dead but the identity map still
    names it (the hub has not pruned the binding yet) — by re-pointing the
    live registry at the real, closed server-side socket. The send genuinely
    fails on the closed connection and must be reported as a miss.
    """
    name = "PROJ/mayfly"
    async with running_hub(SynapseHub(hub_id="syn-test")) as (hub, uri):
        async with connect(uri) as recipient:
            await read_json(recipient)  # welcome
            await send_json(recipient, sender=name, type="heartbeat")
            server_ws = await _bound_server_socket(hub, name)
        await _await_unbound(hub, name)

        # Freeze the race window: the socket died but the map still names it.
        hub.clients.agent_sockets[name] = server_ws
        try:
            delivered = await hub._send_to_agent(name, {"type": "chat", "payload": "ping"})
        finally:
            hub.clients.agent_sockets.pop(name, None)

        assert delivered is False
