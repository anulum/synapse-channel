# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real HTTP admission and request-body failure journeys
from __future__ import annotations

import io
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.a2a_http import MAX_A2A_JSON_BODY_BYTES, build_a2a_handler
from synapse_channel.a2a_http_routes import A2ARouteHandler
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.prompt_bind_http import PromptBindThreadingHTTPServer


class _ConfiguredServer(PromptBindThreadingHTTPServer):
    """Embed the handler with the public server read-budget setting.

    The base is the product's shared server class without the A2A server's
    own validation of the budget, so that an invalid budget can be set here
    and a connection is still ended in order after its answer.
    """

    request_read_timeout_seconds: object = 30.0
    daemon_threads = True


class _FailedFileBodyHandler(A2ARouteHandler):
    """Read the admitted body from a real failed file descriptor."""

    body_file: Path

    def do_POST(self) -> None:
        """Keep TCP response delivery while exercising a failed body stream."""
        descriptor = os.open(self.body_file, os.O_RDONLY)
        stream = io.BufferedReader(io.FileIO(descriptor, closefd=False))
        os.close(descriptor)
        original = self.rfile
        self.rfile = stream
        try:
            super().do_POST()
        finally:
            self.rfile = original
            stream.close()


class _SpooledBodyHandler(A2ARouteHandler):
    """Embed a completed body spool while retaining real TCP response delivery."""

    body_file: Path
    stream_type: type[io.BufferedReader] = io.BufferedReader

    def do_POST(self) -> None:
        """Read an actual body spool without applying socket APIs to its file."""
        connection, original = self.connection, self.rfile
        with self.stream_type(io.FileIO(self.body_file, "r")) as stream:
            self.connection = stream
            self.rfile = stream
            try:
                super().do_POST()
            finally:
                self.connection, self.rfile = connection, original


class _SlowPartialFile(io.BufferedReader):
    """Read an actual single spool byte after a bounded I/O delay."""

    def read(self, size: int | None = -1) -> bytes:
        """Retain short-read semantics while exceeding the configured body budget."""
        chunk = super().read(1)
        time.sleep(0.05)
        return chunk


class _DelayedBodyHandler(_SpooledBodyHandler):
    """Embed the actual delayed partial spool for deadline observation."""

    stream_type = _SlowPartialFile


@contextmanager
def edge(
    *, handler: type[A2ARouteHandler] | None = None
) -> Iterator[tuple[A2ABridge, _ConfiguredServer]]:
    """Serve one concrete bridge and join every owning HTTP worker on exit."""
    agent = SynapseAgent("HTTPREAD/owner", machine_identity=False)
    bridge = A2ABridge(
        agent=agent,
        agent_card={"name": "local edge"},
        target="HTTPREAD/worker",
        allowed_authorities=("127.0.0.1",),
        task_timeout_seconds=0.0,
    )
    kind = handler or build_a2a_handler(bridge)
    if handler is not None:
        handler.bridge = bridge
    server = _ConfiguredServer(("127.0.0.1", 0), kind)
    bridge.allowed_authorities = (f"127.0.0.1:{server.server_port}",)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield bridge, server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2.0)
        assert not worker.is_alive()


def request(
    server: _ConfiguredServer,
    method: str,
    path: str,
    *,
    body: bytes = b"{}",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Send one real HTTP request and decode its complete JSON response."""
    client = HTTPConnection("127.0.0.1", server.server_port, timeout=3.0)
    try:
        client.request(
            method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})}
        )
        response = client.getresponse()
        data = json.loads(response.read())
        assert isinstance(data, dict)
        return response.status, data
    finally:
        client.close()


@pytest.mark.parametrize("budget", ["not-a-number", None, 0.0])
def test_embedding_server_invalid_read_budget_uses_existing_default(budget: object) -> None:
    """An invalid embedding budget still permits bounded body validation."""
    with edge() as (_, server):
        server.request_read_timeout_seconds = budget
        status, body = request(server, "POST", "/message:send")
        assert status == 400 and body["title"] == "Invalid A2A message"


def test_elapsed_body_deadline_refuses_before_dispatch(tmp_path: Path) -> None:
    """Actual partial I/O expires the budget even with coarse platform clocks."""
    path = tmp_path / "delayed.json"
    path.write_bytes(b"{}")
    _DelayedBodyHandler.body_file = path
    with edge(handler=_DelayedBodyHandler) as (bridge, server):
        server.request_read_timeout_seconds = 0.005
        status, body = request(server, "POST", "/message:send")
        assert status == 408
        assert body["error"]["details"][0]["reason"] == "A2A_HTTP_READ_TIMEOUT"
        assert bridge.store.list_tasks() == []


def test_real_file_descriptor_read_failure_is_a_caller_safe_timeout(tmp_path: Path) -> None:
    """An actual descriptor failure produces the authored safe HTTP refusal."""
    path = tmp_path / "body.json"
    path.write_bytes(b"{}")
    _FailedFileBodyHandler.body_file = path
    with edge(handler=_FailedFileBodyHandler) as (_, server):
        status, body = request(server, "POST", "/message:send")
        assert status == 408 and body["detail"] == "Request body read failed"
        assert "descriptor" not in json.dumps(body).lower()


def test_declared_oversized_body_is_refused_without_waiting_for_its_bytes() -> None:
    """The length cap refuses an oversized declaration before reading bytes."""
    with edge() as (bridge, server):
        status, _ = request(
            server,
            "POST",
            "/message:send",
            body=b"",
            headers={"Content-Length": str(MAX_A2A_JSON_BODY_BYTES + 1)},
        )
        assert status == 413 and bridge.store.list_tasks() == []


def test_peer_body_eof_is_refused_as_incomplete_without_creating_a_task() -> None:
    """An actual peer half-close cannot dispatch an incomplete body."""
    with edge() as (bridge, server):
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=3.0) as client:
            client.sendall(
                (
                    "POST /message:send HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{server.server_port}\r\n"
                    "Content-Type: application/json\r\n"
                    "Content-Length: 100\r\nConnection: close\r\n\r\n{}"
                ).encode()
            )
            client.shutdown(socket.SHUT_WR)
            chunks = []
            while chunk := client.recv(8192):
                chunks.append(chunk)
        response = b"".join(chunks)
        assert b"408" in response and b"Request body incomplete" in response
        assert bridge.store.list_tasks() == []


def test_completed_body_spool_is_validated_without_socket_timeout(tmp_path: Path) -> None:
    """A real file-backed embedding validates a completed spool over actual HTTP."""
    path = tmp_path / "body.json"
    path.write_bytes(b"{}")
    _SpooledBodyHandler.body_file = path
    with edge(handler=_SpooledBodyHandler) as (bridge, server):
        status, body = request(server, "POST", "/message:send")
        assert status == 400 and body["title"] == "Invalid A2A message"
        assert bridge.store.list_tasks() == []
