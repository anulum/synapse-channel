# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the listener one hub binds
"""The socket one hub binds, and the readiness around it."""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from typing import Any

from websockets.asyncio.server import Server, serve

from synapse_channel.core.hub_connection import shared_port_sockets
from synapse_channel.core.hub_defaults import DEFAULT_HOST, DEFAULT_PORT
from synapse_channel.core.hub_service_lifetime import HubServiceLifetime

logger = logging.getLogger("synapse.hub")

# The websockets server logs its connection lifecycle here — a descendant of the
# ``synapse`` logger, so its records reach the app's handler, where
# HandshakeAbortFilter (installed by configure_logging) quiets only the benign
# aborted-handshake tracebacks. websockets creates a per-connection *child* of
# this logger, so the filter must live on the handler (which sees child records),
# not on this logger (whose filters a child record bypasses).
ws_server_logger = logging.getLogger("synapse.hub.ws")


class HubServer:
    """Own the listening socket, its bind address and the readiness event.

    The constructor keywords are the fixed listener constants. The message
    size and the close timeout are read when the listener opens. ``bind_host``
    and ``bind_port`` are read-only: only serving and recording a bound socket
    change them. ``bound_address`` is ``None`` until a bind succeeds and
    after the listener stops.
    """

    def __init__(
        self,
        *,
        max_queue: int,
        ping_interval: float,
        ping_timeout: float,
    ) -> None:
        self._serving = asyncio.Event()
        self._bind_host = DEFAULT_HOST
        self._bind_port = DEFAULT_PORT
        self._bound_address: tuple[str, int] | None = None
        self._max_queue = max_queue
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout

    @property
    def bind_host(self) -> str:
        """Host the current attempt binds, including the value seen by the HTTP guard."""
        return self._bind_host

    @property
    def bind_port(self) -> int:
        """Port the current attempt binds, replaced by the kernel assignment when it is 0."""
        return self._bind_port

    @property
    def bound_address(self) -> tuple[str, int] | None:
        """Return the active TCP bind address, including an assigned port.

        The value is ``None`` before serving has bound its socket and after
        that server stops. In particular, callers that request port ``0``
        receive the kernel-assigned port instead of having to perform a racy
        reserve-and-release probe. Every address the host name resolves to
        listens on that same port.
        """
        return self._bound_address

    async def wait_until_serving(self, timeout: float = 3.0) -> tuple[str, int]:
        """Wait until the listener binds and return its actual TCP address.

        Parameters
        ----------
        timeout:
            Maximum seconds to wait for the socket bind.

        Returns
        -------
        tuple[str, int]
            Host and kernel-assigned port of the active listening socket.

        Raises
        ------
        TimeoutError
            If the listener does not bind before ``timeout``.
        RuntimeError
            If readiness is signalled without an active address.
        """
        await asyncio.wait_for(self._serving.wait(), timeout=timeout)
        if self._bound_address is None:
            raise RuntimeError("hub signalled readiness without a bound address")
        return self._bound_address

    async def serve(
        self,
        host: str,
        port: int,
        ssl_context: ssl.SSLContext | None,
        *,
        stop: asyncio.Event,
        services: HubServiceLifetime,
        handler: Callable[..., Any],
        process_request: Callable[..., Any],
        max_size: int,
        close_timeout: float,
    ) -> None:
        """Bind until ``stop``, then clear the address and the readiness event."""
        self._bind_host = host
        self._bind_port = int(port)
        self._bound_address = None
        self._serving.clear()
        started = False
        listener: list[socket.socket] | None = None
        adopted = 0
        try:
            listener = shared_port_sockets(host, int(port))
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(services.monitoring())
                open_listener = self._opening(
                    host,
                    port,
                    ssl_context,
                    handler,
                    process_request,
                    max_size,
                    close_timeout,
                )
                if listener is None:
                    server = await stack.enter_async_context(open_listener(None))
                    await server.start_serving()
                    self._record_bound(server.sockets, host)
                else:
                    bound: list[socket.socket] = []
                    for listening in listener:
                        server = await stack.enter_async_context(open_listener(listening))
                        adopted += 1
                        await server.start_serving()
                        bound.extend(server.sockets)
                    self._record_bound(bound, host)
                started = True
                self._serving.set()
                self._log_address(host, ssl_context)
                services.start(stop)
                await stop.wait()
                services.check_completed()
        except BaseException:
            if listener is not None:
                for leftover in listener[adopted:]:
                    leftover.close()
            if not started:
                # A startup waiter must report a failed bind instead of a readiness timeout.
                self._serving.set()
            raise
        finally:
            await services.finish(started)
            self._bound_address = None
            if started:
                self._serving.clear()

    def _opening(
        self,
        host: str,
        port: int,
        ssl_context: ssl.SSLContext | None,
        handler: Callable[..., Any],
        process_request: Callable[..., Any],
        max_size: int,
        close_timeout: float,
    ) -> Callable[[socket.socket | None], Server]:
        """Return the opener that passes these limits for every socket of this attempt."""

        def open_listener(listening: socket.socket | None) -> Server:
            return self._open(
                host,
                port,
                ssl_context,
                listening,
                handler,
                process_request,
                max_size,
                close_timeout,
            )

        return open_listener

    def _open(
        self,
        host: str,
        port: int,
        ssl_context: ssl.SSLContext | None,
        listening: socket.socket | None,
        handler: Callable[..., Any],
        process_request: Callable[..., Any],
        max_size: int,
        close_timeout: float,
    ) -> Server:
        """Open one listener. Accepting waits for the caller's ``start_serving``.

        A cancellation that arrives while the listener opens still closes its
        socket. ``listening`` is one already bound socket that shares its port
        with the hub's other addresses.
        """
        if listening is None:
            return serve(
                handler,
                host,
                port,
                max_size=max_size,
                max_queue=self._max_queue,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
                close_timeout=close_timeout,
                process_request=process_request,
                ssl=ssl_context,
                logger=ws_server_logger,
                start_serving=False,
            )
        return serve(
            handler,
            sock=listening,
            max_size=max_size,
            max_queue=self._max_queue,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
            close_timeout=close_timeout,
            process_request=process_request,
            ssl=ssl_context,
            logger=ws_server_logger,
            start_serving=False,
        )

    def _record_bound(self, sockets: Sequence[socket.socket], host: str) -> None:
        """Record the listening sockets before publishing successful readiness."""
        if not sockets:
            raise RuntimeError("hub server bound without a listening socket")
        ports = {int(listening.getsockname()[1]) for listening in sockets}
        if len(ports) != 1:
            raise RuntimeError("hub server bound different ports for one name")
        self._bind_port = next(iter(ports))
        self._bound_address = (host, self._bind_port)

    def _log_address(self, host: str, ssl_context: ssl.SSLContext | None) -> None:
        """Log the actual assigned port with its existing transport scheme."""
        scheme = "wss" if ssl_context is not None else "ws"
        logger.info(
            "Synapse Hub running on %s://%s:%d",
            scheme,
            host,
            self._bind_port,
        )
