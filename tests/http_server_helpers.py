# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real localhost HTTP helpers for network-client tests

from __future__ import annotations

import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from hub_e2e_helpers import _free_port


@dataclass(frozen=True)
class RecordedHttpRequest:
    """One request received by the local test HTTP server."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes


class LocalHttpResponder:
    """Serve one configured response over a real localhost HTTP socket."""

    def __init__(
        self,
        *,
        body: bytes,
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self.body = body
        self.status = status
        self.content_type = content_type
        self.requests: list[RecordedHttpRequest] = []
        self.port = _free_port()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner._handle(self)

            def do_POST(self) -> None:
                owner._handle(self)

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        """Return the base URL for this local server."""
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> LocalHttpResponder:
        """Start serving requests."""
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Stop the server and close its socket."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length") or "0")
        body = handler.rfile.read(length) if length else b""
        self.requests.append(
            RecordedHttpRequest(
                method=handler.command,
                path=handler.path,
                headers={str(key): str(value) for key, value in handler.headers.items()},
                body=body,
            )
        )
        handler.send_response(self.status)
        handler.send_header("Content-Type", self.content_type)
        handler.send_header("Content-Length", str(len(self.body)))
        handler.end_headers()
        handler.wfile.write(self.body)
