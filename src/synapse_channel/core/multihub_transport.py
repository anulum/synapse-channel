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
authorisation is layered on top separately. For a self-signed or private-CA ``wss://`` peer,
:func:`pinned_connector` trusts the peer by SHA-256 certificate pin instead of CA chain,
mirroring the federation-bundle fetch ceremony.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.clock_skew import ClockSkew, measure_clock_skew
from synapse_channel.core.multihub_federation import MultiHubAuthoriser
from synapse_channel.core.multihub_follower import EventFetcher
from synapse_channel.core.multihub_wire import (
    LogRequest,
    MultiHubWireError,
    decode_log_snapshot,
    encode_log_request,
)
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.protocol import (
    MessageType,
    ProtocolNegotiation,
    build_envelope,
    loads_bounded,
    negotiate_protocol_version,
    read_protocol_version,
)
from synapse_channel.core.tls import (
    HubTLSConfigError,
    live_peer_certificate_pin,
    pin_trust_client_context,
)

logger = logging.getLogger(__name__)
"""Module logger for operator-visible multi-hub transport warnings."""

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


class _ExtraInfoTransport(Protocol):
    """Transport surface needed to inspect the live TLS object."""

    def get_extra_info(self, name: str, default: object = None) -> object:  # pragma: no cover
        """Return transport metadata by name."""
        ...


class _PinnedSocket(_Socket, Protocol):
    """A websocket client connection with transport metadata."""

    transport: _ExtraInfoTransport


def pinned_connector(expected_pin: str) -> _Connector:
    """Return a connector that accepts a ``wss://`` peer certificate only by SHA-256 pin.

    The pull-side counterpart of
    :func:`synapse_channel.core.federation_fetch.pinned_connector`: it lets a
    follower pull a self-signed or private-CA peer hub over TLS without any CA
    dependency, matching the pin the operator recorded from the peer's federation
    bundle (`docs/federated-trust-model.md`).

    Parameters
    ----------
    expected_pin : str
        Certificate pin in ``sha256:<hex>`` form. The TLS handshake uses an
        unverified client context because trust is by pin, not chain; immediately
        after the handshake, the live peer certificate is hashed and compared to
        this value. A missing TLS object, absent certificate, malformed
        certificate, or mismatch fails the fetch before any snapshot frame is
        trusted, so the follower's cursor is left unadvanced.

    Returns
    -------
    _Connector
        Connector suitable for :func:`network_fetcher`.
    """
    normalized = expected_pin.strip().lower()

    @contextlib.asynccontextmanager
    async def _open(uri: str) -> AsyncIterator[_Socket]:
        if not uri.startswith("wss://"):
            msg = "--pin requires a wss:// peer URI"
            raise MultiHubFetchError(msg)
        context = pin_trust_client_context()
        async with connect(uri, ping_interval=PING_INTERVAL, ssl=context) as socket:
            pinned = cast(_PinnedSocket, socket)
            actual = _live_certificate_pin(pinned)
            if actual.lower() != normalized:
                msg = f"peer certificate pin mismatch: expected {normalized}, got {actual}"
                raise MultiHubFetchError(msg)
            yield pinned

    def _factory(uri: str) -> AbstractAsyncContextManager[_Socket]:
        return _open(uri)

    return _factory


def _live_certificate_pin(socket: _PinnedSocket) -> str:
    """Return the SHA-256 pin for ``socket``'s live peer certificate."""
    try:
        return live_peer_certificate_pin(socket.transport)
    except HubTLSConfigError as exc:
        raise MultiHubFetchError(str(exc)) from exc


def network_fetcher(
    uri: str,
    *,
    local_id: str,
    token: str | None = None,
    limit: int | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
    authoriser: MultiHubAuthoriser | None = None,
    connector: _Connector = _default_connector,
    protocol_warning_sink: Callable[[ProtocolNegotiation], None] | None = None,
    clock: Callable[[], float] = time.time,
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
    authoriser : MultiHubAuthoriser or None, optional
        A deny-by-default gate consulted before each fetch connects (see
        :func:`~synapse_channel.core.multihub_federation.peer_authoriser`). When supplied and the
        peer is not authorised, the fetch fails closed without connecting and the follower's
        cursor is left unadvanced. ``None`` (the default) does not gate the pull, for an open or
        already-trusted peer.
    connector : _Connector, optional
        Opens the peer connection; injected for testing. Defaults to a real websocket client.
    protocol_warning_sink : Callable[[ProtocolNegotiation], None] or None, optional
        Observer called when the peer advertises an older, newer, or absent wire
        version. The fetch still proceeds at the lowest common compatibility level.
    clock : Callable[[], float], optional
        Wall-clock source used to measure local-minus-peer skew from the peer
        welcome timestamp. Injected by tests; defaults to :func:`time.time`.

    Returns
    -------
    EventFetcher
        An async callable ``fetch(after_seq)`` returning the peer's events past the cursor, or
        raising :class:`MultiHubFetchError` on any failure.
    """
    return _NetworkFetcher(
        uri,
        local_id=local_id,
        token=token,
        limit=limit,
        timeout=timeout,
        authoriser=authoriser,
        connector=connector,
        protocol_warning_sink=protocol_warning_sink,
        clock=clock,
    )


class _NetworkFetcher:
    """Callable multi-hub event fetcher with last-observed wire negotiation metadata."""

    def __init__(
        self,
        uri: str,
        *,
        local_id: str,
        token: str | None,
        limit: int | None,
        timeout: float,
        authoriser: MultiHubAuthoriser | None,
        connector: _Connector,
        protocol_warning_sink: Callable[[ProtocolNegotiation], None] | None,
        clock: Callable[[], float],
    ) -> None:
        self._uri = uri
        self._local_id = local_id
        self._token = token
        self._limit = limit
        self._timeout = timeout
        self._authoriser = authoriser
        self._connector = connector
        self._protocol_warning_sink = protocol_warning_sink
        self._clock = clock
        self.last_protocol_negotiation: ProtocolNegotiation | None = None
        self.last_log_end_seq: int | None = None
        self.last_clock_skew: ClockSkew | None = None

    async def __call__(self, after_seq: int) -> Sequence[StoredEvent]:
        """Fetch peer events after ``after_seq`` and capture wire-version metadata."""
        if self._authoriser is not None:
            decision = self._authoriser()
            if not decision.allowed:
                msg = f"peer {self._uri!r} not authorised for a multi-hub pull: {decision.reason}"
                raise MultiHubFetchError(msg)
        fields: dict[str, Any] = dict(
            encode_log_request(LogRequest(after_seq=after_seq, limit=self._limit))
        )
        if self._token is not None:
            fields["token"] = self._token
        request = build_envelope(self._local_id, MessageType.MULTIHUB_LOG_REQUEST, **fields)
        try:
            async with self._connector(self._uri) as socket:
                await socket.send(json.dumps(request))
                frame = await asyncio.wait_for(
                    _await_snapshot(
                        socket,
                        self._record_protocol_negotiation,
                        self._record_clock_skew,
                        clock=self._clock,
                    ),
                    self._timeout,
                )
            snapshot = decode_log_snapshot(frame)
            self.last_log_end_seq = snapshot.log_end_seq
        except MultiHubFetchError:
            raise
        except (
            OSError,
            ConnectionClosed,
            asyncio.TimeoutError,
            MultiHubWireError,
            json.JSONDecodeError,
        ) as exc:
            msg = f"multi-hub fetch from {self._uri!r} failed: {exc}"
            raise MultiHubFetchError(msg) from exc
        return snapshot.events

    def _record_protocol_negotiation(self, negotiation: ProtocolNegotiation) -> None:
        """Store and report a peer wire-version negotiation result."""
        self.last_protocol_negotiation = negotiation
        if negotiation.warning is None:
            return
        logger.warning("multi-hub protocol mismatch from %s: %s", self._uri, negotiation.warning)
        if self._protocol_warning_sink is not None:
            self._protocol_warning_sink(negotiation)

    def _record_clock_skew(self, skew: ClockSkew) -> None:
        """Store the peer clock skew observed from a welcome frame."""
        self.last_clock_skew = skew


async def _await_snapshot(
    socket: _Socket,
    protocol_observer: Callable[[ProtocolNegotiation], None] | None = None,
    clock_skew_observer: Callable[[ClockSkew], None] | None = None,
    *,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
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
        if frame_type == MessageType.WELCOME and protocol_observer is not None:
            protocol_observer(
                negotiate_protocol_version(read_protocol_version(frame.get("protocol_version")))
            )
        if frame_type == MessageType.WELCOME and clock_skew_observer is not None:
            skew = measure_clock_skew(frame.get("timestamp"), observed_at=clock())
            if skew is not None:
                clock_skew_observer(skew)
        if frame_type == MessageType.MULTIHUB_LOG_SNAPSHOT:
            return frame
        if frame_type == MessageType.ERROR:
            msg = f"peer hub refused the multi-hub log request: {frame.get('payload')!r}"
            raise MultiHubFetchError(msg)


def _parse_frame(raw: str | bytes) -> dict[str, Any]:
    """Decode one wire frame to a JSON object, or raise :class:`MultiHubFetchError`.

    The peer hub is a trust boundary, so the reply is decoded with the same
    depth-bounded loader the hub applies to its own inbound frames — a deeply
    nested reply raises :class:`json.JSONDecodeError` and fails the poll closed
    (cursor unadvanced) instead of recursing.
    """
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    decoded = loads_bounded(text)
    if not isinstance(decoded, dict):
        msg = "peer hub sent a frame that is not a JSON object"
        raise MultiHubFetchError(msg)
    return decoded
