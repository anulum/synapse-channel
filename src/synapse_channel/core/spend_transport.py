# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — ask a pool owner hub to reserve, settle or look up (F02)
"""Send one ``spend_request`` to the pool owner's hub and return its private answer.

The request opens a fresh connection, sends one frame (signed with this hub's peer
identity key when one is given) and waits for the matching ``spend_result``. Every
failure is raised as :class:`SpendTransportError`, so the caller fails closed and
never treats a missing answer as a grant.

A timeout is ambiguous: the owner may have committed a grant whose answer was lost.
It is raised as :class:`SpendTransportTimeoutError`. Recover by sending ``query``
with the same seat, task, operation and key: the owner returns the stored answer
and creates nothing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidMessage

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.peer_identity import PeerRegistrationSigner, signed
from synapse_channel.core.protocol import MessageType, build_envelope, loads_bounded
from synapse_channel.core.spend_wire import (
    SpendWireError,
    decode_spend_result,
    encode_spend_request,
)

DEFAULT_SPEND_TIMEOUT = 10.0
"""Seconds one request waits for the owner's answer before failing closed."""


class SpendTransportError(SynapseError, RuntimeError):
    """Raised when a spend request gets no valid answer from the owner hub."""

    code = "spend_transport"


class SpendTransportTimeoutError(SpendTransportError):
    """Raised when the owner does not answer in time; the outcome is unknown."""

    code = "spend_transport_timeout"


class SpendSocket(Protocol):
    """The connection surface one request uses: send a frame, receive frames."""

    async def send(self, message: str) -> None:  # pragma: no cover
        """Send one text frame to the owner."""
        ...

    async def recv(self) -> str | bytes:  # pragma: no cover
        """Receive the next frame from the owner."""
        ...


Connector = Callable[[str], AbstractAsyncContextManager[SpendSocket]]
"""Opens a connection to the owner hub; the default is a plain websocket client."""


def _default_connector(uri: str) -> AbstractAsyncContextManager[SpendSocket]:
    return cast(AbstractAsyncContextManager[SpendSocket], connect(uri))


async def request_spend(
    action: str,
    document: Mapping[str, Any],
    *,
    uri: str,
    local_id: str,
    token: str | None = None,
    timeout: float = DEFAULT_SPEND_TIMEOUT,
    connector: Connector = _default_connector,
    signer: PeerRegistrationSigner | None = None,
) -> dict[str, Any]:
    """Ask the owner hub at ``uri`` to act on its ledger and return its answer.

    Parameters
    ----------
    action : str
        ``reserve``, ``settle`` or ``query``.
    document : Mapping[str, Any]
        The request, settlement or query document.
    uri : str
        The owner hub's websocket URI.
    local_id : str
        This hub's id. The owner authorises it and scopes every reservation to it.
    token : str or None
        A connect token for a secured owner hub.
    timeout : float
        Seconds to wait for the answer.
    connector : callable
        Opens the connection; a pinned mutual-TLS connector for a certificate grant.
    signer : PeerRegistrationSigner or None
        Signs the frame for an identity-key grant.

    Returns
    -------
    dict[str, Any]
        The owner's answer: a grant, a settlement, a stored response, or the uniform
        refusal.

    Raises
    ------
    SpendTransportError
        On a refused or dropped connection, an error frame or a malformed answer.
    SpendTransportTimeoutError
        When no answer arrives in time. Query by key to learn the outcome.
    """
    fields: dict[str, Any] = encode_spend_request(action, document)
    if token is not None:
        fields["token"] = token
    envelope = signed(build_envelope(local_id, MessageType.SPEND_REQUEST, **fields), signer)
    try:
        async with connector(uri) as socket:
            await socket.send(json.dumps(envelope))
            frame = await asyncio.wait_for(_await_result(socket), timeout)
        return decode_spend_result(frame, action)
    except SpendTransportError:
        raise
    except asyncio.TimeoutError as exc:
        raise SpendTransportTimeoutError(
            f"the spend owner at {uri!r} did not answer within {timeout:g}s; query by key"
        ) from exc
    except (OSError, ConnectionClosed, InvalidMessage, SpendWireError, json.JSONDecodeError) as exc:
        raise SpendTransportError(f"spend request to {uri!r} failed: {exc}") from exc


async def _await_result(socket: SpendSocket) -> dict[str, Any]:
    """Read frames until the spend result; an error frame fails the request."""
    while True:
        raw = await socket.recv()
        frame = loads_bounded(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        if not isinstance(frame, dict):
            raise SpendTransportError("the owner sent a frame that is not a JSON object")
        if frame.get("type") == MessageType.SPEND_RESULT:
            return frame
        if frame.get("type") == MessageType.ERROR:
            raise SpendTransportError(f"the owner refused the request: {frame.get('payload')!r}")
