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
from typing import Any, cast

import pytest

from synapse_channel.core.handlers.messaging import _replay_directed_backlog
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.outbound_send import OutboundSender
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


class _Gate:
    """A socket whose first write waits until the test opens the gate."""

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.gate = asyncio.Event()
        self.closed: list[int] = []

    async def send(self, raw: str) -> None:
        """Record one frame, and block the first write on ``gate``."""
        self.writes.append(raw)
        if len(self.writes) == 1:
            await self.gate.wait()

    async def close(self, code: int, reason: str) -> None:
        """Record a close code. ``reason`` is accepted and ignored."""
        del reason
        self.closed.append(code)


class _FlushFail:
    """Accept the backlog frame and fail the next one."""

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.gate = asyncio.Event()

    async def send(self, raw: str) -> None:
        """Write ``a`` after the gate, and reject every later frame."""
        if raw != "a":
            raise ConnectionError("flush failed")
        self.writes.append(raw)
        await self.gate.wait()


@pytest.mark.asyncio
async def test_cancelled_backlog_does_not_keep_the_hold() -> None:
    """Cancelling a backlog removes its hold so a later live send can finish."""
    sender = OutboundSender(send_timeout=5.0, close_timeout=1.0)
    socket = _Gate()
    backlog = asyncio.create_task(sender.send_backlog(socket, ["a", "b"]))
    await asyncio.sleep(0)
    backlog.cancel()
    with pytest.raises(asyncio.CancelledError):
        await backlog
    assert len(sender._replay_holds) == 0
    socket.gate.set()
    await sender.send(socket, "live")
    assert socket.writes == ["a", "live"]


@pytest.mark.asyncio
async def test_cancelled_live_frame_is_not_written() -> None:
    """A live sender that gives up is not written when the backlog later flushes."""
    sender = OutboundSender(send_timeout=5.0, close_timeout=1.0)
    socket = _Gate()
    backlog = asyncio.create_task(sender.send_backlog(socket, ["a"]))
    await asyncio.sleep(0)
    live = asyncio.create_task(sender.send(socket, "live"))
    await asyncio.sleep(0)
    live.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live
    socket.gate.set()
    await backlog
    assert socket.writes == ["a"]


@pytest.mark.asyncio
async def test_flush_failure_fails_the_waiting_sender() -> None:
    """A failed flush write fails that live sender and does not keep the hold."""
    sender = OutboundSender()
    socket = _FlushFail()
    backlog = asyncio.create_task(sender.send_backlog(socket, ["a"]))
    await asyncio.sleep(0)
    live = asyncio.create_task(sender.send(socket, "live"))
    await asyncio.sleep(0)
    socket.gate.set()
    backlog_result, live_result = await asyncio.gather(backlog, live, return_exceptions=True)
    assert isinstance(backlog_result, ConnectionError)
    assert isinstance(live_result, ConnectionError)
    assert socket.writes == ["a"]
    assert len(sender._replay_holds) == 0


@pytest.mark.asyncio
async def test_second_backlog_waits_behind_the_first() -> None:
    """A second replay is queued, and the socket stays open."""
    sender = OutboundSender()
    socket = _Gate()
    first = asyncio.create_task(sender.send_backlog(socket, ["a", "b"]))
    await asyncio.sleep(0)
    second = asyncio.create_task(sender.send_backlog(socket, ["c"]))
    await asyncio.sleep(0)
    socket.gate.set()
    await asyncio.gather(first, second)
    assert socket.writes == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_empty_backlog_returns() -> None:
    """An empty backlog does not take a hold."""
    sender = OutboundSender()
    socket = _Gate()
    await sender.send_backlog(socket, [])
    assert len(sender._replay_holds) == 0
    assert socket.writes == []


@pytest.mark.asyncio
async def test_held_send_times_out_and_closes() -> None:
    """A live frame queued behind a replay does not wait past the send deadline.

    The hold is already active. A stalled backlog write uses the same deadline
    and fails the queued frame itself, so this test does not start that write.
    """
    sender = OutboundSender(send_timeout=0.05, close_timeout=0.05)
    socket = _Gate()
    sender._replay_holds[socket] = []
    with pytest.raises(TimeoutError, match="outbound delivery timeout"):
        await sender.send(socket, "live")
    assert 1013 in socket.closed
    assert socket.writes == []
    queued = sender._replay_holds[socket]
    assert len(queued) == 1
    assert queued[0][1].cancelled()


@pytest.mark.asyncio
async def test_stalled_backlog_write_fails_the_held_sender() -> None:
    """A backlog write that misses its deadline does not leave the live sender waiting."""
    sender = OutboundSender(send_timeout=0.05, close_timeout=0.05)
    socket = _Gate()
    backlog = asyncio.create_task(sender.send_backlog(socket, ["a"]))
    await asyncio.sleep(0)
    with pytest.raises((TimeoutError, ConnectionError)):
        await sender.send(socket, "live")
    result = await asyncio.gather(backlog, return_exceptions=True)
    assert isinstance(result[0], TimeoutError)
    assert 1013 in socket.closed
    assert "live" not in socket.writes
    assert len(sender._replay_holds) == 0


@pytest.mark.asyncio
async def test_replay_without_backlog_sender_uses_send_json(tmp_path: Path) -> None:
    """A hub with no backlog sender still replays one frame at a time."""
    store = _journal(tmp_path / "hub.db", 3)
    sent: list[int] = []

    class _Hub:
        journal = store

        def roles_of(self, name: str) -> tuple[str, ...]:
            del name
            return ()

        async def send_json(self, websocket: object, data: dict[str, object]) -> None:
            del websocket
            seq = data["seq"]
            assert isinstance(seq, int)
            sent.append(seq)

    try:
        await _replay_directed_backlog(cast(Any, _Hub()), _SEAT, _SEAT, 0, object())
    finally:
        store.close()
    assert sent == [1, 2, 3]


@pytest.mark.asyncio
async def test_second_backlog_cancel_drops_its_frame() -> None:
    """Cancelling a queued second backlog does not write its frame."""
    sender = OutboundSender()
    socket = _Gate()
    first = asyncio.create_task(sender.send_backlog(socket, ["a", "b"]))
    await asyncio.sleep(0)
    second = asyncio.create_task(sender.send_backlog(socket, ["c"]))
    await asyncio.sleep(0)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    socket.gate.set()
    await first
    assert socket.writes == ["a", "b"]


class _FailLive:
    """Block the backlog, then fail the live frame after its waiter is gone."""

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.gate = asyncio.Event()
        self.live: asyncio.Task[None] | None = None

    async def send(self, raw: str) -> None:
        """Hold ``a``. Cancel the live waiter, then reject that frame."""
        if raw == "live":
            assert self.live is not None
            self.live.cancel()
            await asyncio.sleep(0)
            raise ConnectionError("flush failed")
        self.writes.append(raw)
        await self.gate.wait()


@pytest.mark.asyncio
async def test_flush_skips_a_frame_whose_waiter_is_already_gone() -> None:
    """A flush that fails does not report a frame whose waiter already left."""
    sender = OutboundSender()
    socket = _FailLive()
    backlog = asyncio.create_task(sender.send_backlog(socket, ["a", "b"]))
    await asyncio.sleep(0)
    live = asyncio.create_task(sender.send(socket, "live"))
    later = asyncio.create_task(sender.send(socket, "later"))
    socket.live = live
    await asyncio.sleep(0)
    later.cancel()
    with pytest.raises(asyncio.CancelledError):
        await later
    socket.gate.set()
    backlog_result, live_result = await asyncio.gather(backlog, live, return_exceptions=True)
    assert isinstance(backlog_result, ConnectionError)
    assert isinstance(live_result, asyncio.CancelledError)
    assert "live" not in socket.writes
    assert "later" not in socket.writes


class _FinishLive:
    """Cancel the live waiter during its write, then let that write finish."""

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.gate = asyncio.Event()
        self.live: asyncio.Task[None] | None = None

    async def send(self, raw: str) -> None:
        """Hold ``a``. Cancel the live waiter, then accept the frame."""
        self.writes.append(raw)
        if raw == "a":
            await self.gate.wait()
            return
        if raw == "live" and self.live is not None:
            self.live.cancel()
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_flush_does_not_complete_a_waiter_that_already_left() -> None:
    """A finished write does not mark a waiter that already left."""
    sender = OutboundSender()
    socket = _FinishLive()
    backlog = asyncio.create_task(sender.send_backlog(socket, ["a"]))
    await asyncio.sleep(0)
    live = asyncio.create_task(sender.send(socket, "live"))
    socket.live = live
    await asyncio.sleep(0)
    socket.gate.set()
    backlog_result, live_result = await asyncio.gather(backlog, live, return_exceptions=True)
    assert backlog_result is None
    assert isinstance(live_result, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_held_timeout_leaves_a_finished_future() -> None:
    """A timeout does not cancel a held future that already finished."""
    sender = OutboundSender()
    socket = _Gate()

    async def _timeout(awaitable: object, timeout: float) -> None:
        del timeout
        close = getattr(awaitable, "close", None)
        if close is not None:
            close()
        raise TimeoutError

    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    future.set_result(None)
    original = asyncio.wait_for
    cast(Any, asyncio).wait_for = _timeout
    try:
        with pytest.raises(TimeoutError, match="outbound delivery timeout"):
            await sender._await_held(socket, future)
    finally:
        cast(Any, asyncio).wait_for = original
    assert future.done()
    assert not future.cancelled()
