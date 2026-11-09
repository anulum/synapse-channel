# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — authority-owned protected frontend control
"""Route native frontend channels into the existing protected authority."""

from __future__ import annotations

import asyncio
import secrets
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from synapse_channel.core.protected_write_control_policy import (
    ProtectedWriteControlPeer,
    ProtectedWriteControlPolicy,
)
from synapse_channel.core.protected_write_control_wire import (
    ProtectedWriteControlBinding,
    read_protected_write_control_frame,
)
from synapse_channel.core.protected_write_operator_socket import retained_listener_options
from synapse_channel.core.protected_write_transport import ProtectedWriteTransport
from synapse_channel.core.protected_write_unix_exchange import require_native_unix_credentials


class ProtectedWriteControlServer:
    """Own bounded native connections for exactly one enrolled frontend process.

    Parameters
    ----------
    ingress:
        Existing authority ingress sharing its actor, registry and replay ledger.
    frontend_peer:
        Supervisor-enrolled expected PID, UID and GID, checked by the kernel.
    policy:
        Immutable continuity, incarnation, wire, deadline and aggregate ceilings.

    Notes
    -----
    This component launches no actor and supplies no replacement/restart API.
    The supervisor owns root endpoint ancestry, ACLs and retained peer lifetime.
    No ordinary frame or operator command is routed by this channel.
    """

    def __init__(
        self,
        ingress: ProtectedWriteTransport,
        *,
        frontend_peer: ProtectedWriteControlPeer,
        policy: ProtectedWriteControlPolicy,
    ) -> None:
        require_native_unix_credentials(sys.platform, hasattr(socket, "SO_PEERCRED"))
        ProtectedWriteControlBinding(policy, "0" * 64).validate_capacity(ingress.max_wire_bytes)
        self._ingress = ingress
        self._peer = frontend_peer
        self._policy = policy
        self._tasks: dict[asyncio.Task[None], asyncio.StreamWriter] = {}
        self._closing = False

    def __call__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Admit a native peer and reserve capacity before creating a handler task.

        Parameters
        ----------
        reader, writer:
            Actual accepted Unix stream pair, transferred to this owner.
        """
        try:
            if (
                self._closing
                or len(self._tasks) >= self._policy.max_connections
                or (len(self._tasks) + 1) * self._policy.connection_bytes
                > self._policy.max_inflight_bytes
            ):
                raise ValueError("protected frontend capacity unavailable")
            self._peer.verify(writer)
        except (ValueError, OSError):
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks[task] = writer
        task.add_done_callback(self._tasks.pop)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        binding = ProtectedWriteControlBinding(self._policy, secrets.token_hex(32))
        session = self._ingress.open_session()
        sequence = 1
        try:
            async with asyncio.timeout(self._policy.timeout_seconds):
                writer.write(binding.encode("hello", 0, ""))
                await writer.drain()
            while True:
                async with asyncio.timeout(self._policy.timeout_seconds):
                    try:
                        raw = await read_protected_write_control_frame(reader, self._policy)
                        request = binding.decode(raw, kind="request", sequence=sequence)
                        response = await session.exchange(request)
                    except ValueError:
                        writer.write(binding.encode("refused", sequence, ""))
                        await writer.drain()
                        return
                    writer.write(binding.encode("result", sequence, response.decode("utf-8")))
                    await writer.drain()
                sequence += 1
        except Exception:
            # No reply is evidence of success; reconcile the original journal identity.
            writer.transport.abort()
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def aclose(self) -> None:
        """Refuse new peers, cancel and drain all owned work before stores close.

        Notes
        -----
        The existing mutation actor drains any admitted durable transaction.
        Closing a response does not roll it back or authorise a retry. Repeated
        closure remains terminal and does not reopen this server object.
        """
        self._closing = True
        pending = tuple(self._tasks.items())
        for task, writer in pending:
            writer.close()
            task.cancel()
        await asyncio.gather(*(task for task, _ in pending), return_exceptions=True)

    @asynccontextmanager
    async def serve(self, listener: socket.socket) -> AsyncIterator[None]:
        """Serve an enrolled prebound Unix listener and own its complete lifetime.

        Parameters
        ----------
        listener:
            Supervisor-owned native Unix socket, bound but not yet listening,
            transferred on server creation. Listening starts in the authority
            process so the connecting frontend observes its native credentials.
            No pathname or account is created or removed by this component.

        Yields
        ------
        None
            After the listener starts. Caller-owned stores must outlive exit.

        Raises
        ------
        ValueError
            If the server was closed or the supplied endpoint is not Unix.
        OSError
            If native listener setup fails.
        """
        if (
            self._closing
            or listener.family != socket.AF_UNIX
            or listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        ):
            raise ValueError("enrolled Unix control listener required")
        server = await asyncio.start_unix_server(
            self,
            sock=listener,
            limit=self._policy.json_limits.max_wire_bytes,
            backlog=min(self._policy.max_connections, socket.SOMAXCONN),
            start_serving=False,
            **retained_listener_options(),
        )
        try:
            await server.start_serving()
            yield
        finally:
            server.close()
            try:
                await self.aclose()
            finally:
                await server.wait_closed()
