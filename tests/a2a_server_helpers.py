# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

import json
import threading
import time
from io import BytesIO
from typing import Any

from synapse_channel.a2a_server import A2ABridge, build_a2a_handler
from synapse_channel.a2a_store import A2ATaskStore


class FakeAgent:
    """Small async agent stub for A2A bridge tests."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def chat(self, payload: str, *, target: str = "all") -> None:
        """Record one chat call."""
        self.messages.append((target, payload))


class SlowAgent(FakeAgent):
    """Agent stub that records overlapping chat calls."""

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


class HandlerHarness:
    """Instantiate one stdlib request handler without binding a socket."""

    def __init__(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        bridge = A2ABridge(
            agent=FakeAgent(),
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
        handler_type = build_a2a_handler(bridge)
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        self.handler: Any = handler_type.__new__(handler_type)
        self.handler.command = method
        self.handler.path = path
        self.handler.request_version = "HTTP/1.1"
        self.handler.requestline = f"{method} {path} HTTP/1.1"
        handler: Any = self.handler
        handler.headers = {"Content-Length": str(len(payload)), **(headers or {})}
        self.handler.rfile = BytesIO(payload)
        self.handler.wfile = BytesIO()
        self.handler.close_connection = False
        self.handler.request = object()
        self.handler.client_address = ("127.0.0.1", 1)
        handler.server = object()
        self.handler.responses = type(self.handler).responses

    def run(self) -> tuple[int, dict[str, Any]]:
        """Run the handler method and return HTTP status plus decoded body."""
        if self.handler.command == "GET":
            self.handler.do_GET()
        elif self.handler.command == "POST":
            self.handler.do_POST()
        elif self.handler.command == "DELETE":
            self.handler.do_DELETE()
        else:
            raise AssertionError(self.handler.command)
        raw = self.handler.wfile.getvalue()
        header_blob, body = raw.split(b"\r\n\r\n", 1)
        status = int(header_blob.split(b" ", 2)[1])
        return status, json.loads(body.decode("utf-8"))

    def run_sse(self) -> tuple[int, dict[str, Any]]:
        """Run the POST handler and return HTTP status plus the first SSE data body."""
        if self.handler.command != "POST":
            raise AssertionError(self.handler.command)
        self.handler.do_POST()
        raw = self.handler.wfile.getvalue()
        header_blob, body = raw.split(b"\r\n\r\n", 1)
        status = int(header_blob.split(b" ", 2)[1])
        assert b"Content-Type: text/event-stream" in header_blob
        line = next(part for part in body.splitlines() if part.startswith(b"data: "))
        return status, json.loads(line.removeprefix(b"data: ").decode("utf-8"))
