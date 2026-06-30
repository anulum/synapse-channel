# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — forwarding half: ask a namespace's owning hub to grant a claim
"""Forwarding half of cross-hub claim routing: ask the owning hub to grant a claim.

When an agent claims a namespace through a hub that does not own it, that hub must route the
claim to the hub that does (:mod:`synapse_channel.core.namespace_ownership`). The serving half
(:mod:`synapse_channel.core.handlers.multihub_claim`) grants such a forwarded claim on the
owning side; this module is the network counterpart that reaches it. :func:`forward_claim`
opens a connection to the owning hub, sends a
:data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_CLAIM_REQUEST`, and decodes the
:data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_CLAIM_RESULT` the owner replies with,
both framed by the shared codec (:mod:`synapse_channel.core.multihub_claim_wire`).

A forward opens a fresh connection and closes it when done, holding no live state between claims
— the on-demand posture that keeps a non-owning hub from carrying a standing outbound surface.
Every failure mode — a refused or dropped connection, a hub error frame, a malformed or absent
result, or a timeout — is raised as :class:`ClaimForwardError`. The caller (the non-owning hub's
claim gate) relays a returned result to its client, and on a raised error falls back to refusing
the claim and naming the owner, so an unreachable owner or a split never lets a claim slip
through ungranted-but-believed-granted: the same fail-closed posture the local grant path holds.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.multihub_claim_wire import (
    ClaimForwardRequest,
    ClaimForwardResult,
    ClaimWireError,
    decode_claim_forward_result,
    encode_claim_forward_request,
)
from synapse_channel.core.protocol import MessageType, build_envelope

DEFAULT_FORWARD_TIMEOUT = 10.0
"""Seconds a single forward waits for the owner's result before failing closed."""

PING_INTERVAL = 20.0
"""Keepalive ping interval, in seconds, for the per-forward connection."""


class ClaimForwardError(RuntimeError):
    """Raised when forwarding a claim to its owning hub fails.

    Every transport failure — connection, protocol, decode, or timeout — surfaces as this one
    type, so the non-owning hub's claim gate catches a single error and falls back to refusing
    the claim and naming the owner rather than relaying a result it never received.
    """


@dataclass(frozen=True, slots=True)
class ClaimForwardPeer:
    """How a non-owning hub reaches an owning hub to forward a claim to it.

    A non-owning hub maps an owning hub's id to one of these so a claim it does not own is
    routed to the hub that does. The map is opt-in: a hub with no entry for an owner refuses
    such a claim and names the owner, exactly as before, so no existing deployment changes.

    Attributes
    ----------
    uri : str
        The owning hub's websocket URI (``ws://`` or, with TLS, ``wss://``).
    token : str or None
        An authentication token carried on the forwarded request where the owner gates the
        first frame; ``None`` for an open or mutual-TLS-only owner.
    """

    uri: str
    token: str | None = None


class ClaimForwarder(Protocol):
    """Forwards a claim to an owning hub and returns its verdict.

    The seam the non-owning hub calls; :func:`forward_claim` is the shipped implementation, and
    a test injects a stand-in so the hub wiring is exercised without a real owner connection.
    """

    async def __call__(
        self,
        request: ClaimForwardRequest,
        *,
        uri: str,
        local_id: str,
        token: str | None = None,
    ) -> ClaimForwardResult:  # pragma: no cover
        """Forward ``request`` to the owner at ``uri`` and return the result."""
        ...


class _Socket(Protocol):
    """The minimal connection surface a forward uses: send a frame, receive frames."""

    async def send(self, message: str) -> None:  # pragma: no cover
        """Send one text frame to the peer."""
        ...

    async def recv(self) -> str | bytes:  # pragma: no cover
        """Receive the next frame from the peer."""
        ...


class _Connector(Protocol):
    """Opens a peer connection as an async context manager yielding a :class:`_Socket`."""

    def __call__(self, uri: str) -> AbstractAsyncContextManager[_Socket]:  # pragma: no cover
        """Open a connection to ``uri``."""
        ...


def _default_connector(uri: str) -> AbstractAsyncContextManager[_Socket]:
    """Open a real websocket connection to ``uri`` with keepalive pings.

    A ``wss://`` URI negotiates TLS through the ``websockets`` library's default context.
    """
    return cast(AbstractAsyncContextManager[_Socket], connect(uri, ping_interval=PING_INTERVAL))


async def forward_claim(
    request: ClaimForwardRequest,
    *,
    uri: str,
    local_id: str,
    token: str | None = None,
    timeout: float = DEFAULT_FORWARD_TIMEOUT,
    connector: _Connector = _default_connector,
) -> ClaimForwardResult:
    """Forward a claim to the owning hub at ``uri`` and return its authoritative result.

    Parameters
    ----------
    request : ClaimForwardRequest
        The namespace, claimant, task id, and claim body to grant on the owning hub.
    uri : str
        The owning hub's websocket URI (``ws://`` or, with TLS, ``wss://``).
    local_id : str
        The identity stamped as the request sender, so the owner addresses the result back and
        its serving policy authorises this hub as the forwarding peer.
    token : str or None, optional
        An authentication token carried on the request frame, where a secured hub gates the
        first frame. ``None`` (the default) sends no token, for an open hub.
    timeout : float, optional
        Seconds the forward waits for the result before failing closed.
    connector : _Connector, optional
        Opens the owner connection; injected for testing. Defaults to a real websocket client.

    Returns
    -------
    ClaimForwardResult
        The owning hub's verdict — granted with the authentic grant fields, or a denial with a
        reason — for the caller to relay to its client.

    Raises
    ------
    ClaimForwardError
        On any transport failure: a refused or dropped connection, a hub error frame, a
        malformed or absent result, or a timeout. The caller falls back to refusing the claim.
    """
    fields: dict[str, Any] = dict(encode_claim_forward_request(request))
    if token is not None:
        fields["token"] = token
    envelope = build_envelope(local_id, MessageType.MULTIHUB_CLAIM_REQUEST, **fields)
    try:
        async with connector(uri) as socket:
            await socket.send(json.dumps(envelope))
            frame = await asyncio.wait_for(_await_result(socket), timeout)
        return decode_claim_forward_result(frame)
    except ClaimForwardError:
        raise
    except (
        OSError,
        ConnectionClosed,
        asyncio.TimeoutError,
        ClaimWireError,
        json.JSONDecodeError,
    ) as exc:
        msg = f"forwarding a claim to {uri!r} failed: {exc}"
        raise ClaimForwardError(msg) from exc


async def _await_result(socket: _Socket) -> dict[str, Any]:
    """Read frames from ``socket`` until the claim result arrives.

    Frames that are neither the result nor an error (a welcome, a presence broadcast, the
    grant broadcast the owner fans out to its own agents) are skipped. A hub error frame
    raises, so a refusal fails the forward rather than hanging until the timeout.

    Parameters
    ----------
    socket : _Socket
        The open owner connection.

    Returns
    -------
    dict[str, Any]
        The decoded result frame.

    Raises
    ------
    ClaimForwardError
        If the hub replies with an error frame, or a frame is not a JSON object.
    """
    while True:
        frame = _parse_frame(await socket.recv())
        frame_type = frame.get("type")
        if frame_type == MessageType.MULTIHUB_CLAIM_RESULT:
            return frame
        if frame_type == MessageType.ERROR:
            msg = f"owning hub refused the forwarded claim: {frame.get('payload')!r}"
            raise ClaimForwardError(msg)


def _parse_frame(raw: str | bytes) -> dict[str, Any]:
    """Decode one wire frame to a JSON object, or raise :class:`ClaimForwardError`."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        msg = "owning hub sent a frame that is not a JSON object"
        raise ClaimForwardError(msg)
    return decoded
