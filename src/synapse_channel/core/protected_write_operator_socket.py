# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — local protected enrollment operator socket
"""Accept bounded issue/revoke commands only from an OS-authenticated operator."""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypedDict

from synapse_channel.core.protected_write_enrollment_control import ProtectedEnrollmentControl
from synapse_channel.core.protected_write_listener import (
    DEFAULT_FORBIDDEN_UIDS,
)
from synapse_channel.core.protected_write_listener import (
    validate_protected_authority_listener as validate_protected_authority_listener,
)

_MAX_REQUEST_BYTES = 16_384
_READ_TIMEOUT_SECONDS = 5.0
_ACCEPTED = b'{"ok":true}\n'
_REFUSED = b'{"ok":false}\n'


class RetainedListenerOptions(TypedDict, total=False):
    """Only the optional cleanup keyword accepted by newer Unix server runtimes."""

    cleanup_socket: bool


def retained_listener_options() -> RetainedListenerOptions:
    """Keep a prebound listener's pathname under its supervisor's custody.

    Returns
    -------
    dict
        On Python 3.13 and later, disable asyncio's automatic socket pathname
        removal. Older runtimes already leave the pathname to its owner and
        do not accept that keyword. This configures cleanup, never peer authority.

    Notes
    -----
    The supervisor retains the socket identity and decides whether its exact
    pathname may be removed after all serving tasks have drained.
    """
    return {"cleanup_socket": False} if sys.version_info >= (3, 13) else {}


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Decode a command object while refusing duplicate JSON keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate operator command key")
        result[key] = value
    return result


def _peer_uid(writer: asyncio.StreamWriter) -> int:
    """Read the real Unix peer UID from kernel credentials or refuse the transport."""
    transport_socket = writer.get_extra_info("socket")
    if transport_socket is None or transport_socket.family != socket.AF_UNIX:
        raise ValueError("operator control requires a Unix socket")
    raw = transport_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", raw)
    return int(uid)


async def handle_operator_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    control: ProtectedEnrollmentControl,
    operator_uid: int,
) -> None:
    """Process one frame without acknowledging an uncertain mutation outcome.

    Parameters
    ----------
    reader:
        Accepted connection supplying one bounded length-prefixed JSON command.
    writer:
        Same connection; its Unix socket supplies the actual kernel peer UID.
    control:
        Enrollment mutations bound to the retained durable authority actor.
    operator_uid:
        Independently enrolled UID permitted to issue or revoke enrollments.

    Raises
    ------
    asyncio.CancelledError
        After any in-flight durable mutation has drained under the actor.
    Exception
        On unexpected storage or transport failure; no result is fabricated.

    Notes
    -----
    Peer identity is kernel-supplied, never in JSON. Peer/frame refusals return
    ``ok:false``. Once control is invoked, even a metadata error closes without
    a result. A drained mutation may already be durable in that case,
    so an absent response requires journal inspection rather than an automatic
    retry. Stores must remain open until the owning request task is drained.
    """
    response: bytes | None = None
    try:
        try:
            if _peer_uid(writer) != operator_uid:
                raise ValueError("operator peer UID mismatch")
            header = await asyncio.wait_for(reader.readexactly(4), _READ_TIMEOUT_SECONDS)
            length = struct.unpack("!I", header)[0]
            if not 0 < length <= _MAX_REQUEST_BYTES:
                raise ValueError("operator command size refused")
            raw = await asyncio.wait_for(reader.readexactly(length), _READ_TIMEOUT_SECONDS)
            command = json.loads(raw, object_pairs_hook=_closed_object)
            if type(command) is not dict:
                raise ValueError("operator command must be an object")
            if command.get("op") == "issue" and command.keys() == {"op", "metadata"}:
                if type(command["metadata"]) is not dict:
                    raise ValueError("operator issue metadata must be an object")
            elif command.get("op") != "revoke" or command.keys() != {"op", "session_id"}:
                raise ValueError("unsupported operator command")
        except (
            ValueError,
            UnicodeError,
            json.JSONDecodeError,
            RecursionError,
            asyncio.TimeoutError,
            asyncio.IncompleteReadError,
        ):
            response = _REFUSED
        else:
            if command["op"] == "issue":
                await control.issue_from_metadata(command["metadata"])
            else:
                await control.revoke(command["session_id"])
            response = _ACCEPTED
    finally:
        try:
            if response is not None:
                writer.write(response)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


@asynccontextmanager
async def serve_operator_listener(
    listener: socket.socket,
    *,
    control: ProtectedEnrollmentControl,
    operator_uid: int,
    start_serving: bool = True,
    forbidden_uids: frozenset[int] = DEFAULT_FORBIDDEN_UIDS,
) -> AsyncIterator[asyncio.AbstractServer]:
    """Serve a prebound root listener and drain its accepted requests on exit.

    Parameters
    ----------
    listener:
        Independently retained root-bound Unix stream socket, not yet listening,
        under a root-owned non-writable parent chain.
    control:
        Enrollment control bound to the sole durable authority actor.
    operator_uid:
        Dedicated operator peer UID, distinct from root, UID1000 and this
        authority's effective UID.
    start_serving:
        Start accepts immediately. False lets the paired authority lifecycle
        construct both servers before enabling either acceptor.
    forbidden_uids:
        Explicit deployment exclusions; root remains forbidden. The default
        also excludes UID1000. Actual process credentials are always observed.

    Yields
    ------
    asyncio.AbstractServer
        Context-owned server. The authority calls listen under its actual UID.
        Stops accepts before cancelling pending commands,
        drains their tasks, then waits for transport shutdown.

    Raises
    ------
    ValueError
        On an invalid dedicated-role or root-owned listener boundary.
    OSError
        On unavailable listener metadata or server creation failure.

    Notes
    -----
    Use as an async context manager. It owns the supplied listener after server
    creation. Caller-owned journal/key stores must remain open until exit. No
    socket path is created or removed. Interrupted responses do not roll back
    already durable enrollment mutations.

    The server is acquired with accepts disabled before its cancellable start.
    Cancellation while enabling accepts therefore closes an already owned
    server before draining its commands, rather than losing the startup handle.
    """
    from synapse_channel.core.protected_write_operator_connections import (
        retain_operator_connections,
    )

    validate_protected_authority_listener(listener, operator_uid, forbidden_uids=forbidden_uids)
    listener.listen(socket.SOMAXCONN)
    validate_protected_authority_listener(
        listener, operator_uid, forbidden_uids=forbidden_uids, listening=True
    )
    server: asyncio.AbstractServer | None = None
    try:
        async with retain_operator_connections(
            control=control, operator_uid=operator_uid
        ) as handler:
            server = await asyncio.start_unix_server(
                handler,
                sock=listener,
                limit=_MAX_REQUEST_BYTES,
                start_serving=False,
                **retained_listener_options(),
            )
            try:
                if start_serving:
                    await server.start_serving()
                yield server
            finally:
                server.close()
    finally:
        if server is not None:
            await server.wait_closed()
