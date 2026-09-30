# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deadline, failure and cancellation tests for bounded outbound writes

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from synapse_channel.core.outbound_send import OutboundSender


class _Transport:
    """Record aborts without substituting protocol behavior in e2e tests."""

    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        """Record that the transport was forcibly closed."""
        self.aborted = True


class _Socket:
    """Control coroutine completion to exercise cleanup failure boundaries."""

    def __init__(self, *, block: bool = False, close_mode: str = "ok") -> None:
        self.transport: Any = _Transport()
        self.block = block
        self.close_mode = close_mode
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.sent: list[str] = []
        self.close_code: int | None = None

    async def send(self, raw: str) -> None:
        """Record active writes, exposing success, stalls and write failure."""
        self.active += 1
        self.started.set()
        try:
            if self.block:
                await self.release.wait()
            if raw == "error":
                raise OSError("write failed")
            self.sent.append(raw)
        finally:
            self.active -= 1

    async def close(self, *, code: int, reason: str) -> None:
        """Model peer close success, silence, failure or late write completion."""
        self.close_code = code
        if self.close_mode == "error":
            raise OSError("close failed")
        if self.close_mode == "block":
            await asyncio.Event().wait()
        if self.close_mode == "release":
            self.release.set()
            await asyncio.sleep(0)


@pytest.mark.parametrize("deadline", [0.0, -1.0, float("nan"), float("inf"), True])
@pytest.mark.parametrize("field", ["send_timeout", "close_timeout"])
def test_invalid_deadlines_are_refused(deadline: float, field: str) -> None:
    """Reject disabled, non-finite and boolean write/close deadlines."""
    with pytest.raises(ValueError, match="positive and finite"):
        OutboundSender(**{field: deadline})


async def test_completed_write_and_transport_error_propagation() -> None:
    """Successful writes complete; write faults remain observable to callers."""
    socket = _Socket()
    sender = OutboundSender(send_timeout=0.1, close_timeout=0.1)
    await sender.send(socket, "ok")
    with pytest.raises(OSError, match="write failed"):
        await sender.send(socket, "error")
    assert socket.sent == ["ok"]
    assert socket.active == 0
    assert not socket.transport.aborted


@pytest.mark.parametrize("close_mode", ["ok", "error", "block", "release"])
async def test_stalled_writes_close_and_leave_no_background_writer(close_mode: str) -> None:
    """Close failure or peer silence cannot keep the stalled write alive."""
    socket = _Socket(block=True, close_mode=close_mode)
    sender = OutboundSender(send_timeout=0.01, close_timeout=0.01)
    with pytest.raises(TimeoutError, match="outbound delivery timeout"):
        await sender.send(socket, "blocked")
    assert socket.close_code == 1013
    assert socket.transport.aborted
    assert socket.active == 0


async def test_stalled_socket_without_transport_still_cleans_up() -> None:
    """A custom socket without transport metadata still drains its write task."""
    socket = _Socket(block=True)
    socket.transport = None
    with pytest.raises(TimeoutError):
        await OutboundSender(send_timeout=0.01, close_timeout=0.01).send(socket, "blocked")
    assert socket.active == 0


async def test_caller_cancellation_aborts_and_drains_unfinished_write() -> None:
    """Cancelling fan-out cannot leave an orphaned send coroutine."""
    socket = _Socket(block=True)
    operation = asyncio.create_task(OutboundSender().send(socket, "blocked"))
    await socket.started.wait()
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert socket.transport.aborted
    assert socket.active == 0


async def test_abort_failure_does_not_orphan_the_write() -> None:
    """Cleanup still drains the writer when a broken transport rejects abort."""

    class BrokenTransport:
        """Model a transport teardown fault, independently of peer traffic."""

        def abort(self) -> None:
            """Expose a teardown failure without allowing a writer leak."""
            raise OSError("abort failed")

    socket = _Socket(block=True)
    socket.transport = BrokenTransport()
    with pytest.raises(TimeoutError):
        await OutboundSender(send_timeout=0.01, close_timeout=0.01).send(socket, "blocked")
    assert socket.active == 0
