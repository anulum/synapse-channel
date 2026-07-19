# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real-socket bounds for A2A HTTP concurrent admission and body reads

"""Real-socket coverage for A2A HTTP concurrent admission and read deadlines."""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from http import HTTPStatus
from typing import Any

import pytest

from a2a_server_helpers import RecordingAgent
from synapse_channel.a2a_http import (
    DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS,
    DEFAULT_MAX_CONCURRENT_A2A_REQUESTS,
    A2AHTTPServer,
    make_a2a_http_server,
)
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _agent_card() -> dict[str, Any]:
    return {
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
    }


def _bridge(agent: Any | None = None) -> A2ABridge:
    return A2ABridge(
        agent=agent or RecordingAgent(),
        agent_card=_agent_card(),
        target="WORKER",
        store=A2ATaskStore(),
    )


@contextlib.contextmanager
def _running_server(
    bridge: A2ABridge,
    *,
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_A2A_REQUESTS,
    request_read_timeout_seconds: float = DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS,
) -> Any:
    port = _free_port()
    server = make_a2a_http_server(
        bridge=bridge,
        host="127.0.0.1",
        port=port,
        max_concurrent_requests=max_concurrent_requests,
        request_read_timeout_seconds=request_read_timeout_seconds,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, port
    finally:
        server.shutdown()
        server.server_close()
        with contextlib.suppress(RuntimeError):
            thread.join(timeout=2.0)


def _get_agent_card(port: int, *, timeout: float = 2.0) -> tuple[int, dict[str, Any]]:
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(
            b"GET /.well-known/agent-card.json HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        raw = _recv_http(sock, timeout=timeout)
    finally:
        sock.close()
    return _parse_http_response(raw)


def _recv_http(sock: socket.socket, *, timeout: float) -> bytes:
    sock.settimeout(timeout)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            part = sock.recv(65536)
        except TimeoutError:
            break
        if not part:
            break
        chunks.append(part)
        if b"\r\n\r\n" in b"".join(chunks):
            # Prefer full body when Content-Length is known; otherwise stop at headers.
            joined = b"".join(chunks)
            header, _, body = joined.partition(b"\r\n\r\n")
            length = None
            for line in header.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    try:
                        length = int(line.split(b":", 1)[1].strip())
                    except ValueError:
                        length = None
                    break
            if length is None or len(body) >= length:
                break
    return b"".join(chunks)


def _parse_http_response(raw: bytes) -> tuple[int, dict[str, Any]]:
    header, _, body = raw.partition(b"\r\n\r\n")
    status_line = header.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    status = int(status_line.split(" ", 2)[1])
    if not body:
        return status, {}
    return status, json.loads(body.decode("utf-8"))


class _HoldOpenAgent(RecordingAgent):
    """Recording agent that blocks chat until an event is signalled."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    async def chat(
        self,
        payload: str,
        *,
        target: str = "all",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.entered.set()
        assert self.release.wait(timeout=5.0), "hold-open agent was not released"
        await super().chat(payload, target=target, metadata=metadata)


def _post_message_send(port: int, *, timeout: float = 2.0) -> tuple[int, dict[str, Any]]:
    body = json.dumps(
        {
            "message": {
                "messageId": "msg-bounds-1",
                "role": "ROLE_USER",
                "parts": [{"text": "hello-bounds"}],
            }
        }
    ).encode("utf-8")
    request = (
        b"POST /message:send HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"Connection: close\r\n"
        + b"\r\n"
        + body
    )
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(request)
        raw = _recv_http(sock, timeout=timeout)
    finally:
        sock.close()
    return _parse_http_response(raw)


def test_make_a2a_http_server_rejects_invalid_bounds() -> None:
    bridge = _bridge()
    with pytest.raises(ValueError, match="max_concurrent_requests"):
        make_a2a_http_server(bridge=bridge, host="127.0.0.1", port=0, max_concurrent_requests=0)
    with pytest.raises(ValueError, match="request_read_timeout_seconds"):
        make_a2a_http_server(
            bridge=bridge,
            host="127.0.0.1",
            port=0,
            request_read_timeout_seconds=0.0,
        )


def test_concurrent_capacity_refuses_with_503_and_releases() -> None:
    """A second connection is refused while the first occupies the only slot."""
    agent = _HoldOpenAgent()
    bridge = _bridge(agent)
    with _running_server(
        bridge,
        max_concurrent_requests=1,
        request_read_timeout_seconds=5.0,
    ) as (_server, port):
        holder_status: dict[str, Any] = {}
        holder_error: list[BaseException] = []

        def hold_request() -> None:
            try:
                status, body = _post_message_send(port, timeout=5.0)
                holder_status["status"] = status
                holder_status["body"] = body
            except BaseException as exc:  # pragma: no cover - diagnostic
                holder_error.append(exc)

        holder = threading.Thread(target=hold_request, daemon=True)
        holder.start()
        assert agent.entered.wait(timeout=2.0), "first request never entered the agent"

        refused_status, refused_body = _get_agent_card(port, timeout=2.0)
        assert refused_status == int(HTTPStatus.SERVICE_UNAVAILABLE)
        assert refused_body.get("title") == "Service Unavailable"
        error = refused_body.get("error") or {}
        details = error.get("details") or []
        reasons = [item.get("reason") for item in details if isinstance(item, dict)]
        assert "A2A_HTTP_CAPACITY_EXHAUSTED" in reasons

        agent.release.set()
        holder.join(timeout=5.0)
        assert not holder_error
        assert holder_status.get("status") == int(HTTPStatus.OK)

        # Capacity must be free after the holder finishes.
        free_status, free_body = _get_agent_card(port, timeout=2.0)
        assert free_status == int(HTTPStatus.OK)
        assert free_body.get("name") == "SYNAPSE CHANNEL"


def test_incomplete_slow_body_times_out_without_long_sleep() -> None:
    """A declared Content-Length that never finishes is refused with 408 promptly."""
    bridge = _bridge()
    with _running_server(
        bridge,
        max_concurrent_requests=4,
        request_read_timeout_seconds=0.35,
    ) as (_server, port):
        sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        try:
            # Announce 64 body bytes but send only a tiny prefix, then stall.
            headers = (
                b"POST /message:send HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 64\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b'{"'
            )
            started = time.monotonic()
            sock.sendall(headers)
            raw = _recv_http(sock, timeout=2.0)
            elapsed = time.monotonic() - started
        finally:
            sock.close()

        status, body = _parse_http_response(raw)
        assert status == int(HTTPStatus.REQUEST_TIMEOUT)
        assert body.get("title") == "Request Timeout"
        error = body.get("error") or {}
        details = error.get("details") or []
        reasons = [item.get("reason") for item in details if isinstance(item, dict)]
        assert "A2A_HTTP_READ_TIMEOUT" in reasons
        # Bound must fire near the configured deadline, not a multi-second hang.
        assert elapsed < 1.5


def test_a2a_http_server_exposes_configured_limits() -> None:
    bridge = _bridge()
    server = make_a2a_http_server(
        bridge=bridge,
        host="127.0.0.1",
        port=0,
        max_concurrent_requests=7,
        request_read_timeout_seconds=1.25,
    )
    try:
        assert isinstance(server, A2AHTTPServer)
        assert server.max_concurrent_requests == 7
        assert server.request_read_timeout_seconds == pytest.approx(1.25)
    finally:
        server.server_close()
