# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for the hub's outbound messaging unit

from __future__ import annotations

import json
from pathlib import Path

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
