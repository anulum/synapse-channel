# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE GITHUB APP — real local GitHub REST boundary harness
"""Serve planned JSON responses over a real loopback HTTP socket."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass(frozen=True)
class ResponseSpec:
    """One HTTP response returned by the local API server."""

    status: int = 200
    body: object = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    add_content_length: bool = True


@dataclass(frozen=True)
class RecordedRequest:
    """One complete HTTP request observed by the local API server."""

    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes


class LocalGitHubApi:
    """Threaded loopback server with deterministic per-route response queues."""

    def __init__(self, plans: Mapping[tuple[str, str], list[ResponseSpec]]) -> None:
        """Copy response queues so each test owns its consumption state."""
        self._plans = {key: list(values) for key, values in plans.items()}
        self.requests: list[RecordedRequest] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """Return the live loopback origin."""
        if self._server is None:
            raise RuntimeError("server is not running")
        return f"http://127.0.0.1:{self._server.server_port}"

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length", "0"))
        body = handler.rfile.read(length)
        self.requests.append(
            RecordedRequest(
                method=handler.command,
                path=handler.path,
                headers={key: value for key, value in handler.headers.items()},
                body=body,
            )
        )
        queue = self._plans.get((handler.command, handler.path), [])
        spec = queue.pop(0) if queue else ResponseSpec(status=404, body={"message": "not found"})
        raw = spec.body if isinstance(spec.body, bytes) else json.dumps(spec.body).encode("utf-8")
        handler.send_response(spec.status)
        handler.send_header("Content-Type", "application/json")
        if spec.add_content_length and "Content-Length" not in spec.headers:
            handler.send_header("Content-Length", str(len(raw)))
        for key, value in spec.headers.items():
            handler.send_header(key, value)
        handler.end_headers()
        handler.wfile.write(raw)

    def start(self) -> None:
        """Bind an ephemeral loopback port and start serving."""
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer._handle(self)

            def do_POST(self) -> None:
                outer._handle(self)

            def log_message(self, format: str, *args: object) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the server and join its thread."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)


@contextmanager
def serve_api(
    plans: Mapping[tuple[str, str], list[ResponseSpec]],
) -> Iterator[LocalGitHubApi]:
    """Yield a running real HTTP API and always clean it up."""
    server = LocalGitHubApi(plans)
    server.start()
    try:
        yield server
    finally:
        server.stop()
