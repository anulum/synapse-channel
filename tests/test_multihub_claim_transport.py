# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — forwarding half of cross-hub claim routing

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
from synapse_channel.core.multihub_claim_transport import (
    ClaimForwardError,
    ClaimForwardTimeoutError,
    forward_claim,
)
from synapse_channel.core.multihub_claim_wire import (
    ClaimForwardRequest,
    ClaimForwardResult,
    encode_claim_forward_result,
)
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.protocol import MAX_JSON_DEPTH, MessageType

_REQUEST = MessageType.MULTIHUB_CLAIM_REQUEST
_RESULT = MessageType.MULTIHUB_CLAIM_RESULT
_NAMESPACE = "SYNAPSE-CHANNEL"


def _request(task_id: str = "t1") -> ClaimForwardRequest:
    """Return a claim-forward request for the owned namespace."""
    return ClaimForwardRequest(
        namespace=_NAMESPACE,
        claimant="SYNAPSE-CHANNEL/alice",
        task_id=task_id,
        claim={"task_id": task_id, "note": "forwarded work"},
    )


def _wire(frame: dict[str, Any]) -> str:
    """Serialise a frame the way the hub would put it on the wire."""
    return json.dumps(frame)


def _result_frame(result: ClaimForwardResult) -> str:
    """Build a serialised claim-result reply frame."""
    return _wire({"type": _RESULT, **encode_claim_forward_result(result)})


def _granted(task_id: str = "t1") -> ClaimForwardResult:
    """Return a granted result with a grant body the owner would relay."""
    return ClaimForwardResult(
        granted=True,
        task_id=task_id,
        namespace=_NAMESPACE,
        owner_hub_id="syn-owner",
        detail="claimed",
        grant={"owner": "SYNAPSE-CHANNEL/alice", "task_id": task_id},
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
    """A connection whose receive never completes, to drive the forward timeout."""

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


async def test_forward_returns_the_grant_and_sends_the_request() -> None:
    socket = _FakeSocket([_wire({"type": "welcome"}), _result_frame(_granted())])
    opened: list[str] = []
    result = await forward_claim(
        _request(),
        uri="ws://owner:1/",
        local_id="forwarder",
        connector=_connector(socket, opened=opened),
    )
    assert result.granted is True
    assert result.grant == {"owner": "SYNAPSE-CHANNEL/alice", "task_id": "t1"}
    assert opened == ["ws://owner:1/"]
    request = json.loads(socket.sent[0])
    assert request["type"] == _REQUEST
    assert request["sender"] == "forwarder"
    assert request["namespace"] == _NAMESPACE
    assert request["claimant"] == "SYNAPSE-CHANNEL/alice"
    assert "token" not in request


async def test_forward_skips_the_grant_broadcast_then_decodes_a_bytes_result() -> None:
    frames: list[str | bytes | BaseException] = [
        _wire({"type": MessageType.CLAIM_GRANTED, "task_id": "t1"}),
        _result_frame(_granted()).encode("utf-8"),
    ]
    result = await forward_claim(
        _request(), uri="ws://owner/", local_id="f", connector=_connector(_FakeSocket(frames))
    )
    assert result.granted is True


async def test_forward_relays_a_denial() -> None:
    denied = ClaimForwardResult(
        granted=False,
        task_id="t1",
        namespace=_NAMESPACE,
        owner_hub_id="syn-owner",
        detail="task already held",
    )
    result = await forward_claim(
        _request(),
        uri="ws://owner/",
        local_id="f",
        connector=_connector(_FakeSocket([_result_frame(denied)])),
    )
    assert result.granted is False
    assert result.detail == "task already held"
    assert result.grant is None


async def test_forward_carries_a_token_on_the_request() -> None:
    socket = _FakeSocket([_result_frame(_granted())])
    await forward_claim(
        _request(), uri="ws://owner/", local_id="f", token="secret", connector=_connector(socket)
    )
    request = json.loads(socket.sent[0])
    assert request["token"] == "secret"


# --- failure modes (every one fails closed as ClaimForwardError) -------------------------


async def test_forward_raises_on_an_error_frame() -> None:
    socket = _FakeSocket([_wire({"type": MessageType.ERROR, "payload": "Rate limit exceeded."})])
    with pytest.raises(ClaimForwardError, match="refused"):
        await forward_claim(
            _request(), uri="ws://owner/", local_id="f", connector=_connector(socket)
        )


async def test_forward_raises_on_a_non_object_frame() -> None:
    socket = _FakeSocket([_wire({"type": "welcome"}), json.dumps([1, 2, 3])])
    with pytest.raises(ClaimForwardError, match="not a JSON object"):
        await forward_claim(
            _request(), uri="ws://owner/", local_id="f", connector=_connector(socket)
        )


async def test_forward_raises_on_invalid_json() -> None:
    socket = _FakeSocket(["this is not json{"])
    with pytest.raises(ClaimForwardError, match="failed"):
        await forward_claim(
            _request(), uri="ws://owner/", local_id="f", connector=_connector(socket)
        )


async def test_forward_raises_on_a_malformed_result() -> None:
    bad = _wire({"type": _RESULT, "granted": "yes", "task_id": "t1", "namespace": _NAMESPACE})
    with pytest.raises(ClaimForwardError, match="failed"):
        await forward_claim(
            _request(), uri="ws://owner/", local_id="f", connector=_connector(_FakeSocket([bad]))
        )


async def test_forward_raises_when_the_connection_closes_before_a_result() -> None:
    socket = _FakeSocket([_wire({"type": "welcome"})])  # closes after, no result
    with pytest.raises(ClaimForwardError, match="failed"):
        await forward_claim(
            _request(), uri="ws://owner/", local_id="f", connector=_connector(socket)
        )


async def test_forward_raises_on_a_dropped_connection() -> None:
    socket = _FakeSocket([OSError("connection reset")])
    with pytest.raises(ClaimForwardError, match="failed"):
        await forward_claim(
            _request(), uri="ws://owner/", local_id="f", connector=_connector(socket)
        )


async def test_forward_times_out_when_no_result_arrives() -> None:
    with pytest.raises(ClaimForwardTimeoutError, match="timed out"):
        await forward_claim(
            _request(),
            uri="ws://owner/",
            local_id="f",
            timeout=0.05,
            connector=_connector(_HangingSocket()),
        )


# --- real-socket integration against the serving half ------------------------------------


async def test_forward_claim_relays_a_real_hubs_refusal() -> None:
    # A real owning hub that governs the namespace but configures no serving policy refuses
    # every forwarded claim; the transport carries the request to its handler and decodes the
    # authentic result, proving the wire path without a mutual-TLS handshake.
    ownership = NamespaceOwnership(owners={_NAMESPACE: "syn-owner"}, local_hub_id="syn-owner")
    hub = SynapseHub(hub_id="syn-owner", namespace_ownership=ownership)
    async with running_hub(hub) as (_, uri):
        result = await forward_claim(_request(), uri=uri, local_id="peer")
    assert result.granted is False
    assert result.detail == "peer not authorised to forward claims"


async def test_forward_fails_closed_on_a_deeply_nested_reply() -> None:
    """An owning-hub reply nested past the wire depth bound refuses the claim."""
    bomb = "[" * (MAX_JSON_DEPTH + 1) + "1" + "]" * (MAX_JSON_DEPTH + 1)
    socket = _FakeSocket([_wire({"type": "welcome"}), bomb])
    with pytest.raises(ClaimForwardError, match="failed"):
        await forward_claim(
            _request(), uri="ws://owner/", local_id="f", connector=_connector(socket)
        )
