# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fetching half of the federation-bundle exchange
"""Fetching half of the federation-bundle exchange.

:func:`fetch_federation_offer` opens a connection to a peer hub, sends a
:data:`~synapse_channel.core.protocol.MessageType.FEDERATION_OFFER_REQUEST`, and decodes
the :data:`~synapse_channel.core.protocol.MessageType.FEDERATION_OFFER` the peer's serving
handler (:mod:`synapse_channel.core.handlers.federation_offer`) replies with — both framed
by the shared codec (:mod:`synapse_channel.core.federation_wire`).

A fetch opens a fresh connection and closes it when done. Every failure mode — a refused
or dropped connection, a hub error frame (including the peer having no offer configured),
a malformed offer, or a timeout — is raised as :class:`FederationFetchError`, so the
calling ceremony imports nothing on any failure.

What the fetch returns is **transport, not trust**: the offered material is exactly as
untrusted as a bundle file received by e-mail. The operator ceremony that consumes this —
``synapse federation fetch`` — displays the bundle fingerprints for an out-of-band
comparison and leaves the import a separate, explicitly confirmed step
(`docs/federated-trust-model.md`). There is no trust-on-first-use.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.federation import FederationPeer
from synapse_channel.core.federation_wire import FederationWireError, decode_federation_offer
from synapse_channel.core.protocol import MessageType, build_envelope, loads_bounded
from synapse_channel.core.tls import (
    HubTLSConfigError,
    live_peer_certificate_pin,
    pin_trust_client_context,
)

DEFAULT_FETCH_TIMEOUT = 10.0
"""Seconds a fetch waits for the offer before failing closed."""

PING_INTERVAL = 20.0
"""Keepalive ping interval, in seconds, for the per-fetch connection."""


class FederationFetchError(SynapseError, RuntimeError):
    """Raised when fetching a peer hub's federation-bundle offer fails.

    Every transport failure — connection, protocol, decode, or timeout — surfaces as this
    one type, so the ceremony catches a single error and imports nothing.
    """

    code = "federation_fetch"


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


class _ExtraInfoTransport(Protocol):
    """Transport surface needed to inspect the live TLS object."""

    def get_extra_info(self, name: str, default: object = None) -> object:  # pragma: no cover
        """Return transport metadata by name."""
        ...


class _PinnedSocket(_Socket, Protocol):
    """A websocket client connection with transport metadata."""

    transport: _ExtraInfoTransport


def _default_connector(uri: str) -> AbstractAsyncContextManager[_Socket]:
    """Open a real websocket connection to ``uri`` with keepalive pings.

    A ``wss://`` URI negotiates TLS through the ``websockets`` library's default context.
    """
    return cast(AbstractAsyncContextManager[_Socket], connect(uri, ping_interval=PING_INTERVAL))


def pinned_connector(expected_pin: str) -> _Connector:
    """Return a connector that accepts a ``wss://`` certificate only by SHA-256 pin.

    Parameters
    ----------
    expected_pin : str
        Certificate pin in ``sha256:<hex>`` form. The TLS handshake uses an
        unverified client context because the operator is explicitly pinning a
        self-signed or private-CA peer; immediately after the handshake, the live
        peer certificate is hashed and compared to this value. A missing TLS
        object, absent certificate, malformed certificate, or mismatch fails the
        fetch before any offer frame is trusted.

    Returns
    -------
    _Connector
        Connector suitable for :func:`fetch_federation_offer`.
    """
    normalized = expected_pin.strip().lower()

    @contextlib.asynccontextmanager
    async def _open(uri: str) -> AsyncIterator[_Socket]:
        if not uri.startswith("wss://"):
            msg = "--pin requires a wss:// federation peer URI"
            raise FederationFetchError(msg)
        context = pin_trust_client_context()
        async with connect(uri, ping_interval=PING_INTERVAL, ssl=context) as socket:
            pinned = cast(_PinnedSocket, socket)
            actual = _live_certificate_pin(pinned)
            if actual.lower() != normalized:
                msg = f"peer certificate pin mismatch: expected {normalized}, got {actual}"
                raise FederationFetchError(msg)
            yield pinned

    def _factory(uri: str) -> AbstractAsyncContextManager[_Socket]:
        return _open(uri)

    return _factory


def _live_certificate_pin(socket: _PinnedSocket) -> str:
    """Return the SHA-256 pin for ``socket``'s live peer certificate."""
    try:
        return live_peer_certificate_pin(socket.transport)
    except HubTLSConfigError as exc:
        raise FederationFetchError(str(exc)) from exc


async def fetch_federation_offer(
    uri: str,
    *,
    local_id: str,
    token: str | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
    connector: _Connector = _default_connector,
) -> FederationPeer:
    """Fetch a peer hub's offered federation-bundle material over one connection.

    Parameters
    ----------
    uri : str
        The peer hub's websocket URI (``ws://`` or, with TLS, ``wss://``).
    local_id : str
        The identity stamped as the request sender, so the peer addresses the offer back.
    token : str or None, optional
        An authentication token carried on the request frame, where a secured hub gates
        the first frame. ``None`` (the default) sends no token, for an open hub.
    timeout : float, optional
        Seconds the fetch waits for the offer before failing closed.
    connector : _Connector, optional
        Opens the peer connection; injected for testing. Defaults to a real websocket
        client.

    Returns
    -------
    FederationPeer
        The offered bundle material — untrusted until the operator compares fingerprints
        out-of-band and imports it explicitly.

    Raises
    ------
    FederationFetchError
        On any failure: connection, refusal (an error frame, including a hub with no
        offer configured), malformed material, or timeout.
    """
    fields: dict[str, Any] = {}
    if token is not None:
        fields["token"] = token
    request = build_envelope(local_id, MessageType.FEDERATION_OFFER_REQUEST, **fields)
    try:
        async with connector(uri) as socket:
            await socket.send(json.dumps(request))
            frame = await asyncio.wait_for(_await_offer(socket), timeout)
        return decode_federation_offer(frame)
    except FederationFetchError:
        raise
    except (
        OSError,
        ConnectionClosed,
        asyncio.TimeoutError,
        FederationWireError,
        json.JSONDecodeError,
    ) as exc:
        msg = f"federation-offer fetch from {uri!r} failed: {exc}"
        raise FederationFetchError(msg) from exc


async def _await_offer(socket: _Socket) -> dict[str, Any]:
    """Read frames from ``socket`` until the federation offer arrives.

    Frames that are neither the offer nor an error (a welcome, a presence broadcast) are
    skipped. A hub error frame raises, so a refusal fails the fetch rather than hanging
    until the timeout.

    Parameters
    ----------
    socket : _Socket
        The open peer connection.

    Returns
    -------
    dict[str, Any]
        The decoded offer frame.

    Raises
    ------
    FederationFetchError
        If the hub replies with an error frame, or a frame is not a JSON object.
    """
    while True:
        frame = _parse_frame(await socket.recv())
        frame_type = frame.get("type")
        if frame_type == MessageType.FEDERATION_OFFER:
            return frame
        if frame_type == MessageType.ERROR:
            msg = f"peer hub refused the federation-offer request: {frame.get('payload')!r}"
            raise FederationFetchError(msg)


def _parse_frame(raw: str | bytes) -> dict[str, Any]:
    """Decode one wire frame to a JSON object, or raise :class:`FederationFetchError`.

    The peer hub is a trust boundary, so the reply is decoded with the same depth-bounded
    loader the hub applies to its own inbound frames — a deeply nested reply raises
    :class:`json.JSONDecodeError` and fails the fetch closed instead of recursing.
    """
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    decoded = loads_bounded(text)
    if not isinstance(decoded, dict):
        msg = "peer hub sent a frame that is not a JSON object"
        raise FederationFetchError(msg)
    return decoded
