# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fetching half of the cross-host multi-hub event-log pull
"""Fetching half of the cross-host multi-hub event-log pull.

The read-only follower (:mod:`synapse_channel.core.multihub_follower`) consumes a peer hub's
event log through an injected :data:`~synapse_channel.core.multihub_follower.EventFetcher`. Its
shipped fetcher, :func:`~synapse_channel.core.multihub_follower.store_fetcher`, reads a peer
:class:`~synapse_channel.core.persistence.EventStore` off a shared filesystem. This module
provides the network counterpart: :func:`network_fetcher` returns an ``EventFetcher`` that opens
a connection to a peer hub, sends a
:data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_LOG_REQUEST` for the events past a
cursor, and decodes the :data:`~synapse_channel.core.protocol.MessageType.MULTIHUB_LOG_SNAPSHOT`
the peer's serving handler (:mod:`synapse_channel.core.handlers.multihub`) replies with — both
framed by the shared codec (:mod:`synapse_channel.core.multihub_wire`).

A fetch opens a fresh connection and closes it when done, so a fetcher holds no live state
between polls. Every failure mode — a refused or dropped connection, a hub error frame, a
malformed or absent snapshot, or a timeout — is raised as :class:`MultiHubFetchError`. The
follower advances a peer's cursor only from the union it builds *after* the fetch returns, so a
raised fetch leaves the cursor unadvanced: the same fail-closed posture the read-side already
relies on, now extended across the network. The transport carries an optional token on its
request frame (the hub gates authentication on the first frame); deny-by-default peer
authorisation is layered on top separately.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.multihub_follower import EventFetcher
from synapse_channel.core.multihub_wire import (
    LogRequest,
    MultiHubWireError,
    decode_log_snapshot,
    encode_log_request,
)
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.protocol import MessageType, build_envelope

DEFAULT_FETCH_TIMEOUT = 10.0
"""Seconds a single fetch waits for the snapshot before failing closed."""

PING_INTERVAL = 20.0
"""Keepalive ping interval, in seconds, for the per-fetch connection."""


class MultiHubFetchError(RuntimeError):
    """Raised when a network fetch of a peer hub's event log fails.

    Every transport failure — connection, protocol, decode, or timeout — surfaces as this one
    type, so the follower (or an operator loop) catches a single error and leaves the peer's
    cursor unadvanced.
    """


class _Socket(Protocol):
    """The minimal connection surface a fetch uses: send a frame, receive frames."""

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


def network_fetcher(
    uri: str,
    *,
    local_id: str,
    token: str | None = None,
    limit: int | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
    connector: _Connector = _default_connector,
) -> EventFetcher:
    """Return an :data:`~synapse_channel.core.multihub_follower.EventFetcher` over a connection.

    Parameters
    ----------
    uri : str
        The peer hub's websocket URI (``ws://`` or, with TLS, ``wss://``).
    local_id : str
        The identity stamped as the request sender, so the peer addresses the snapshot back.
    token : str or None, optional
        An authentication token carried on the request frame, where a secured hub gates the
        first frame. ``None`` (the default) sends no token, for an open hub.
    limit : int or None, optional
        Maximum events per fetch. ``None`` (the default) leaves the batch uncapped; a cap lets
        the follower walk a large backlog forward one bounded batch per poll.
    timeout : float, optional
        Seconds a fetch waits for the snapshot before failing closed.
    connector : _Connector, optional
        Opens the peer connection; injected for testing. Defaults to a real websocket client.

    Returns
    -------
    EventFetcher
        An async callable ``fetch(after_seq)`` returning the peer's events past the cursor, or
        raising :class:`MultiHubFetchError` on any failure.
    """

    async def fetch(after_seq: int) -> Sequence[StoredEvent]:
        fields: dict[str, Any] = dict(
            encode_log_request(LogRequest(after_seq=after_seq, limit=limit))
        )
        if token is not None:
            fields["token"] = token
        request = build_envelope(local_id, MessageType.MULTIHUB_LOG_REQUEST, **fields)
        try:
            async with connector(uri) as socket:
                await socket.send(json.dumps(request))
                frame = await asyncio.wait_for(_await_snapshot(socket), timeout)
            snapshot = decode_log_snapshot(frame)
        except MultiHubFetchError:
            raise
        except (
            OSError,
            ConnectionClosed,
            TimeoutError,
            MultiHubWireError,
            json.JSONDecodeError,
        ) as exc:
            msg = f"multi-hub fetch from {uri!r} failed: {exc}"
            raise MultiHubFetchError(msg) from exc
        return snapshot.events

    return fetch


async def _await_snapshot(socket: _Socket) -> dict[str, Any]:
    """Read frames from ``socket`` until the log snapshot arrives.

    Frames that are neither the snapshot nor an error (a welcome, a presence broadcast) are
    skipped. A hub error frame raises, so a refusal fails the fetch rather than hanging until
    the timeout.

    Parameters
    ----------
    socket : _Socket
        The open peer connection.

    Returns
    -------
    dict[str, Any]
        The decoded snapshot frame.

    Raises
    ------
    MultiHubFetchError
        If the hub replies with an error frame, or a frame is not a JSON object.
    """
    while True:
        frame = _parse_frame(await socket.recv())
        frame_type = frame.get("type")
        if frame_type == MessageType.MULTIHUB_LOG_SNAPSHOT:
            return frame
        if frame_type == MessageType.ERROR:
            msg = f"peer hub refused the multi-hub log request: {frame.get('payload')!r}"
            raise MultiHubFetchError(msg)


def _parse_frame(raw: str | bytes) -> dict[str, Any]:
    """Decode one wire frame to a JSON object, or raise :class:`MultiHubFetchError`."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        msg = "peer hub sent a frame that is not a JSON object"
        raise MultiHubFetchError(msg)
    return decoded
