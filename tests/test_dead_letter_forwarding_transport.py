# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — initiating half of cross-hub dead-letter forwarding (the transport)

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.dead_letter_forwarding import (
    FORWARDING_FIELD,
    DeadLetterForwardError,
    forwarding_notice,
)
from synapse_channel.core.dead_letter_forwarding_transport import forward_dead_letter
from synapse_channel.core.protocol import MessageType

_TYPE = MessageType.DEAD_LETTER_FORWARDING


def _notice() -> dict[str, Any]:
    return forwarding_notice("OWNED/reader", 3, origin_hub_id="syn-edge", owner_hub_id="syn-owner")


class _FakeSocket:
    """A connection that records what was sent; a forward awaits no reply, so it never receives."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


class _DroppingSocket:
    """A connection whose send fails, to drive the dropped-connection path."""

    async def send(self, message: str) -> None:
        raise ConnectionClosed(None, None)


class _HangingSocket:
    """A connection whose send never completes, to drive the forward timeout."""

    async def send(self, message: str) -> None:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover


def _connector(socket: Any, *, opened: list[str] | None = None) -> Any:
    """Return an injectable connector yielding ``socket`` and recording opened URIs."""

    @contextlib.asynccontextmanager
    async def _open(_uri: str) -> AsyncIterator[Any]:
        if opened is not None:
            opened.append(_uri)
        yield socket

    def factory(uri: str) -> AbstractAsyncContextManager[Any]:
        return _open(uri)

    return factory


def _refusing_connector(exc: BaseException) -> Any:
    """Return a connector whose connection attempt raises ``exc`` before yielding a socket."""

    @contextlib.asynccontextmanager
    async def _open(_uri: str) -> AsyncIterator[Any]:
        raise exc
        yield  # pragma: no cover - unreachable, present only to make this an async generator

    def factory(uri: str) -> AbstractAsyncContextManager[Any]:
        return _open(uri)

    return factory


# --- happy path --------------------------------------------------------------------------


async def test_forward_sends_the_nested_pointer_and_awaits_no_reply() -> None:
    socket = _FakeSocket()
    opened: list[str] = []
    # Fire-and-forget: the sender returns nothing (typed ``-> None``); it awaits no verdict.
    await forward_dead_letter(
        _notice(),
        uri="ws://owner:1/",
        local_id="syn-edge",
        connector=_connector(socket, opened=opened),
    )
    assert opened == ["ws://owner:1/"]
    assert len(socket.sent) == 1
    frame = json.loads(socket.sent[0])
    assert frame["type"] == _TYPE
    assert frame["sender"] == "syn-edge"
    assert "token" not in frame
    # The pointer rides under its own field, not spread across the reserved envelope keys.
    assert frame[FORWARDING_FIELD] == _notice()
    assert frame["target"] == "all"  # the envelope recipient, untouched by the pointer's target


async def test_forward_carries_a_token_when_the_owner_gates_the_first_frame() -> None:
    socket = _FakeSocket()
    await forward_dead_letter(
        _notice(),
        uri="wss://owner/",
        local_id="syn-edge",
        token="secret",
        connector=_connector(socket),
    )
    frame = json.loads(socket.sent[0])
    assert frame["token"] == "secret"


async def test_forward_never_puts_a_message_body_on_the_wire() -> None:
    # The honesty bound over the transport: only the four-field pointer is framed, never a body.
    socket = _FakeSocket()
    await forward_dead_letter(
        _notice(), uri="ws://owner/", local_id="syn-edge", connector=_connector(socket)
    )
    frame = json.loads(socket.sent[0])
    assert set(frame[FORWARDING_FIELD]) == {"target", "count", "origin_hub_id", "owner_hub_id"}
    assert "secret-body" not in socket.sent[0]


# --- failure modes (every one fails closed as DeadLetterForwardError) ---------------------


async def test_forward_raises_when_the_connection_is_refused() -> None:
    with pytest.raises(DeadLetterForwardError, match="failed"):
        await forward_dead_letter(
            _notice(),
            uri="ws://owner/",
            local_id="syn-edge",
            connector=_refusing_connector(OSError("connection refused")),
        )


async def test_forward_raises_when_the_send_drops() -> None:
    with pytest.raises(DeadLetterForwardError, match="failed"):
        await forward_dead_letter(
            _notice(),
            uri="ws://owner/",
            local_id="syn-edge",
            connector=_connector(_DroppingSocket()),
        )


async def test_forward_times_out_when_the_send_hangs() -> None:
    with pytest.raises(DeadLetterForwardError, match="failed"):
        await forward_dead_letter(
            _notice(),
            uri="ws://owner/",
            local_id="syn-edge",
            timeout=0.05,
            connector=_connector(_HangingSocket()),
        )
