# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — mailbox replay stays ahead of a live frame
"""A live send that arrives during replay does not overtake the backlog."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from synapse_channel.core.handlers.messaging import _replay_directed_backlog
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

_SEAT = "ORDER/seat"


class _Socket:
    """Record frames. The first write wakes the test; later writes can fail."""

    def __init__(self, *, fail_after: int | None = None, pause_first: bool = False) -> None:
        self.writes: list[int] = []
        self.first = asyncio.Event()
        self.release = asyncio.Event()
        self.fail_after = fail_after
        self.pause_first = pause_first

    async def send(self, raw: str) -> None:
        """Append one sequence. A paused first write lets the test queue a live frame."""
        frame = json.loads(raw)
        seq = frame["seq"]
        assert isinstance(seq, int) and not isinstance(seq, bool)
        if self.fail_after is not None and len(self.writes) >= self.fail_after:
            raise ConnectionError("socket dropped")
        self.writes.append(seq)
        if len(self.writes) == 1:
            self.first.set()
            if self.pause_first:
                await self.release.wait()


def _journal(path: Path, count: int) -> EventStore:
    """Store ``count`` directed chats for the seat and return the open store."""
    store = EventStore(path)
    for index in range(count):
        store.append(
            EventKind.CHAT,
            {
                "type": "chat",
                "sender": "ORDER/peer",
                "target": _SEAT,
                "payload": f"old-{index}",
            },
        )
    return store


@pytest.mark.asyncio
async def test_live_send_during_replay_stays_behind_the_backlog(tmp_path: Path) -> None:
    """Three replayed frames stay ahead of a live frame that arrives after the first."""
    store = _journal(tmp_path / "hub.db", 3)
    try:
        hub = SynapseHub(journal=store)
        socket = _Socket()
        hub.clients.agent_sockets[_SEAT] = socket
        replay = asyncio.create_task(_replay_directed_backlog(hub, _SEAT, _SEAT, 0, socket))
        await socket.first.wait()
        live = asyncio.create_task(
            hub.send_to_agent(
                _SEAT,
                {
                    "type": "chat",
                    "sender": "ORDER/peer",
                    "target": _SEAT,
                    "payload": "live",
                    "seq": 4,
                },
            )
        )
        await asyncio.gather(replay, live)
    finally:
        store.close()
    assert socket.writes == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_live_send_is_not_written_when_replay_drops(tmp_path: Path) -> None:
    """A live frame queued during replay is not written after the backlog write fails."""
    store = _journal(tmp_path / "hub.db", 3)
    try:
        hub = SynapseHub(journal=store)
        socket = _Socket(fail_after=1, pause_first=True)
        hub.clients.agent_sockets[_SEAT] = socket
        replay = asyncio.create_task(_replay_directed_backlog(hub, _SEAT, _SEAT, 0, socket))
        await socket.first.wait()
        live = asyncio.create_task(
            hub.send_to_agent(
                _SEAT,
                {
                    "type": "chat",
                    "sender": "ORDER/peer",
                    "target": _SEAT,
                    "payload": "live",
                    "seq": 4,
                },
            )
        )
        await asyncio.sleep(0)
        socket.release.set()
        replay_result, delivered = await asyncio.gather(replay, live, return_exceptions=True)
    finally:
        store.close()
    assert isinstance(replay_result, ConnectionError)
    assert socket.writes == [1]
    assert delivered is False
