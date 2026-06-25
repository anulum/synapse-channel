# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real localhost HTTP helpers for A2A bridge tests

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from typing import Any

from synapse_channel.a2a_server import A2ABridge, build_a2a_handler
from synapse_channel.a2a_store import A2ATaskStore


class RecordingAgent:
    """Minimal chat-capable agent adapter that records bridge submissions."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def chat(self, payload: str, *, target: str = "all") -> None:
        """Record one chat call made by the bridge."""
        self.messages.append((target, payload))


class SlowRecordingAgent(RecordingAgent):
    """Recording agent adapter that exposes overlapping chat submissions."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._active_chats = 0
        self.max_active_chats = 0

    async def chat(self, payload: str, *, target: str = "all") -> None:
        """Record chat calls and the maximum concurrent entry count."""
        with self._lock:
            self._active_chats += 1
            self.max_active_chats = max(self.max_active_chats, self._active_chats)
        try:
            time.sleep(0.05)
            await super().chat(payload, target=target)
        finally:
            with self._lock:
                self._active_chats -= 1


@dataclass
class BridgeRef:
    """Mutable bridge reference retained for existing route-test setup style."""

    bridge: A2ABridge


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _default_bridge() -> A2ABridge:
    return A2ABridge(
        agent=RecordingAgent(),
        agent_card={
            "name": "SYNAPSE CHANNEL",
            "description": "bridge",
            "supportedInterfaces": [
                {
                    "url": "https://example.test/a2a/v1",
                    "protocolBinding": "HTTP+JSON",
                    "protocolVersion": "1.0",
                }
            ],
            "version": "0.0",
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "extendedAgentCard": False,
            },
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "skills": [],
        },
        target="WORKER",
        store=A2ATaskStore(),
    )


class HandlerHarness:
    """Exercise an A2A request through a real localhost HTTP server."""

    def __init__(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.method = method
        self.path = path
        self.body = body
        self.headers = headers or {}
        self.handler = BridgeRef(_default_bridge())

    def run(self) -> tuple[int, dict[str, Any]]:
        """Run the configured request and return HTTP status plus decoded body."""
        status, _headers, body = self._request()
        return status, json.loads(body.decode("utf-8"))

    def run_sse(self) -> tuple[int, dict[str, Any]]:
        """Run the configured request and return status plus the first SSE data body."""
        status, headers, body = self._request()
        assert headers.get("Content-Type") == "text/event-stream"
        line = next(part for part in body.splitlines() if part.startswith(b"data: "))
        return status, json.loads(line.removeprefix(b"data: ").decode("utf-8"))

    def _request(self) -> tuple[int, dict[str, str], bytes]:
        port = _free_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), build_a2a_handler(self.handler.bridge))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            if self.body is None:
                payload = b""
            elif isinstance(self.body, bytes):
                payload = self.body
            else:
                payload = json.dumps(self.body).encode("utf-8")
            headers = {"Content-Length": str(len(payload)), **self.headers}
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
            try:
                conn.request(self.method, self.path, body=payload, headers=headers)
                response = conn.getresponse()
                body = response.read()
                parsed_headers = {key: value for key, value in response.getheaders()}
                return response.status, parsed_headers, body
            finally:
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
            with contextlib.suppress(RuntimeError):
                thread.join(timeout=2.0)
