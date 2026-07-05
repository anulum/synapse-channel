# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — initiating half of a cross-hub operator relay

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_transport import (
    RelayTransportError,
    relay_operator_action,
)
from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    RelayActionResult,
    encode_relay_result,
)
from synapse_channel.core.protocol import MAX_JSON_DEPTH, MessageType

_REQUEST = MessageType.OPERATOR_RELAY_REQUEST
_RESULT = MessageType.OPERATOR_RELAY_RESULT
_NAMESPACE = "SYNAPSE-CHANNEL"


def _request(task_id: str = "t1") -> RelayActionRequest:
    return RelayActionRequest(
        action="release",
        namespace=_NAMESPACE,
        task_id=task_id,
        operator="ops-admin",
        origin_hub_id="syn-a",
    )


def _wire(frame: dict[str, Any]) -> str:
    return json.dumps(frame)


def _result_frame(result: RelayActionResult) -> str:
    return _wire({"type": _RESULT, **encode_relay_result(result)})


def _applied(task_id: str = "t1", detail: str = "released") -> RelayActionResult:
    return RelayActionResult(
        applied=True,
        action="release",
        namespace=_NAMESPACE,
        task_id=task_id,
        owner_hub_id="syn-owner",
        detail=detail,
    )


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
    """A connection whose receive never completes, to drive the relay timeout."""

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


# --- happy path --------------------------------------------------------------------------


async def test_relay_returns_the_verdict_and_sends_the_request() -> None:
    socket = _FakeSocket([_wire({"type": "welcome"}), _result_frame(_applied())])
    opened: list[str] = []
    result = await relay_operator_action(
        _request(),
        uri="ws://peer:1/",
        local_id="operator-relay",
        connector=_connector(socket, opened=opened),
    )
    assert result.applied is True
    assert result.detail == "released"
    assert opened == ["ws://peer:1/"]
    request = json.loads(socket.sent[0])
    assert request["type"] == _REQUEST
    assert request["sender"] == "operator-relay"
    assert request["action"] == "release"
    assert request["operator"] == "ops-admin"
    assert "token" not in request


async def test_relay_skips_a_broadcast_then_decodes_a_bytes_result() -> None:
    frames: list[str | bytes | BaseException] = [
        _wire({"type": MessageType.RELEASE_GRANTED, "task_id": "t1"}),
        _result_frame(_applied()).encode("utf-8"),
    ]
    result = await relay_operator_action(
        _request(), uri="ws://peer/", local_id="op", connector=_connector(_FakeSocket(frames))
    )
    assert result.applied is True


async def test_relay_reports_a_refusal() -> None:
    refused = RelayActionResult(
        applied=False,
        action="release",
        namespace=_NAMESPACE,
        task_id="t1",
        owner_hub_id="syn-owner",
        detail="scope_not_granted",
    )
    result = await relay_operator_action(
        _request(),
        uri="ws://peer/",
        local_id="op",
        connector=_connector(_FakeSocket([_result_frame(refused)])),
    )
    assert result.applied is False
    assert result.detail == "scope_not_granted"


async def test_relay_carries_a_token_on_the_request() -> None:
    socket = _FakeSocket([_result_frame(_applied())])
    await relay_operator_action(
        _request(), uri="ws://peer/", local_id="op", token="secret", connector=_connector(socket)
    )
    request = json.loads(socket.sent[0])
    assert request["token"] == "secret"


# --- failure modes (every one fails closed as RelayTransportError) -----------------------


async def test_relay_raises_on_an_error_frame() -> None:
    socket = _FakeSocket([_wire({"type": MessageType.ERROR, "payload": "Malformed request"})])
    with pytest.raises(RelayTransportError, match="refused"):
        await relay_operator_action(
            _request(), uri="ws://peer/", local_id="op", connector=_connector(socket)
        )


async def test_relay_raises_on_a_non_object_frame() -> None:
    socket = _FakeSocket([_wire({"type": "welcome"}), json.dumps([1, 2, 3])])
    with pytest.raises(RelayTransportError, match="not a JSON object"):
        await relay_operator_action(
            _request(), uri="ws://peer/", local_id="op", connector=_connector(socket)
        )


async def test_relay_raises_on_invalid_json() -> None:
    socket = _FakeSocket(["this is not json{"])
    with pytest.raises(RelayTransportError, match="failed"):
        await relay_operator_action(
            _request(), uri="ws://peer/", local_id="op", connector=_connector(socket)
        )


async def test_relay_raises_on_a_malformed_result() -> None:
    bad = _wire({"type": _RESULT, "applied": "yes", "action": "release", "task_id": "t1"})
    with pytest.raises(RelayTransportError, match="failed"):
        await relay_operator_action(
            _request(), uri="ws://peer/", local_id="op", connector=_connector(_FakeSocket([bad]))
        )


async def test_relay_raises_when_the_connection_closes_before_a_result() -> None:
    socket = _FakeSocket([_wire({"type": "welcome"})])
    with pytest.raises(RelayTransportError, match="failed"):
        await relay_operator_action(
            _request(), uri="ws://peer/", local_id="op", connector=_connector(socket)
        )


async def test_relay_raises_on_a_dropped_connection() -> None:
    socket = _FakeSocket([OSError("connection reset")])
    with pytest.raises(RelayTransportError, match="failed"):
        await relay_operator_action(
            _request(), uri="ws://peer/", local_id="op", connector=_connector(socket)
        )


async def test_relay_times_out_when_no_result_arrives() -> None:
    with pytest.raises(RelayTransportError, match="failed"):
        await relay_operator_action(
            _request(),
            uri="ws://peer/",
            local_id="op",
            timeout=0.05,
            connector=_connector(_HangingSocket()),
        )


async def test_relay_fails_closed_on_a_deeply_nested_reply() -> None:
    bomb = "[" * (MAX_JSON_DEPTH + 1) + "1" + "]" * (MAX_JSON_DEPTH + 1)
    socket = _FakeSocket([_wire({"type": "welcome"}), bomb])
    with pytest.raises(RelayTransportError, match="failed"):
        await relay_operator_action(
            _request(), uri="ws://peer/", local_id="op", connector=_connector(socket)
        )


# --- real-socket integration against the serving half -----------------------------------


async def test_relay_reaches_a_real_hub_and_decodes_its_refusal() -> None:
    # A real hub that governs the namespace but configures no serving policy refuses every
    # relay; the transport carries the request to its handler and decodes the authentic
    # result, proving the wire path without a mutual-TLS handshake.
    ownership = NamespaceOwnership(owners={_NAMESPACE: "syn-owner"}, local_hub_id="syn-owner")
    hub = SynapseHub(hub_id="syn-owner", namespace_ownership=ownership)
    async with running_hub(hub) as (_, uri):
        result = await relay_operator_action(_request(), uri=uri, local_id="peer")
    assert result.applied is False
    assert result.detail == "peer_not_authorised"
