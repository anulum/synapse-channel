# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — fetching half of the multi-hub event-log pull

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast

import pytest
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.clock_skew import ClockSkew
from synapse_channel.core.multihub_federation import MultiHubAuthorisation
from synapse_channel.core.multihub_follower import MultiHubFollower
from synapse_channel.core.multihub_transport import (
    MultiHubFetchError,
    network_fetcher,
)
from synapse_channel.core.multihub_wire import (
    AFTER_SEQ_FIELD,
    EVENTS_FIELD,
    LIMIT_FIELD,
    NEXT_CURSOR_FIELD,
    LogSnapshot,
    encode_log_snapshot,
)
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.protocol import (
    MAX_JSON_DEPTH,
    WIRE_PROTOCOL_VERSION,
    MessageType,
    ProtocolNegotiation,
)

_REQUEST = MessageType.MULTIHUB_LOG_REQUEST
_SNAPSHOT = MessageType.MULTIHUB_LOG_SNAPSHOT


class _ProtocolAwareFetcher(Protocol):
    """Test-side surface exposed by the network fetcher object."""

    last_protocol_negotiation: ProtocolNegotiation | None
    last_clock_skew: ClockSkew | None


def _event(seq: int) -> StoredEvent:
    """Return a small stored event for snapshot fixtures."""
    return StoredEvent(seq=seq, ts=float(seq), kind="chat", payload={"text": f"m{seq}"})


def _wire(frame: dict[str, Any]) -> str:
    """Serialise a frame the way the hub would put it on the wire."""
    return json.dumps(frame)


def _snapshot_frame(events: Sequence[StoredEvent], next_cursor: int) -> str:
    """Build a serialised snapshot reply frame."""
    body = encode_log_snapshot(LogSnapshot(events=tuple(events), next_cursor=next_cursor))
    return _wire({"type": _SNAPSHOT, **body})


class _FakeSocket:
    """A scripted connection: returns queued frames, records what was sent."""

    def __init__(self, frames: Sequence[str | bytes | BaseException]) -> None:
        self._frames = list(frames)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        if not self._frames:
            raise ConnectionClosed(None, None)
        nxt = self._frames.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


class _HangingSocket:
    """A connection whose receive never completes, to drive the fetch timeout."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
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


def _last_negotiation(fetch: object) -> ProtocolNegotiation | None:
    """Return a network fetcher's last protocol negotiation for assertions."""
    return cast(_ProtocolAwareFetcher, fetch).last_protocol_negotiation


def _last_clock_skew(fetch: object) -> ClockSkew | None:
    """Return a network fetcher's last clock-skew observation for assertions."""
    return cast(_ProtocolAwareFetcher, fetch).last_clock_skew


# --- happy path --------------------------------------------------------------------------


async def test_fetch_returns_events_and_sends_the_cursor_request() -> None:
    socket = _FakeSocket([_wire({"type": "welcome"}), _snapshot_frame([_event(1), _event(2)], 2)])
    opened: list[str] = []
    fetch = network_fetcher(
        "ws://peer:1/", local_id="follower", connector=_connector(socket, opened=opened)
    )
    events = await fetch(0)
    assert [event.seq for event in events] == [1, 2]
    assert opened == ["ws://peer:1/"]
    request = json.loads(socket.sent[0])
    assert request["type"] == _REQUEST
    assert request["sender"] == "follower"
    assert request[AFTER_SEQ_FIELD] == 0
    assert request[LIMIT_FIELD] is None
    assert "token" not in request
    negotiation = _last_negotiation(fetch)
    assert negotiation is not None
    assert negotiation.peer_version is None
    assert negotiation.effective_version == 1


async def test_fetch_records_matching_protocol_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    socket = _FakeSocket(
        [
            _wire({"type": "welcome", "protocol_version": WIRE_PROTOCOL_VERSION}),
            _snapshot_frame([_event(1)], 1),
        ]
    )
    fetch = network_fetcher("ws://peer/", local_id="f", connector=_connector(socket))
    with caplog.at_level(logging.WARNING, logger="synapse_channel.core.multihub_transport"):
        events = await fetch(0)
    negotiation = _last_negotiation(fetch)
    assert [event.seq for event in events] == [1]
    assert negotiation is not None
    assert negotiation.effective_version == WIRE_PROTOCOL_VERSION
    assert negotiation.warning is None
    assert "protocol mismatch" not in caplog.text


async def test_fetch_records_peer_clock_skew_from_welcome_timestamp() -> None:
    socket = _FakeSocket(
        [
            _wire({"type": "welcome", "timestamp": 90.0}),
            _snapshot_frame([_event(1)], 1),
        ]
    )
    fetch = network_fetcher(
        "ws://peer/",
        local_id="f",
        connector=_connector(socket),
        clock=lambda: 100.0,
    )

    await fetch(0)

    skew = _last_clock_skew(fetch)
    assert skew is not None
    assert skew.seconds == 10.0


async def test_fetch_ignores_unusable_welcome_timestamp() -> None:
    socket = _FakeSocket(
        [
            _wire({"type": "welcome", "timestamp": True}),
            _snapshot_frame([_event(1)], 1),
        ]
    )
    fetch = network_fetcher(
        "ws://peer/",
        local_id="f",
        connector=_connector(socket),
        clock=lambda: 100.0,
    )

    await fetch(0)

    assert _last_clock_skew(fetch) is None


async def test_fetch_warns_and_degrades_for_an_older_peer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    warnings: list[ProtocolNegotiation] = []
    socket = _FakeSocket(
        [_wire({"type": "welcome", "protocol_version": 1}), _snapshot_frame([_event(1)], 1)]
    )
    fetch = network_fetcher(
        "ws://peer/",
        local_id="f",
        connector=_connector(socket),
        protocol_warning_sink=warnings.append,
    )
    with caplog.at_level(logging.WARNING, logger="synapse_channel.core.multihub_transport"):
        await fetch(0)
    negotiation = _last_negotiation(fetch)
    assert negotiation is not None
    assert negotiation.peer_version == 1
    assert negotiation.effective_version == 1
    assert negotiation.warning is not None
    assert warnings == [negotiation]
    assert "older than local" in caplog.text


async def test_fetch_warns_and_degrades_for_a_newer_peer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    socket = _FakeSocket(
        [
            _wire({"type": "welcome", "protocol_version": WIRE_PROTOCOL_VERSION + 1}),
            _snapshot_frame([], 0),
        ]
    )
    fetch = network_fetcher("ws://peer/", local_id="f", connector=_connector(socket))
    with caplog.at_level(logging.WARNING, logger="synapse_channel.core.multihub_transport"):
        await fetch(0)
    negotiation = _last_negotiation(fetch)
    assert negotiation is not None
    assert negotiation.peer_version == WIRE_PROTOCOL_VERSION + 1
    assert negotiation.effective_version == WIRE_PROTOCOL_VERSION
    assert negotiation.warning is not None
    assert "newer than local" in caplog.text


async def test_fetch_skips_presence_then_decodes_a_bytes_snapshot() -> None:
    frames: list[str | bytes | BaseException] = [
        _wire({"type": "presence_update", "agent": "x"}),
        _snapshot_frame([_event(5)], 5).encode("utf-8"),
    ]
    fetch = network_fetcher("ws://peer/", local_id="f", connector=_connector(_FakeSocket(frames)))
    events = await fetch(4)
    assert [event.seq for event in events] == [5]


async def test_fetch_forwards_limit_and_token_on_the_request() -> None:
    socket = _FakeSocket([_snapshot_frame([], 7)])
    fetch = network_fetcher(
        "ws://peer/", local_id="f", token="secret", limit=3, connector=_connector(socket)
    )
    events = await fetch(7)
    assert events == ()
    request = json.loads(socket.sent[0])
    assert request[LIMIT_FIELD] == 3
    assert request["token"] == "secret"


# --- failure modes (every one fails closed as MultiHubFetchError) ------------------------


async def test_fetch_raises_on_an_error_frame() -> None:
    socket = _FakeSocket([_wire({"type": MessageType.ERROR, "payload": "Rate limit exceeded."})])
    fetch = network_fetcher("ws://peer/", local_id="f", connector=_connector(socket))
    with pytest.raises(MultiHubFetchError, match="refused"):
        await fetch(0)


async def test_fetch_raises_on_a_non_object_frame() -> None:
    socket = _FakeSocket([_wire({"type": "welcome"}), json.dumps([1, 2, 3])])
    fetch = network_fetcher("ws://peer/", local_id="f", connector=_connector(socket))
    with pytest.raises(MultiHubFetchError, match="not a JSON object"):
        await fetch(0)


async def test_fetch_raises_on_invalid_json() -> None:
    socket = _FakeSocket(["this is not json{"])
    fetch = network_fetcher("ws://peer/", local_id="f", connector=_connector(socket))
    with pytest.raises(MultiHubFetchError, match="failed"):
        await fetch(0)


async def test_fetch_raises_on_a_malformed_snapshot() -> None:
    bad = _wire({"type": _SNAPSHOT, EVENTS_FIELD: [{"seq": "x"}], NEXT_CURSOR_FIELD: 0})
    fetch = network_fetcher("ws://peer/", local_id="f", connector=_connector(_FakeSocket([bad])))
    with pytest.raises(MultiHubFetchError, match="failed"):
        await fetch(0)


async def test_fetch_raises_when_the_connection_closes_before_a_snapshot() -> None:
    socket = _FakeSocket([_wire({"type": "welcome"})])  # closes after, no snapshot
    fetch = network_fetcher("ws://peer/", local_id="f", connector=_connector(socket))
    with pytest.raises(MultiHubFetchError, match="failed"):
        await fetch(0)


async def test_fetch_raises_on_a_dropped_connection() -> None:
    socket = _FakeSocket([OSError("connection reset")])
    fetch = network_fetcher("ws://peer/", local_id="f", connector=_connector(socket))
    with pytest.raises(MultiHubFetchError, match="failed"):
        await fetch(0)


async def test_fetch_times_out_when_no_snapshot_arrives() -> None:
    fetch = network_fetcher(
        "ws://peer/", local_id="f", timeout=0.05, connector=_connector(_HangingSocket())
    )
    with pytest.raises(MultiHubFetchError, match="failed"):
        await fetch(0)


# --- deny-by-default authorisation gate --------------------------------------------------


async def test_fetch_proceeds_when_the_authoriser_allows() -> None:
    socket = _FakeSocket([_snapshot_frame([_event(1)], 1)])
    fetch = network_fetcher(
        "ws://peer/",
        local_id="f",
        authoriser=lambda: MultiHubAuthorisation(allowed=True, reason="authorised"),
        connector=_connector(socket),
    )
    events = await fetch(0)
    assert [event.seq for event in events] == [1]


async def test_fetch_fails_closed_without_connecting_when_unauthorised() -> None:
    opened: list[str] = []
    socket = _FakeSocket([_snapshot_frame([_event(1)], 1)])
    fetch = network_fetcher(
        "ws://peer/",
        local_id="f",
        authoriser=lambda: MultiHubAuthorisation(allowed=False, reason="unknown_domain"),
        connector=_connector(socket, opened=opened),
    )
    with pytest.raises(MultiHubFetchError, match="not authorised.*unknown_domain"):
        await fetch(0)
    assert opened == []
    assert socket.sent == []


# --- real-socket integration against the serving half ------------------------------------


async def _seed_chats(uri: str, count: int) -> None:
    """Drive chats through a real hub so its journal holds events to pull."""
    from websockets.asyncio.client import connect

    async with connect(uri) as ws:
        await read_until_type(ws, "welcome")
        await send_json(ws, sender="writer", type="heartbeat")
        for index in range(count):
            await send_json(ws, sender="writer", type="chat", payload=f"m{index}")
            await read_until_type(ws, "chat")


async def test_network_fetcher_pulls_a_real_hubs_log(tmp_path: Any) -> None:
    store = EventStore(tmp_path / "events.db")
    from synapse_channel.core.hub import SynapseHub

    hub = SynapseHub(hub_id="syn-peer", journal=store)
    async with running_hub(hub) as (_, uri):
        await _seed_chats(uri, 3)
        fetch = network_fetcher(uri, local_id="follower")
        events = await fetch(0)
        follower = MultiHubFollower()
        observed = await follower.poll("syn-peer", network_fetcher(uri, local_id="f2"))
    store.close()
    assert [event.seq for event in events] == [1, 2, 3]
    assert observed is not None
    negotiation = follower.protocol_negotiation("syn-peer")
    assert negotiation is not None
    assert negotiation.effective_version == WIRE_PROTOCOL_VERSION


async def test_fetch_fails_closed_on_a_deeply_nested_reply() -> None:
    """A peer reply nested past the wire depth bound fails the poll, not the parser."""
    bomb = "[" * (MAX_JSON_DEPTH + 1) + "1" + "]" * (MAX_JSON_DEPTH + 1)
    socket = _FakeSocket([bomb])
    fetch = network_fetcher("ws://peer/", local_id="f", connector=_connector(socket))
    with pytest.raises(MultiHubFetchError, match="failed"):
        await fetch(0)
