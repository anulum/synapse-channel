# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — network client that forwards one message to a peer hub
"""Forwarding half of cross-hub messaging: hand one message to the hub that hosts its target.

:func:`forward_message` opens a connection to a configured message peer, sends one
:data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_MESSAGE_FORWARD` and returns the
decoded :data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_MESSAGE_RESULT`. Like claim
forwarding (:mod:`synapse_channel.core.multihub_claim_transport`) it holds no standing
connection: each forward connects, registers under this hub's id, and closes.

Two failure classes are kept apart because the caller treats them differently:

* :class:`MessageForwardTransportError` — the peer could not be reached or did not answer
  (refused connection, timeout, dropped socket, undecodable reply). A queued chat stays pending
  and is retried.
* :class:`MessageForwardRejectedError` — the peer answered with an ``error`` frame instead of a
  result, for example because it predates wire version 5 and does not know the frame. Retrying
  would not change that answer, so the caller settles the forward as refused.

A ``refused`` result is not an exception: it is the peer's decision, returned to the caller.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.hub_address import is_valid_hub_id
from synapse_channel.core.message_forward_wire import (
    MessageForwardRequest,
    MessageForwardResult,
    MessageForwardWireError,
    decode_message_forward_result,
    encode_message_forward_request,
)
from synapse_channel.core.multihub_transport import pinned_connector
from synapse_channel.core.protocol import MessageType, build_envelope, loads_bounded

DEFAULT_FORWARD_TIMEOUT = 10.0
"""Seconds one forward waits for the peer's result before it counts as unanswered."""

PING_INTERVAL = 20.0
"""Keepalive ping interval, in seconds, for the per-forward connection."""


class MessageForwardTransportError(SynapseError, RuntimeError):
    """The peer hub could not be reached or did not answer a forward; retrying may succeed."""

    code = "message_forward_transport"


class MessageForwardRejectedError(SynapseError, RuntimeError):
    """The peer hub answered a forward with an error frame; retrying will not help."""

    code = "message_forward_rejected"


Connector = Callable[[str], AbstractAsyncContextManager[Any]]
"""Opens one peer connection as an async context manager."""


@dataclass(frozen=True, slots=True)
class MessageForwardPeer:
    """How this hub reaches one message peer.

    Attributes
    ----------
    uri : str
        The peer hub's websocket URI (``ws://`` or, with TLS, ``wss://``).
    token : str or None
        Connect token for a peer that gates the first frame; ``None`` for an open or
        mutual-TLS-only peer.
    connector : Connector or None
        Pinned, optionally client-authenticated connection factory; ``None`` keeps the
        default system-CA transport.
    """

    uri: str
    token: str | None = None
    connector: Connector | None = field(default=None, repr=False, compare=False)


def parse_message_peers(
    values: list[str],
    *,
    token: str | None = None,
    pins: Mapping[str, str] | None = None,
    client_certificate_file: str | None = None,
    client_key_file: str | None = None,
) -> dict[str, MessageForwardPeer]:
    """Parse repeatable ``HUB_ID=URI`` values into the message-peer route map.

    Parameters
    ----------
    values : list[str]
        Raw ``--message-peer`` values, each ``HUB_ID=URI``.
    token : str or None, optional
        Connect token applied to every peer.
    pins : Mapping[str, str] or None, optional
        ``sha256:`` certificate pin per peer hub id.
    client_certificate_file, client_key_file : str or None, optional
        Paired owner-only client identity presented to peers for mutual TLS. When set, every
        peer must also be pinned, so client authentication never weakens server
        authentication.

    Returns
    -------
    dict[str, MessageForwardPeer]
        Peer hub id to its route.

    Raises
    ------
    ValueError
        If a value is not ``HUB_ID=URI``, a hub id is invalid or repeated, the URI is not
        ``ws://``/``wss://``, a client identity is half-configured or lacks a pin, or a pin
        names an unconfigured peer.
    """
    if (client_certificate_file is None) != (client_key_file is None):
        raise ValueError("multi-hub client certificate and private key must be configured together")
    peers: dict[str, MessageForwardPeer] = {}
    for value in values:
        hub_id, sep, uri = value.partition("=")
        hub_id, uri = hub_id.strip(), uri.strip()
        if not sep or not hub_id or not uri:
            raise ValueError(f"--message-peer must use HUB_ID=URI, got {value!r}")
        if not is_valid_hub_id(hub_id):
            raise ValueError(f"--message-peer hub id {hub_id!r} is not a valid hub id")
        if not uri.startswith(("ws://", "wss://")):
            raise ValueError(f"--message-peer URI for {hub_id!r} must be ws:// or wss://")
        if hub_id in peers:
            raise ValueError(f"--message-peer names hub {hub_id!r} twice")
        pin = pins.get(hub_id) if pins is not None else None
        if client_certificate_file is not None and pin is None:
            raise ValueError(
                f"multi-hub client identity requires --message-peer-pin for hub {hub_id!r}"
            )
        connector = (
            None
            if pin is None
            else cast(
                "Connector",
                pinned_connector(
                    pin,
                    client_certificate_file=client_certificate_file,
                    client_key_file=client_key_file,
                ),
            )
        )
        peers[hub_id] = MessageForwardPeer(uri=uri, token=token, connector=connector)
    if pins is not None:
        unknown = sorted(set(pins) - set(peers))
        if unknown:
            raise ValueError(
                "--message-peer-pin names hubs not configured by --message-peer: "
                + ", ".join(unknown)
            )
    return peers


class MessageForwarder(Protocol):
    """Forwards one message to a peer hub and returns its answer."""

    async def __call__(
        self,
        request: MessageForwardRequest,
        *,
        peer: MessageForwardPeer,
        local_id: str,
    ) -> MessageForwardResult:
        """Forward ``request`` to ``peer`` as ``local_id`` and return the result."""


class _Socket(Protocol):
    """The minimal connection surface a forward uses."""

    async def send(self, message: str) -> None:
        """Send one text frame."""

    async def recv(self) -> str | bytes:
        """Receive the next frame."""


def _default_connector(uri: str) -> AbstractAsyncContextManager[_Socket]:
    """Open a real websocket connection to ``uri`` with keepalive pings."""
    return cast(AbstractAsyncContextManager[_Socket], connect(uri, ping_interval=PING_INTERVAL))


async def forward_message(
    request: MessageForwardRequest,
    *,
    peer: MessageForwardPeer,
    local_id: str,
    timeout: float = DEFAULT_FORWARD_TIMEOUT,
) -> MessageForwardResult:
    """Forward one message to ``peer`` and return the peer's decoded answer.

    Parameters
    ----------
    request : MessageForwardRequest
        The message to forward.
    peer : MessageForwardPeer
        Where and how to reach the peer.
    local_id : str
        This hub's id, registered as the frame sender so the peer's serving policy can
        authorise it and address the result back.
    timeout : float, optional
        Seconds to wait for the result.

    Returns
    -------
    MessageForwardResult
        The peer's answer: accepted, duplicate or refused.

    Raises
    ------
    MessageForwardTransportError
        On a refused or dropped connection, a timeout, a result for another forward, or an
        undecodable reply.
    MessageForwardRejectedError
        When the peer answers with an ``error`` frame instead of a result.
    """
    fields: dict[str, Any] = dict(encode_message_forward_request(request))
    if peer.token is not None:
        fields["token"] = peer.token
    envelope = build_envelope(local_id, MessageType.MULTIHUB_MESSAGE_FORWARD, **fields)
    connector = peer.connector if peer.connector is not None else _default_connector
    try:
        async with connector(peer.uri) as socket:
            await socket.send(json.dumps(envelope))
            frame = await asyncio.wait_for(_await_result(socket), timeout)
        result = decode_message_forward_result(frame)
    except (MessageForwardRejectedError, MessageForwardTransportError):
        raise
    except asyncio.TimeoutError as exc:
        msg = f"peer {peer.uri!r} did not answer within {timeout:g}s"
        raise MessageForwardTransportError(msg) from exc
    except (OSError, ConnectionClosed, MessageForwardWireError, json.JSONDecodeError) as exc:
        msg = f"forwarding to {peer.uri!r} failed: {exc}"
        raise MessageForwardTransportError(msg) from exc
    if result.forward_id != request.forward_id:
        msg = (
            f"peer {peer.uri!r} answered forward {result.forward_id!r}, not {request.forward_id!r}"
        )
        raise MessageForwardTransportError(msg)
    return result


async def _await_result(socket: _Socket) -> dict[str, Any]:
    """Read frames until the forward result arrives; an ``error`` frame rejects the forward."""
    while True:
        raw = await socket.recv()
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        frame = loads_bounded(text)
        if not isinstance(frame, dict):
            raise MessageForwardTransportError("peer sent a frame that is not a JSON object")
        frame_type = frame.get("type")
        if frame_type == MessageType.MULTIHUB_MESSAGE_RESULT:
            return frame
        if frame_type == MessageType.ERROR:
            msg = f"peer refused the forward: {frame.get('payload')!r}"
            raise MessageForwardRejectedError(msg)
