# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — stdlib HTTP edge for the Agent2Agent bridge
"""Stdlib HTTP edge for the Agent2Agent bridge."""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time as time
from collections.abc import Iterable as Iterable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs as parse_qs
from urllib.parse import urlparse as urlparse

from synapse_channel import a2a_http_protocol as _protocol
from synapse_channel.a2a import JsonMap as JsonMap
from synapse_channel.a2a_http_handler import (
    DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS as DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS,
)
from synapse_channel.a2a_http_handler import (
    MAX_A2A_JSON_BODY_BYTES as MAX_A2A_JSON_BODY_BYTES,
)
from synapse_channel.a2a_http_routes import A2ARouteHandler
from synapse_channel.a2a_validation import SSE_MEDIA_TYPE as SSE_MEDIA_TYPE
from synapse_channel.a2a_validation import TERMINAL_TASK_STATES as TERMINAL_TASK_STATES
from synapse_channel.a2a_validation import (
    is_supported_json_media_type as is_supported_json_media_type,
)
from synapse_channel.core.error_boundaries import http_error_boundary as http_error_boundary
from synapse_channel.core.protocol import loads_bounded as loads_bounded
from synapse_channel.prompt_bind_http import PromptBindThreadingHTTPServer

if TYPE_CHECKING:
    from synapse_channel.a2a_server import A2ABridge

DEFAULT_MAX_CONCURRENT_A2A_REQUESTS = 32
"""Default concurrent in-flight HTTP requests admitted by the A2A edge."""

bearer_token_matches = _protocol.bearer_token_matches
non_negative_int = _protocol.non_negative_int
origin_allowed = _protocol.origin_allowed
parse_push_config_path = _protocol.parse_push_config_path
parse_push_delivery_path = _protocol.parse_push_delivery_path
problem_response = _protocol.problem_response


class A2AHTTPServer(PromptBindThreadingHTTPServer):
    """Threading A2A HTTP server with bounded concurrent admission.

    Each accepted connection must acquire one admission slot before a worker
    thread is started. When the ceiling is full the server writes a deterministic
    ``503`` problem response on the accepted socket and closes it without
    invoking the request handler. Every admitted path — normal completion, parse
    error, read timeout, client disconnect, or handler exception — releases the
    slot exactly once so capacity cannot leak.
    """

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int] | tuple[str, int, int, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_A2A_REQUESTS,
        request_read_timeout_seconds: float = DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS,
    ) -> None:
        if max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be >= 1")
        if request_read_timeout_seconds <= 0.0:
            raise ValueError("request_read_timeout_seconds must be > 0")
        self.max_concurrent_requests = int(max_concurrent_requests)
        self.request_read_timeout_seconds = float(request_read_timeout_seconds)
        self._admission = threading.BoundedSemaphore(self.max_concurrent_requests)
        super().__init__(server_address, RequestHandlerClass)

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: Any,
    ) -> None:
        """Admit one connection or refuse with ``503`` when capacity is full."""
        if not self._admission.acquire(blocking=False):
            self._send_capacity_refusal(request)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._admission.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        """Run one handler and always release the admission slot afterward."""
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self._admission.release()

    def finish_request(self, request: Any, client_address: Any) -> None:
        """Apply the read deadline to the accepted socket before handling."""
        sock = request[1] if isinstance(request, tuple) else request
        if isinstance(sock, socket.socket):
            sock.settimeout(self.request_read_timeout_seconds)
        super().finish_request(request, client_address)

    def _send_capacity_refusal(self, request: Any) -> None:
        """Write a deterministic capacity-exhausted response on ``request``."""
        body = problem_response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Service Unavailable",
            "A2A HTTP concurrent request capacity is exhausted",
            reason="A2A_HTTP_CAPACITY_EXHAUSTED",
        )
        raw = json.dumps(_protocol.to_wire_json(body), sort_keys=True).encode("utf-8")
        header = (
            f"HTTP/1.1 {int(HTTPStatus.SERVICE_UNAVAILABLE)} Service Unavailable\r\n"
            f"Content-Type: {_protocol.HTTP_JSON_MEDIA_TYPE}\r\n"
            f"Content-Length: {len(raw)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        sock = request[1] if isinstance(request, tuple) else request
        if not isinstance(sock, socket.socket):
            return
        try:
            sock.sendall(header + raw)
        except OSError:
            return


def build_a2a_handler(bridge: A2ABridge) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class bound to ``bridge``.

    Parameters
    ----------
    bridge : A2ABridge
        Bridge orchestrator used by the HTTP edge.

    Returns
    -------
    type[BaseHTTPRequestHandler]
        Configured stdlib HTTP handler class.

    Raises
    ------
    ValueError
        If the bridge has no trusted advertised endpoint authority.
    """
    if not bridge.allowed_authorities:
        raise ValueError("A2A HTTP requires at least one trusted endpoint authority")

    class A2ARequestHandler(A2ARouteHandler):
        """HTTP handler bound independently to one A2A bridge."""

    A2ARequestHandler.bridge = bridge
    return A2ARequestHandler


def wrap_a2a_server_socket(
    server: A2AHTTPServer,
    ssl_context: ssl.SSLContext | None,
) -> A2AHTTPServer:
    """Optionally wrap an A2A server socket for native TLS.

    Parameters
    ----------
    server :
        Bound A2A HTTP server (plaintext until wrapped).
    ssl_context :
        Server-side TLS context from :func:`build_server_ssl_context`, or
        ``None`` to leave the socket plaintext.

    Returns
    -------
    A2AHTTPServer
        The same server instance, with a TLS-wrapped socket when ``ssl_context``
        is provided.
    """
    if ssl_context is not None:
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
    return server


def make_a2a_http_server(
    *,
    bridge: A2ABridge,
    host: str,
    port: int,
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_A2A_REQUESTS,
    request_read_timeout_seconds: float = DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS,
    ssl_context: ssl.SSLContext | None = None,
) -> A2AHTTPServer:
    """Build a stdlib A2A HTTP(S) server for callers that manage its lifecycle.

    Parameters
    ----------
    bridge : A2ABridge
        Bridge orchestrator used by the handler.
    host : str
        Host interface to bind.
    port : int
        TCP port to bind.
    max_concurrent_requests : int, optional
        Hard ceiling on concurrent in-flight HTTP requests (default 32).
    request_read_timeout_seconds : float, optional
        Wall-clock budget for reading one request body (default 30s).
    ssl_context : ssl.SSLContext or None, optional
        When set, the listening socket is wrapped for native HTTPS after bind.

    Returns
    -------
    A2AHTTPServer
        Configured HTTP(S) server with bounded admission and read deadlines.
    """
    server = A2AHTTPServer(
        (host, port),
        build_a2a_handler(bridge),
        max_concurrent_requests=max_concurrent_requests,
        request_read_timeout_seconds=request_read_timeout_seconds,
    )
    return wrap_a2a_server_socket(server, ssl_context)


def serve_a2a_http(
    *,
    bridge: A2ABridge,
    host: str,
    port: int,
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_A2A_REQUESTS,
    request_read_timeout_seconds: float = DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS,
    ssl_context: ssl.SSLContext | None = None,
) -> (
    None
):  # pragma: no cover - blocking process wrapper; server factory is covered by real HTTP tests.
    """Run a blocking A2A HTTP or HTTPS server.

    Parameters
    ----------
    bridge : A2ABridge
        Bridge orchestrator used by the handler.
    host : str
        Host interface to bind.
    port : int
        TCP port to bind.
    max_concurrent_requests : int, optional
        Hard ceiling on concurrent in-flight HTTP requests (default 32).
    request_read_timeout_seconds : float, optional
        Wall-clock budget for reading one request body (default 30s).
    ssl_context : ssl.SSLContext or None, optional
        Native TLS context; when set the edge serves HTTPS.
    """
    server = make_a2a_http_server(
        bridge=bridge,
        host=host,
        port=port,
        max_concurrent_requests=max_concurrent_requests,
        request_read_timeout_seconds=request_read_timeout_seconds,
        ssl_context=ssl_context,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
