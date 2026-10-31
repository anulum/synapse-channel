# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — peer-bound owned protected request ingress
"""Require kernel Unix peers and retain protected request tasks through shutdown."""

from __future__ import annotations

import asyncio
import socket
import struct

from websockets.asyncio.server import ServerConnection

from synapse_channel.core.protected_write_transport import ProtectedWriteTransport


class ProtectedWriteUnixIngress:
    """Apply a trusted peer allowlist before signed ingress and own request tasks.

    Parameters
    ----------
    ingress:
        Real protected transport sharing the authority actor and current registry.
    allowed_uids:
        Immutable independently selected peer UID set. This component checks
        actual SO_PEERCRED; the deployment lifecycle additionally requires
        dedicated roles, root listener custody and private credential sources.
    max_connections:
        Positive concurrent request bound, at most 128.

    Notes
    -----
    Peer UID permission does not replace per-frame signatures, current session
    checks or authority ordering. No protected message enters signed parsing or
    dispatch before the peer gate. The listener must use ``connection_type()``
    to apply that gate and the connection cap before the opening handshake too.
    The owner must await ``aclose`` before closing replay/journal/key stores.
    """

    def __init__(
        self,
        ingress: ProtectedWriteTransport,
        *,
        allowed_uids: frozenset[int],
        max_connections: int = 32,
    ) -> None:
        """Retain immutable peer UID policy and a bounded accepted-request capacity.

        The caller owns the protected transport. Construction opens no listener
        and starts no request tasks. Raise ValueError unless the UID set is a
        nonempty bounded frozenset of native nonnegative integers and capacity is
        a native integer from one through 128.
        """
        if (
            type(allowed_uids) is not frozenset
            or not 0 < len(allowed_uids) <= 128
            or any(type(uid) is not int or uid < 0 for uid in allowed_uids)
            or type(max_connections) is not int
            or not 0 < max_connections <= 128
        ):
            raise ValueError("invalid protected Unix ingress peer or capacity policy")
        self._ingress = ingress
        self._uids = allowed_uids
        self._maximum = max_connections
        self._closing = False
        self._tasks: dict[asyncio.Task[None], ServerConnection] = {}
        self._connections: set[ServerConnection] = set()

    def connection_type(self) -> type[ServerConnection]:
        """Create the listener protocol that gates real peers before any handshake task.

        Returns
        -------
        type[ServerConnection]
            Pass as ``create_connection`` to the owned WebSocket server. Capacity
            includes idle opening handshakes as well as upgraded connections.
            Rejected sockets are closed before the server registers a handler.
        """
        uids, connections = self._uids, self._connections

        def available() -> bool:
            """Read current shutdown and capacity policy without exporting adapter internals."""
            return not self._closing and len(connections) < self._maximum

        class PeerConnection(ServerConnection):
            """Retain one accepted transport slot until the native connection is lost."""

            admitted = False

            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                """Gate native credentials and total capacity before opening-handshake setup."""
                peer = transport.get_extra_info("socket")
                permitted = False
                if peer.family == socket.AF_UNIX:
                    _pid, uid, _gid = struct.unpack(
                        "3i",
                        peer.getsockopt(
                            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                        ),
                    )
                    permitted = uid in uids
                if not available() or not permitted:
                    transport.close()
                    return
                connections.add(self)
                self.admitted = True
                super().connection_made(transport)

            def connection_lost(self, exc: Exception | None) -> None:
                """Release the accepted slot; rejected transports have no handshake resources."""
                if self.admitted:
                    connections.discard(self)
                    super().connection_lost(exc)

        return PeerConnection

    async def __call__(self, connection: ServerConnection) -> None:
        """Require an actual allowed Unix peer before entering signed ingress.

        Parameters
        ----------
        connection:
            Accepted WebSocket connection; identity comes from its kernel socket.
        """
        if self._closing or len(self._tasks) >= self._maximum:
            await connection.close(code=1013, reason="protected service unavailable")
            return
        peer = connection.transport.get_extra_info("socket")
        if peer.family != socket.AF_UNIX:
            await connection.close(code=1008, reason="protected peer refused")
            return
        _pid, uid, _gid = struct.unpack(
            "3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        )
        if uid not in self._uids:
            await connection.close(code=1008, reason="protected peer refused")
            return
        task = asyncio.create_task(self._ingress(connection))
        self._tasks[task] = connection
        try:
            await task
        finally:
            self._tasks.pop(task)

    async def aclose(self) -> None:
        """Abort connections and cancel/drain owned requests before store teardown.

        Already durable mutations are not rolled back. An aborted response is
        not an effective receipt; recovery must inspect the retained journal.
        Repeated close is harmless. New connections remain refused after close.
        """
        self._closing = True
        for connection in tuple(self._connections):
            connection.transport.abort()
        pending = tuple(self._tasks.items())
        for task, connection in pending:
            connection.transport.abort()
            task.cancel()
        await asyncio.gather(*(task for task, _connection in pending), return_exceptions=True)
