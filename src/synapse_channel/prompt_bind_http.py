# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — threading HTTP server that binds without a reverse-DNS lookup
"""Threading HTTP server that binds without a reverse-DNS lookup.

``http.server.HTTPServer.server_bind`` resolves ``server_name`` via
``socket.getfqdn(host)`` — a reverse-DNS lookup on the bind address. On macOS that
lookup can block for several seconds on loopback/CI hosts (it returns immediately
on Linux), so the accept loop only starts long after the socket is bound — past a
client's readiness deadline, which reads as a server that "listens" (the bind
completes and any banner prints) yet never answers. ``server_name`` is
informational: it is not used to bind the socket or serve requests, so binding
without it starts the accept loop promptly on every platform.

The same class ends each connection in order after its answer; see
:mod:`synapse_channel.lingering_close` for why and within which bounds.
"""

from __future__ import annotations

import socket
import socketserver
from http.server import ThreadingHTTPServer
from typing import Any, cast

from synapse_channel.lingering_close import LingeringCloser


class PromptBindThreadingHTTPServer(ThreadingHTTPServer):
    """A ``ThreadingHTTPServer`` whose bind skips the reverse-DNS FQDN lookup.

    It also ends every connection in order: a connection is not closed while
    bytes of its request are still unread, because that close would reach the
    client as a reset and, on Windows, cost it the answer it was just sent.
    See :mod:`synapse_channel.lingering_close`.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.lingering_closer = LingeringCloser()
        super().__init__(*args, **kwargs)

    def shutdown_request(self, request: Any) -> None:
        """End one connection after its answer: orderly when it can be, at once otherwise."""
        sock = request[1] if isinstance(request, tuple) else request
        if isinstance(sock, socket.socket) and self.lingering_closer.take(sock):
            return
        super().shutdown_request(request)

    def server_close(self) -> None:
        """Close the listening socket and every connection still kept open."""
        self.lingering_closer.close()
        super().server_close()

    def server_bind(self) -> None:
        """Bind the socket and record the host verbatim as ``server_name``.

        Overrides ``HTTPServer.server_bind`` to skip ``socket.getfqdn(host)`` (see
        the module docstring): the plain ``TCPServer.server_bind`` binds the socket
        and ``server_name`` is set to the bind host directly, so the accept loop is
        reachable immediately instead of after a reverse-DNS stall.
        """
        socketserver.TCPServer.server_bind(self)
        # An AF_INET/AF_INET6 bind address is always ``(host: str, port: int, ...)``;
        # ``server_address`` is typed broadly, so narrow it for the informational
        # ``server_name``/``server_port`` fields the stdlib normally derives here.
        self.server_name = cast(str, self.server_address[0])
        self.server_port = self.server_address[1]
