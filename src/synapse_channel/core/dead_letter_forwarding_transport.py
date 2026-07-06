# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — initiating half: hand a dead-letter blackhole pointer to the owning peer hub
"""Initiating half of cross-hub dead-letter forwarding: hand the pointer to the owning peer.

The serving half (:mod:`synapse_channel.core.handlers.dead_letter_forwarding`) records a peer's
blackhole pointer on the owning hub and tells that hub's operators; this module is the network
counterpart that reaches it. :func:`forward_dead_letter` opens a connection to the peer, sends a
single :data:`~synapse_channel.core.protocol.MessageType.DEAD_LETTER_FORWARDING` frame carrying the
:func:`~synapse_channel.core.dead_letter_forwarding.forwarding_notice` pointer, and returns.

Unlike the operator relay, forwarding is **fire-and-forget**: the pointer is an advisory signal,
not a request for a verdict, so the origin does not wait for a reply. The origin has already
written the durable audit before this runs, so a hand-off that cannot reach the peer is
best-effort — it falls back to "recorded but not delivered" rather than a lost signal. Every
failure mode — a refused or dropped connection, or a timeout — is raised as
:class:`~synapse_channel.core.dead_letter_forwarding.DeadLetterForwardError`, the one type the
caller already catches to treat forwarding as best-effort.

A forward opens a fresh connection and closes it when done, holding no live state between calls —
the on-demand posture the operator-relay and claim-forwarding transports use too. The clean close
flushes the sent frame before the socket is torn down, so the pointer reaches the peer even though
no reply is awaited.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from synapse_channel.core.dead_letter_forwarding import FORWARDING_FIELD, DeadLetterForwardError
from synapse_channel.core.protocol import MessageType, build_envelope

DEFAULT_FORWARD_TIMEOUT = 10.0
"""Seconds a single forward waits to connect and send before failing closed."""

PING_INTERVAL = 20.0
"""Keepalive ping interval, in seconds, for the per-forward connection."""


class _Socket(Protocol):
    """The minimal connection surface a forward uses: send one frame."""

    async def send(self, message: str) -> None:  # pragma: no cover - structural
        """Send one text frame to the peer."""
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


async def forward_dead_letter(
    notice: dict[str, Any],
    *,
    uri: str,
    local_id: str,
    token: str | None = None,
    timeout: float = DEFAULT_FORWARD_TIMEOUT,
    connector: _Connector = _default_connector,
) -> None:
    """Transmit a dead-letter forwarding pointer to the owning peer hub at ``uri``, fire-and-forget.

    Parameters
    ----------
    notice : dict[str, Any]
        The honesty-bound pointer from :func:`~synapse_channel.core.dead_letter_forwarding.
        forwarding_notice` — the blackholed target, its undelivered count, and the origin and owner
        hub ids. It carries no message body, so this transports a signal, never a message.
    uri : str
        The owning hub's websocket URI (``ws://`` or, with TLS, ``wss://``).
    local_id : str
        The identity stamped as the frame sender, so the peer's serving policy authorises this
        origin as the forwarding peer. It must match a grant the peer holds, or the peer drops it.
    token : str or None, optional
        An authentication token carried on the frame where a secured hub gates the first frame.
        ``None`` (the default) sends no token, for an open hub.
    timeout : float, optional
        Seconds the forward waits to connect and send before failing closed.
    connector : _Connector, optional
        Opens the peer connection; injected for testing. Defaults to a real websocket client.

    Raises
    ------
    DeadLetterForwardError
        On any transport failure: a refused or dropped connection, or a timeout. The caller
        treats this as best-effort over the already-written durable audit.
    """
    # The pointer is nested under its own field, never spread flat: its ``target`` key would
    # otherwise collide with the envelope's reserved ``target`` (the recipient) argument.
    fields: dict[str, Any] = {FORWARDING_FIELD: dict(notice)}
    if token is not None:
        fields["token"] = token
    envelope = build_envelope(local_id, MessageType.DEAD_LETTER_FORWARDING, **fields)
    try:
        await asyncio.wait_for(_transmit(envelope, uri, connector), timeout)
    except (OSError, ConnectionClosed, asyncio.TimeoutError) as exc:
        msg = f"forwarding a dead-letter signal to {uri!r} failed: {exc}"
        raise DeadLetterForwardError(msg) from exc


async def _transmit(envelope: dict[str, Any], uri: str, connector: _Connector) -> None:
    """Open a connection to ``uri``, send the framed pointer, and close.

    The context manager's clean close flushes the sent frame before the socket is torn down, so the
    pointer reaches the peer even though no reply is awaited.
    """
    async with connector(uri) as socket:
        await socket.send(json.dumps(envelope))
