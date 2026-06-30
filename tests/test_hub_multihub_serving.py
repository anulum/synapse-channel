# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — the serving half of the multi-hub event-log pull, over real sockets

from __future__ import annotations

from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.multihub_wire import LogSnapshot, decode_log_snapshot
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType

_SNAPSHOT = MessageType.MULTIHUB_LOG_SNAPSHOT


async def _connect(uri: str, name: str) -> ClientConnection:
    """Open a raw client socket, drain the welcome, and register with a heartbeat."""
    websocket = await connect(uri)
    await read_until_type(websocket, "welcome")
    await send_json(websocket, sender=name, type="heartbeat")
    return websocket


async def _seed_chats(uri: str, count: int) -> None:
    """Drive ``count`` chats so the hub journals one ``chat`` event per message."""
    async with await _connect(uri, "writer") as ws:
        for index in range(count):
            await send_json(ws, sender="writer", type="chat", payload=f"m{index}")
            await read_until_type(ws, "chat")


async def _pull(uri: str, **request: Any) -> LogSnapshot:
    """Send one multi-hub log request as a peer and decode the snapshot reply."""
    async with await _connect(uri, "peer") as ws:
        await send_json(ws, sender="peer", type=MessageType.MULTIHUB_LOG_REQUEST, **request)
        message = await read_until_type(ws, _SNAPSHOT)
    return decode_log_snapshot(message)


async def test_serves_the_whole_log_from_the_zero_cursor(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        snapshot = await _pull(uri, after_seq=0)
    store.close()
    assert [event.seq for event in snapshot.events] == [1, 2, 3]
    assert {event.kind for event in snapshot.events} == {"chat"}
    assert snapshot.next_cursor == 3


async def test_respects_the_batch_limit(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        snapshot = await _pull(uri, after_seq=0, limit=1)
    store.close()
    assert [event.seq for event in snapshot.events] == [1]
    assert snapshot.next_cursor == 1


async def test_serves_only_events_past_the_cursor(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        snapshot = await _pull(uri, after_seq=1)
    store.close()
    assert [event.seq for event in snapshot.events] == [2, 3]
    assert snapshot.next_cursor == 3


async def test_empty_batch_does_not_move_the_cursor(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        snapshot = await _pull(uri, after_seq=3)
    store.close()
    assert snapshot.events == ()
    assert snapshot.next_cursor == 3


async def test_hub_without_a_journal_serves_an_empty_snapshot() -> None:
    hub = SynapseHub(hub_id="syn-a")
    async with running_hub(hub) as (_, uri):
        snapshot = await _pull(uri, after_seq=5)
    assert snapshot.events == ()
    assert snapshot.next_cursor == 5


async def test_a_malformed_request_is_answered_with_an_empty_snapshot(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    hub = SynapseHub(hub_id="syn-a", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 2)
        snapshot = await _pull(uri, after_seq="not-a-number")
    store.close()
    assert snapshot.events == ()
    assert snapshot.next_cursor == 0
