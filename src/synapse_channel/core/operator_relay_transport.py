# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — initiating half: relay a governed operator action to a peer hub
"""Initiating half of a cross-hub operator relay: ask a peer hub to perform an action.

The serving half (:mod:`synapse_channel.core.handlers.operator_relay`) applies a governed
action on the acting hub behind a deny-by-default gate; this module is the network
counterpart that reaches it. :func:`relay_operator_action` opens a connection to the peer,
sends an :data:`~synapse_channel.core.protocol.MessageType.OPERATOR_RELAY_REQUEST`, and
decodes the :data:`~synapse_channel.core.protocol.MessageType.OPERATOR_RELAY_RESULT` the peer
replies with, both framed by the shared codec (:mod:`synapse_channel.core.operator_relay_wire`).

A relay opens a fresh connection and closes it when done, holding no live state between calls
— the on-demand posture the claim-forwarding transport uses too. Every failure mode — a
refused or dropped connection, a hub error frame, a malformed or absent result, or a timeout —
is raised as :class:`RelayTransportError`, so the caller distinguishes "the peer refused the
relay" (an applied=False result it can report) from "the relay never reached a verdict" (an
error it fails closed on).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    RelayActionResult,
    RelayWireError,
    decode_relay_result,
    encode_relay_request,
)
from synapse_channel.core.protocol import MessageType, build_envelope, loads_bounded

DEFAULT_RELAY_TIMEOUT = 10.0
"""Seconds a single relay waits for the peer's result before failing closed."""

PING_INTERVAL = 20.0
"""Keepalive ping interval, in seconds, for the per-relay connection."""


class RelayTransportError(SynapseError, RuntimeError):
    """Raised when relaying an operator action to a peer hub fails.

    Every transport failure — connection, protocol, decode, or timeout — surfaces as this one
    type, so the caller catches a single error and reports that the relay never reached a
    verdict rather than a result it never received.
    """

    code = "relay_transport"


@dataclass(frozen=True, slots=True)
class OperatorRelayPeer:
    """How an origin hub reaches an owning hub to relay a governed action to it.

    An origin hub maps an owning hub's id to one of these so a relay it cannot apply — the
    target namespace is owned by that peer — is routed to the hub that owns it. The map is
    opt-in and separate from the claim-forwarding route map: relaying a force-release is a
    more privileged capability than forwarding a claim, so an operator arms the two peer sets
    independently, and a hub with no relay route for an owner refuses the relay fail-closed
    rather than reaching a hub it was never told to relay to.

    Attributes
    ----------
    uri : str
        The owning hub's websocket URI (``ws://`` or, with TLS, ``wss://``).
    token : str or None
        An authentication token carried on the relayed request where the owner gates the
        first frame; ``None`` for an open or mutual-TLS-only owner. The origin hub holds this
        so the operator relaying through it never needs the peer's credentials directly.
    """

    uri: str
    token: str | None = None


class RelayForwarder(Protocol):
    """Relays an operator action to an owning hub and returns its verdict.

    The seam an origin hub calls to reach an owner; :func:`relay_operator_action` is the
    shipped implementation, and a test injects a stand-in so the origin-routing wiring is
    exercised without a real owner connection.
    """

    async def __call__(
        self,
        request: RelayActionRequest,
        *,
        uri: str,
        local_id: str,
        token: str | None = None,
    ) -> RelayActionResult:  # pragma: no cover
        """Relay ``request`` to the owner at ``uri`` and return the result."""
        ...


class _Socket(Protocol):
    """The minimal connection surface a relay uses: send a frame, receive frames."""

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


async def relay_operator_action(
    request: RelayActionRequest,
    *,
    uri: str,
    local_id: str,
    token: str | None = None,
    timeout: float = DEFAULT_RELAY_TIMEOUT,
    connector: _Connector = _default_connector,
) -> RelayActionResult:
    """Relay an operator action to the peer hub at ``uri`` and return its verdict.

    Parameters
    ----------
    request : RelayActionRequest
        The action, namespace, task, and asserted operator/origin provenance to relay.
    uri : str
        The peer hub's websocket URI (``ws://`` or, with TLS, ``wss://``).
    local_id : str
        The identity stamped as the request sender, so the peer addresses the result back and
        its serving policy authorises this origin as the relaying peer. It must match a grant
        the peer's serving policy holds, or the relay is refused.
    token : str or None, optional
        An authentication token carried on the request frame, where a secured hub gates the
        first frame. ``None`` (the default) sends no token, for an open hub.
    timeout : float, optional
        Seconds the relay waits for the result before failing closed.
    connector : _Connector, optional
        Opens the peer connection; injected for testing. Defaults to a real websocket client.

    Returns
    -------
    RelayActionResult
        The peer hub's verdict — applied, or refused with a reason — for the caller to report.

    Raises
    ------
    RelayTransportError
        On any transport failure: a refused or dropped connection, a hub error frame, a
        malformed or absent result, or a timeout.
    """
    fields: dict[str, Any] = dict(encode_relay_request(request))
    if token is not None:
        fields["token"] = token
    envelope = build_envelope(local_id, MessageType.OPERATOR_RELAY_REQUEST, **fields)
    try:
        async with connector(uri) as socket:
            await socket.send(json.dumps(envelope))
            frame = await asyncio.wait_for(_await_result(socket), timeout)
        return decode_relay_result(frame)
    except RelayTransportError:
        raise
    except (
        OSError,
        ConnectionClosed,
        asyncio.TimeoutError,
        RelayWireError,
        json.JSONDecodeError,
    ) as exc:
        msg = f"relaying an operator action to {uri!r} failed: {exc}"
        raise RelayTransportError(msg) from exc


async def _await_result(socket: _Socket) -> dict[str, Any]:
    """Read frames from ``socket`` until the relay result arrives.

    Frames that are neither the result nor an error (a welcome, a presence broadcast, the
    system notice the peer fans out to its own agents) are skipped. A hub error frame raises,
    so a refusal fails the relay rather than hanging until the timeout.

    Raises
    ------
    RelayTransportError
        If the hub replies with an error frame, or a frame is not a JSON object.
    """
    while True:
        frame = _parse_frame(await socket.recv())
        frame_type = frame.get("type")
        if frame_type == MessageType.OPERATOR_RELAY_RESULT:
            return frame
        if frame_type == MessageType.ERROR:
            msg = f"peer hub refused the relayed action: {frame.get('payload')!r}"
            raise RelayTransportError(msg)


def _parse_frame(raw: str | bytes) -> dict[str, Any]:
    """Decode one wire frame to a JSON object, or raise :class:`RelayTransportError`.

    The peer hub is a trust boundary, so its verdict is decoded with the same depth-bounded
    loader the hub applies to its own inbound frames — a deeply nested reply raises
    :class:`json.JSONDecodeError` and the relay fails closed instead of recursing.
    """
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    decoded = loads_bounded(text)
    if not isinstance(decoded, dict):
        msg = "peer hub sent a frame that is not a JSON object"
        raise RelayTransportError(msg)
    return decoded
